// chat.js — reusable chat-pane factory.
//
// createChat(config) renders a complete chat surface into a host-provided
// container and drives it over the shared wire protocol. It owns ALL chat DOM
// (the host only supplies an empty mount node) and keeps every bit of state in
// the factory closure — no globals — so a host can mount more than one instance
// (Phase 2: the portal scratchpad as a second instance sharing one connection).
//
// Portal-only concerns are reached only through injected hooks, never named
// directly: getSessionId(), hooks.onUnhandledFrame(), hooks.onTurnFinal(),
// hooks.onSocketOpen(). Local passes none of them.
//
// Depends on globals `marked` and `hljs` (loaded via <script> in the host).
// Canonical home: src/local_ui/static/. See the design doc:
// docs/superpowers/specs/2026-06-12-local-ui-shared-chat-module-design.md

// === Attachment limits (mirror the portal + server allowlist) ===
const MAX_IMAGE = 5 * 1024 * 1024;
const MAX_PDF = 10 * 1024 * 1024;
const MAX_DOCX = 10 * 1024 * 1024;
const MAX_TEXT = 256 * 1024;
const MAX_TOTAL = 20 * 1024 * 1024;
const IMAGE_MIMES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const TEXT_EXTS = [".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".log", ".xml", ".toml", ".ini"];

// Fixed system commands shown under the skills picker's SETTINGS footer.
const SKILLS_CONFIG = [
  { icon: "🪪", label: "Identity", command: "/identity" },
  { icon: "👤", label: "Profile", command: "/profile" },
  { icon: "⚙️", label: "Preferences", command: "/preferences" },
  { icon: "❓", label: "Help", command: "/help" },
];

const TOOL_GERUNDS = {
  Read: "Reading", Write: "Writing", Edit: "Editing",
  Grep: "Searching", Glob: "Searching", Bash: "Running",
  LoadSkill: "Loading skill", WebFetch: "Fetching", Delegate: "Delegating",
};

const DOC_ICON =
  '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
  'stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 ' +
  '2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
const DL_ICON =
  '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
  'stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
  '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" ' +
  'y2="3"/></svg>';

export function createChat(config) {
  const {
    container,
    connection,
    getSessionId = () => "local",
    features = {},
    hooks = {},
  } = config;
  const feat = {
    attachments: true, skills: true, theme: true, emptyState: true, ...features,
  };

  // marked is shared/global; configuring it is idempotent.
  if (window.marked) {
    window.marked.setOptions({
      highlight: (code, lang) =>
        window.hljs ? window.hljs.highlightAuto(code, lang ? [lang] : undefined).value : code,
      breaks: true,
    });
  }
  const md = (text) => (window.marked ? window.marked.parse(text) : text);

  // === Per-instance state ===
  let agentOnline = false;
  let staged = [];          // {filename, mime_type, data(base64), size}
  let inProgressMsg = null; // live assistant DOM node
  let historyAssistant = null;
  let skillsCache = null;
  let dragDepth = 0;

  // === DOM ===
  container.classList.add("cu-chat");
  container.innerHTML = `
    <div class="cu-toolbar">
      <div class="cu-status"><span class="cu-dot offline"></span><span class="cu-status-text">offline</span></div>
      <div class="cu-toolbar-right">
        ${feat.skills ? '<button class="cu-header-btn cu-skills-btn" title="Browse skills"><span class="glyph">✦</span>Skills</button>' : ""}
        ${feat.theme ? '<button class="cu-header-btn cu-theme" title="Toggle theme" aria-label="Toggle theme"></button>' : ""}
      </div>
    </div>
    <div class="cu-messages"></div>
    <div class="cu-footer">
      ${feat.attachments ? '<input type="file" class="cu-file-input" multiple style="display:none" accept="image/png,image/jpeg,image/gif,image/webp,application/pdf,' + DOCX_MIME + ',.docx,text/plain,text/markdown,text/csv,application/json,.md,.txt,.csv,.json,.yaml,.yml,.log,.xml,.toml,.ini">' : ""}
      <div class="cu-staged"></div>
      <div class="cu-composer">
        <button class="cu-collapse" type="button" title="Collapse input" aria-label="Collapse input">⌄</button>
        <textarea class="cu-input" rows="1" placeholder="What would you like to do?"></textarea>
        <div class="cu-composer-row">
          ${feat.attachments ? '<button class="cu-attach" title="Attach files"><span class="glyph">+</span>Attach</button>' : ""}
          <span class="cu-hint">Enter to send · Shift+Enter for newline${feat.attachments ? " · drop or paste files to attach" : ""}</span>
          <button class="cu-send" data-mode="send" title="Send" aria-label="Send">↑</button>
        </div>
        ${feat.attachments ? '<div class="cu-drop-hint">Drop files to attach</div>' : ""}
      </div>
    </div>
    <div class="cu-offline-overlay" hidden>
      <div class="cu-offline-card" role="alertdialog" aria-live="polite" aria-label="Agent offline">
        <div class="cu-offline-spinner"></div>
        <div class="cu-offline-title">Connecting to your agent…</div>
        <div class="cu-offline-sub">This screen will clear automatically once it connects.</div>
      </div>
    </div>`;

  const $ = (sel) => container.querySelector(sel);
  const messagesEl = $(".cu-messages");
  const inputEl = $(".cu-input");
  const sendBtn = $(".cu-send");
  const composerEl = $(".cu-composer");
  const collapseHandle = $(".cu-collapse");
  const statusDot = $(".cu-status .cu-dot");
  const statusText = $(".cu-status-text");
  const stagedEl = $(".cu-staged");
  const fileInput = feat.attachments ? $(".cu-file-input") : null;
  const attachBtn = feat.attachments ? $(".cu-attach") : null;
  const dropHint = feat.attachments ? $(".cu-drop-hint") : null;
  const skillsBtn = feat.skills ? $(".cu-skills-btn") : null;
  const themeBtn = feat.theme ? $(".cu-theme") : null;
  const offlineOverlay = $(".cu-offline-overlay");
  const offlineTitle = $(".cu-offline-title");
  const offlineSub = $(".cu-offline-sub");

  // Global singletons (one per page is fine; created on first instance).
  const skillsOverlay = feat.skills ? ensureSkillsOverlay() : null;
  ensurePrintDoc();

  // === Theme ===
  const hljsLight = document.getElementById("cu-hljs-light");
  const hljsDark = document.getElementById("cu-hljs-dark");
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    if (hljsLight) hljsLight.disabled = theme !== "light";
    if (hljsDark) hljsDark.disabled = theme !== "dark";
    if (themeBtn) {
      themeBtn.textContent = theme === "light" ? "☾" : "☀";
      themeBtn.title = theme === "light" ? "Switch to dark mode" : "Switch to light mode";
    }
  }
  function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    const next = current === "light" ? "dark" : "light";
    localStorage.setItem("curunir-theme", next);
    applyTheme(next);
  }
  if (feat.theme) {
    applyTheme(localStorage.getItem("curunir-theme") || document.documentElement.getAttribute("data-theme") || "light");
    themeBtn.addEventListener("click", toggleTheme);
  }

  // === Status / offline ===
  function setStatus(state) {
    statusDot.className = "cu-dot " + state;
    statusText.textContent = state;
    agentOnline = state === "online";
    offlineOverlay.hidden = state === "online";
    if (state === "reconnecting") {
      offlineTitle.textContent = "Connecting to your agent…";
      offlineSub.textContent = "This screen will clear automatically once it connects.";
    } else if (state === "offline") {
      offlineTitle.textContent = "Agent offline";
      offlineSub.textContent = "Your agent isn't connected right now. This screen will clear automatically once it reconnects.";
    }
    if (!agentOnline && inProgressMsg) {
      finalizeActivity(inProgressMsg);
      inProgressMsg.classList.remove("stopping");
      inProgressMsg = null;
    }
    updateComposerMode();
  }

  function updateComposerMode() {
    const responding = inProgressMsg !== null;
    if (responding && agentOnline) {
      sendBtn.dataset.mode = "stop";
      sendBtn.textContent = "■"; sendBtn.title = "Stop";
      sendBtn.setAttribute("aria-label", "Stop"); sendBtn.disabled = false;
    } else {
      sendBtn.dataset.mode = "send";
      sendBtn.textContent = "↑"; sendBtn.title = "Send";
      sendBtn.setAttribute("aria-label", "Send"); sendBtn.disabled = !agentOnline;
    }
  }

  // === Bootstrap ===
  function bootstrap() {
    connection.send({ content: "", command: "history_request", session_id: getSessionId() });
    if (feat.skills) connection.send({ command: "skills_request", session_id: getSessionId() });
    if (hooks.onSocketOpen) hooks.onSocketOpen();
  }

  // === Inbound frame routing ===
  function handleFrame(msg) {
    if (msg.type === "agent_status") { setStatus(msg.status); return; }
    if (msg.session_id && msg.session_id !== getSessionId()) {
      if (hooks.onUnhandledFrame) hooks.onUnhandledFrame(msg);
      return;
    }
    if (msg.type === "history_snapshot") { renderHistorySnapshot(msg.messages || []); return; }
    if (msg.type === "skills_snapshot") { handleSkillsSnapshot(msg.skills || []); return; }
    if (msg.type) { if (hooks.onUnhandledFrame) hooks.onUnhandledFrame(msg); return; }
    renderAgentChunk(msg);
  }

  function renderHistorySnapshot(messages) {
    messagesEl.innerHTML = "";
    inProgressMsg = null;
    historyAssistant = null;
    for (const m of messages) renderHistoryEntry(m);
    if (!messages.length && feat.emptyState) renderEmptyState();
    for (const el of messagesEl.querySelectorAll(".msg.assistant")) appendResponseActions(el);
    updateComposerMode();
  }

  function handleSkillsSnapshot(skills) {
    skillsCache = skills;
    if (skillsOverlay && !skillsOverlay.overlay.hidden) {
      renderSkillsList(skillsCache, skillsOverlay.search.value);
    }
    if (feat.emptyState && messagesEl.querySelector(".empty-state")) renderEmptyState();
  }

  // === Message rendering ===
  function setUserBody(bodyEl, content) {
    const text = content || "";
    const leading = text.length - text.trimStart().length;
    const match = text.slice(leading).match(/^\/[A-Za-z][\w-]*/);
    if (match) {
      bodyEl.textContent = "";
      const code = document.createElement("code");
      code.className = "slash-cmd";
      code.textContent = match[0];
      bodyEl.appendChild(code);
      const remainder = text.slice(leading + match[0].length);
      if (remainder) bodyEl.appendChild(document.createTextNode(remainder));
    } else {
      bodyEl.textContent = text;
    }
  }

  function renderHistoryEntry(m) {
    if (m.role === "user") {
      historyAssistant = null;
      const el = appendMessage("user");
      setUserBody(el.querySelector(".body"), m.content || "");
      renderAttachments(el, m.attachments);
    } else if (m.role === "assistant") {
      const el = historyAssistant || (historyAssistant = appendMessage("assistant"));
      const body = el.querySelector(".body");
      if (m.content) {
        body.dataset.raw = body.dataset.raw ? body.dataset.raw + "\n\n" + m.content : m.content;
        body.innerHTML = md(body.dataset.raw);
      }
      appendToolCalls(el, m.tool_calls);
      renderAttachments(el, m.attachments);
    } else if (m.role === "system") {
      historyAssistant = null;
      const el = appendMessage("assistant");
      el.querySelector(".body").innerHTML = `<em class="system-msg">${escapeHtml(m.content)}</em>`;
    }
  }

  function ensureActivityIndicator(msgEl, live) {
    let tools = msgEl.querySelector("details.tools");
    if (tools) return tools;
    tools = document.createElement("details");
    tools.className = live ? "tools live" : "tools";
    tools.innerHTML = live
      ? '<summary><div class="activity-row"><span class="pill"><span class="pill-dot"></span>' +
        '<span class="pill-label">Thinking</span></span><span class="tool-current"></span>' +
        '<span class="ticker-caret">▶</span></div></summary><div class="tool-list"></div>'
      : '<summary><div class="ticker"><span class="ticker-dot"></span><span class="ticker-count"></span>' +
        '<span class="ticker-caret">▶</span></div></summary><div class="tool-list"></div>';
    msgEl.appendChild(tools);
    return tools;
  }

  function appendToolCalls(msgEl, calls) {
    if (!calls || !calls.length) return;
    const tools = ensureActivityIndicator(msgEl, false);
    const list = tools.querySelector(".tool-list");
    for (const t of calls) {
      const line = document.createElement("div");
      line.className = "tool-line";
      line.textContent = t;
      list.appendChild(line);
    }
    if (tools.classList.contains("live")) {
      tools.classList.add("has-tools");
    } else {
      const total = list.children.length;
      tools.querySelector(".ticker-count").textContent = total === 1 ? "1 tool" : `${total} tools`;
    }
  }

  function setCurrentTool(msgEl, summary) {
    const cur = msgEl.querySelector("details.tools.live .tool-current");
    if (cur) cur.textContent = toStatusLabel(summary);
  }

  function finalizeActivity(msgEl) {
    if (!msgEl) return;
    const tools = msgEl.querySelector("details.tools.live");
    if (!tools) return;
    const list = tools.querySelector(".tool-list");
    const total = list ? list.children.length : 0;
    if (total === 0) { tools.remove(); return; }
    tools.classList.remove("live", "has-tools");
    tools.querySelector("summary").innerHTML =
      '<div class="ticker"><span class="ticker-dot"></span><span class="ticker-count">' +
      (total === 1 ? "1 tool" : total + " tools") + '</span><span class="ticker-caret">▶</span></div>';
  }

  function toStatusLabel(summary) {
    const sp = summary.indexOf(" ");
    const verb = sp === -1 ? summary : summary.slice(0, sp);
    const g = TOOL_GERUNDS[verb];
    return (g ? g + summary.slice(sp) : summary) + "…";
  }

  function appendResponseActions(msgEl) {
    if (!msgEl || !msgEl.classList.contains("assistant")) return;
    const body = msgEl.querySelector(".body");
    if (!body || !body.dataset.raw) return;
    const existing = msgEl.querySelector(".response-actions");
    if (existing) existing.remove();
    const wrap = document.createElement("div");
    wrap.className = "response-actions";
    const copyBtn = document.createElement("button");
    copyBtn.type = "button"; copyBtn.className = "resp-action"; copyBtn.textContent = "Copy";
    copyBtn.setAttribute("aria-label", "Copy response");
    copyBtn.addEventListener("click", () => copyResponse(body.dataset.raw, copyBtn));
    const printBtn = document.createElement("button");
    printBtn.type = "button"; printBtn.className = "resp-action"; printBtn.textContent = "Print";
    printBtn.setAttribute("aria-label", "Print response");
    printBtn.addEventListener("click", () => printResponse(body));
    wrap.appendChild(copyBtn); wrap.appendChild(printBtn);
    msgEl.appendChild(wrap);
  }

  async function copyResponse(rawText, btn) {
    const original = btn.textContent;
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(rawText); ok = true;
      } else {
        const ta = document.createElement("textarea");
        ta.value = rawText; ta.setAttribute("readonly", "");
        ta.style.position = "fixed"; ta.style.top = "-1000px"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select();
        try { ok = document.execCommand("copy"); } catch {}
        document.body.removeChild(ta);
      }
    } catch {}
    btn.textContent = ok ? "Copied" : "Copy failed";
    setTimeout(() => { btn.textContent = original; }, 1500);
  }

  function printResponse(bodyEl) {
    const printDoc = document.getElementById("cu-print-doc");
    if (!printDoc || !bodyEl) return;
    printDoc.innerHTML = bodyEl.innerHTML;
    const prevTitle = document.title;
    document.title = "Curunir response";
    window.addEventListener("afterprint", () => {
      document.title = prevTitle; printDoc.innerHTML = "";
    }, { once: true });
    window.print();
  }

  function renderAgentChunk(m) {
    if (!inProgressMsg) inProgressMsg = appendMessage("assistant", true);
    const body = inProgressMsg.querySelector(".body");
    if (m.delta) {
      body.dataset.raw = (body.dataset.raw || "") + (m.content || "");
      body.innerHTML = md(body.dataset.raw);
    } else if (m.content) {
      body.dataset.raw = m.content;
      body.innerHTML = md(m.content);
    }
    appendToolCalls(inProgressMsg, m.tool_calls);
    renderAttachments(inProgressMsg, m.attachments);
    if (m.tool_calls && m.tool_calls.length) {
      setCurrentTool(inProgressMsg, m.tool_calls[m.tool_calls.length - 1]);
    }
    if (m.final) {
      finalizeActivity(inProgressMsg);
      inProgressMsg.classList.remove("stopping");
      appendResponseActions(inProgressMsg);
      inProgressMsg = null;
      if (hooks.onTurnFinal) hooks.onTurnFinal();
    }
    updateComposerMode();
    scrollToBottom();
  }

  function renderAttachments(parent, atts) {
    if (!atts || !atts.length) return;
    const wrap = document.createElement("div");
    wrap.className = "attachments";
    for (const a of atts) {
      const name = a.filename || a.path || "file";
      const hasData = typeof a.data === "string";
      if (hasData && typeof a.mime_type === "string" && a.mime_type.startsWith("image/")) {
        const img = document.createElement("img");
        img.className = "attachment-image";
        img.src = "data:" + a.mime_type + ";base64," + a.data;
        img.alt = name; img.title = name + " · click to download";
        img.onclick = () => downloadAttachment(a);
        wrap.appendChild(img);
        continue;
      }
      const ext = name.includes(".") ? name.split(".").pop().toUpperCase() : "";
      const downloadable = hasData;
      const card = document.createElement("span");
      card.className = "attachment-card";
      card.innerHTML =
        '<span class="badge">' + DOC_ICON + '</span><span class="meta"><span class="fname"></span>' +
        '<span class="fsub"></span></span>' + (downloadable ? '<span class="dl">' + DL_ICON + '</span>' : '');
      card.querySelector(".fname").textContent = name;
      card.querySelector(".fsub").textContent = downloadable
        ? (ext ? ext + " · click to download" : "Click to download")
        : (ext ? ext + " file" : "Attachment");
      if (downloadable) {
        card.classList.add("downloadable");
        card.title = "Download";
        card.onclick = () => downloadAttachment(a);
      }
      wrap.appendChild(card);
    }
    if (parent.classList && parent.classList.contains("user")) {
      const body = parent.querySelector(".body");
      body.insertBefore(wrap, body.firstChild);
    } else {
      parent.appendChild(wrap);
    }
  }

  function downloadAttachment(a) {
    const bin = atob(a.data);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const blob = new Blob([bytes], { type: a.mime_type || "application/octet-stream" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = a.filename || "download";
    document.body.appendChild(link); link.click(); link.remove();
    URL.revokeObjectURL(url);
  }

  function appendMessage(role, thinking = false) {
    const es = messagesEl.querySelector(".empty-state");
    if (es) es.remove();
    const el = document.createElement("div");
    el.className = `msg ${role}`;
    el.innerHTML = `<div class="role">${role === "user" ? "you" : "curunir"}</div><div class="body"></div>`;
    if (thinking) ensureActivityIndicator(el, true);
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  function scrollToBottom() {
    requestAnimationFrame(() => { messagesEl.scrollTop = messagesEl.scrollHeight; });
  }

  // === Empty state ===
  function renderEmptyState() {
    const existing = messagesEl.querySelector(".empty-state");
    if (existing) existing.remove();
    const wrap = document.createElement("div");
    wrap.className = "empty-state";
    const greeting = document.createElement("div");
    greeting.className = "es-greeting";
    greeting.textContent = "What would you like to do?";
    wrap.appendChild(greeting);
    const skills = (skillsCache || []).filter((s) => s.starter);
    if (skills.length) {
      const list = document.createElement("div");
      list.className = "es-list";
      for (const s of skills) {
        const row = document.createElement("div");
        row.className = "es-starter";
        row.innerHTML = '<span class="es-arrow">→</span><span class="es-text"></span>';
        row.querySelector(".es-text").textContent = s.summary;
        row.addEventListener("click", () => fillComposer("/" + s.name + " "));
        list.appendChild(row);
      }
      wrap.appendChild(list);
    }
    messagesEl.appendChild(wrap);
  }

  function fillComposer(text) {
    inputEl.value = text;
    inputEl.focus();
    inputEl.setSelectionRange(text.length, text.length);
    inputEl.dispatchEvent(new Event("input"));
  }

  // === Skills picker ===
  function renderSkillsList(skills, filter = "") {
    const listEl = skillsOverlay.list;
    if (!skills.length) {
      listEl.innerHTML = '<div class="cu-skills-empty">No skills available yet.</div>'; return;
    }
    const f = filter.trim().toLowerCase();
    const shown = f
      ? skills.filter((s) => s.display_name.toLowerCase().includes(f) || s.summary.toLowerCase().includes(f))
      : skills;
    if (!shown.length) { listEl.innerHTML = '<div class="cu-skills-empty">No matches.</div>'; return; }
    listEl.innerHTML = "";
    for (const s of shown) {
      const row = document.createElement("div");
      row.className = "skill-row";
      row.innerHTML = '<div><div class="skill-name"></div><div class="skill-summary"></div></div>';
      row.querySelector(".skill-name").textContent = s.display_name;
      row.querySelector(".skill-summary").textContent = s.summary;
      row.addEventListener("click", () => launchSkill("/" + s.name));
      listEl.appendChild(row);
    }
  }

  function renderSkillsConfig() {
    const cfgEl = skillsOverlay.config;
    cfgEl.innerHTML = "";
    for (const c of SKILLS_CONFIG) {
      const row = document.createElement("div");
      row.className = "skill-row config";
      row.innerHTML = '<span class="skill-icon"></span><div class="skill-name"></div>';
      row.querySelector(".skill-icon").textContent = c.icon;
      row.querySelector(".skill-name").textContent = c.label;
      row.addEventListener("click", () => launchSkill(c.command));
      cfgEl.appendChild(row);
    }
  }

  function openSkills() {
    skillsOverlay.overlay.hidden = false;
    skillsOverlay.search.value = "";
    renderSkillsConfig();
    if (skillsCache) {
      renderSkillsList(skillsCache);
    } else {
      skillsOverlay.list.innerHTML = '<div class="cu-skills-empty">Loading…</div>';
      connection.send({ command: "skills_request", session_id: getSessionId() });
    }
    skillsOverlay.search.focus();
  }

  function closeSkills() { skillsOverlay.overlay.hidden = true; }

  function launchSkill(slashText) {
    closeSkills();
    if (!agentOnline) return;
    const el = appendMessage("user");
    setUserBody(el.querySelector(".body"), slashText);
    connection.send({ command: "slash", text: slashText, session_id: getSessionId() });
    if (!inProgressMsg) inProgressMsg = appendMessage("assistant", true);
    updateComposerMode();
  }

  // === Send / interrupt ===
  function send() {
    const content = inputEl.value.trim();
    if (!content && staged.length === 0) return;
    if (!agentOnline) return;
    if (content.startsWith("/") && staged.length === 0) {
      const el = appendMessage("user");
      setUserBody(el.querySelector(".body"), content);
      connection.send({ command: "slash", text: content, session_id: getSessionId() });
      inputEl.value = "";
      if (!inProgressMsg) inProgressMsg = appendMessage("assistant", true);
      updateComposerMode();
      return;
    }
    const el = appendMessage("user");
    el.querySelector(".body").textContent = content;
    renderAttachments(el, staged.map((a) => ({ filename: a.filename })));
    connection.send({
      content, session_id: getSessionId(),
      attachments: staged.length ? staged.map((a) => ({
        filename: a.filename, mime_type: a.mime_type, data: a.data,
      })) : null,
    });
    inputEl.value = "";
    staged = [];
    renderStaged();
    if (!inProgressMsg) inProgressMsg = appendMessage("assistant", true);
    updateComposerMode();
  }

  function interrupt() {
    if (!inProgressMsg || !agentOnline || !connection.isOpen()) return;
    connection.send({ command: "interrupt", session_id: getSessionId() });
    inProgressMsg.classList.add("stopping");
    sendBtn.dataset.mode = "send";
    sendBtn.textContent = "↑"; sendBtn.title = "Send";
    sendBtn.setAttribute("aria-label", "Send");
  }

  // === Attachments staging ===
  function bytesToBase64(bytes) {
    const CHUNK = 0x8000;
    let binary = "";
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return btoa(binary);
  }

  async function stageFile(file) {
    const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    const isImg = file.type.startsWith("image/");
    const isPdf = file.type === "application/pdf" || ext === ".pdf";
    const isDocx = file.type === DOCX_MIME || ext === ".docx";
    const isText = !isImg && !isPdf && !isDocx && (
      file.type.startsWith("text/") || file.type === "application/json" || TEXT_EXTS.includes(ext)
    );
    if (isImg && !IMAGE_MIMES.includes(file.type)) { alert(`Unsupported image type: ${file.type}`); return; }
    if (!isImg && !isPdf && !isDocx && !isText) {
      alert(`Unsupported file type: ${file.name}\nSupported: images (png/jpg/gif/webp), PDF, DOCX, and text formats.`);
      return;
    }
    if (isImg && file.size > MAX_IMAGE) { alert("Image > 5 MB"); return; }
    if (isPdf && file.size > MAX_PDF) { alert("PDF > 10 MB"); return; }
    if (isDocx && file.size > MAX_DOCX) { alert("DOCX > 10 MB"); return; }
    if (isText && file.size > MAX_TEXT) { alert("Text > 256 KB"); return; }
    const totalAfter = staged.reduce((s, a) => s + a.size, 0) + file.size;
    if (totalAfter > MAX_TOTAL) { alert("Total > 20 MB"); return; }
    const buf = await file.arrayBuffer();
    staged.push({
      filename: file.name,
      mime_type: file.type || "application/octet-stream",
      data: bytesToBase64(new Uint8Array(buf)),
      size: file.size,
    });
    renderStaged();
  }

  function renderStaged() {
    stagedEl.innerHTML = "";
    staged.forEach((a, i) => {
      const chip = document.createElement("span");
      chip.className = "attachment";
      chip.textContent = `📎 ${a.filename} ✕`;
      chip.onclick = () => { staged.splice(i, 1); renderStaged(); };
      stagedEl.appendChild(chip);
    });
  }

  // === Composer behavior ===
  function autosizeInput() {
    if (composerEl.classList.contains("collapsed")) return;
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(Math.max(inputEl.scrollHeight, 60), window.innerHeight * 0.3) + "px";
  }
  function setCollapsed(collapsed) {
    composerEl.classList.toggle("collapsed", collapsed);
    collapseHandle.textContent = collapsed ? "⌃" : "⌄";
    collapseHandle.title = collapsed ? "Expand input" : "Collapse input";
    collapseHandle.setAttribute("aria-label", collapseHandle.title);
    if (!collapsed) { inputEl.style.height = ""; autosizeInput(); }
  }

  // === Wiring ===
  sendBtn.onclick = () => { if (sendBtn.dataset.mode === "stop") interrupt(); else send(); };
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  inputEl.addEventListener("input", autosizeInput);
  collapseHandle.addEventListener("click", () => setCollapsed(!composerEl.classList.contains("collapsed")));
  inputEl.addEventListener("focus", () => { if (composerEl.classList.contains("collapsed")) setCollapsed(false); });

  if (feat.skills) {
    skillsBtn.addEventListener("click", openSkills);
    skillsOverlay.close.addEventListener("click", closeSkills);
    skillsOverlay.overlay.addEventListener("click", (e) => { if (e.target === skillsOverlay.overlay) closeSkills(); });
    skillsOverlay.search.addEventListener("input", () => {
      if (skillsCache) renderSkillsList(skillsCache, skillsOverlay.search.value);
    });
  }

  // Esc: close skills if open, else interrupt an in-flight turn.
  function onKeydown(e) {
    if (e.key !== "Escape") return;
    if (skillsOverlay && !skillsOverlay.overlay.hidden) { e.preventDefault(); closeSkills(); return; }
    if (inProgressMsg) { e.preventDefault(); interrupt(); }
  }
  document.addEventListener("keydown", onKeydown);

  // Attachments: picker + drag/drop + paste.
  const dndHandlers = [];
  if (feat.attachments) {
    attachBtn.onclick = () => fileInput.click();
    fileInput.onchange = async () => {
      for (const f of fileInput.files) await stageFile(f);
      fileInput.value = "";
    };
    const isFileDrag = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
    const onDragEnter = (e) => { if (!isFileDrag(e)) return; dragDepth++; container.classList.add("dragging"); };
    const onDragLeave = (e) => { if (!isFileDrag(e)) return; dragDepth = Math.max(0, dragDepth - 1); if (dragDepth === 0) container.classList.remove("dragging"); };
    const onDragOver = (e) => { if (isFileDrag(e)) e.preventDefault(); };
    const onDrop = async (e) => {
      if (!isFileDrag(e)) return;
      e.preventDefault(); dragDepth = 0; container.classList.remove("dragging");
      for (const f of e.dataTransfer.files) await stageFile(f);
      inputEl.focus();
    };
    container.addEventListener("dragenter", onDragEnter);
    container.addEventListener("dragleave", onDragLeave);
    container.addEventListener("dragover", onDragOver);
    container.addEventListener("drop", onDrop);
    dndHandlers.push(["dragenter", onDragEnter], ["dragleave", onDragLeave], ["dragover", onDragOver], ["drop", onDrop]);
    inputEl.addEventListener("paste", async (e) => {
      const items = e.clipboardData?.items || [];
      const files = [];
      for (const it of items) { if (it.kind === "file") { const f = it.getAsFile(); if (f) files.push(f); } }
      if (!files.length) return;
      e.preventDefault();
      for (const f of files) await stageFile(f);
    });
  }

  function rebind(/* sessionId is read live via getSessionId */) {
    messagesEl.innerHTML = "";
    inProgressMsg = null; historyAssistant = null;
    bootstrap();
  }

  function destroy() {
    document.removeEventListener("keydown", onKeydown);
    for (const [type, fn] of dndHandlers) container.removeEventListener(type, fn);
    container.innerHTML = "";
  }

  // Initial paint: show the "Connecting…" overlay and disable the composer
  // until the agent's first agent_status frame flips us online.
  setStatus("reconnecting");

  return { handleFrame, setStatus, bootstrap, rebind, destroy };
}

// === Global singletons shared across instances ===
function ensureSkillsOverlay() {
  let overlay = document.querySelector(".cu-skills-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "cu-skills-overlay";
    overlay.hidden = true;
    overlay.innerHTML =
      '<div class="cu-skills-panel" role="dialog" aria-label="Skills" aria-modal="true">' +
      '<div class="cu-skills-head"><span class="cu-skills-title">✦ Skills</span>' +
      '<button class="cu-skills-close" aria-label="Close">✕</button></div>' +
      '<input class="cu-skills-search" type="text" placeholder="Search skills…" autocomplete="off">' +
      '<div class="cu-skills-list"></div>' +
      '<div class="cu-skills-config-label">SETTINGS</div><div class="cu-skills-config"></div></div>';
    document.body.appendChild(overlay);
  }
  return {
    overlay,
    panel: overlay.querySelector(".cu-skills-panel"),
    search: overlay.querySelector(".cu-skills-search"),
    list: overlay.querySelector(".cu-skills-list"),
    close: overlay.querySelector(".cu-skills-close"),
    config: overlay.querySelector(".cu-skills-config"),
  };
}

function ensurePrintDoc() {
  if (!document.getElementById("cu-print-doc")) {
    const d = document.createElement("div");
    d.id = "cu-print-doc";
    d.setAttribute("aria-hidden", "true");
    document.body.appendChild(d);
  }
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
