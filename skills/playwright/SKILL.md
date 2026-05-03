---
name: playwright
description: "Use when an agent needs to fetch content from a web page (especially JS-rendered), navigate sites, extract text/data, take screenshots, or interact with web UIs. Trigger: tasks involving web content retrieval, page scraping, form submission, or browser-based navigation."
---

# Web Content Agent

Fetch and extract content from JS-rendered web pages using `shot-scraper` CLI. No code required — all operations are shell commands that pipe to stdout.

**Requires:** `shot-scraper` + Chromium binary (`pip install shot-scraper && shot-scraper install`)

> **Pick the right subcommand.** Bare `shot-scraper URL` produces a **PNG screenshot** (binary) — useless for text extraction. For HTML/text use `shot-scraper html URL -o -`; for structured data or page text use `shot-scraper javascript URL "..."`. See "Common Mistakes" below.

## Usage

### Get rendered HTML from a page

```bash
# Full page HTML (JS-rendered) to stdout
shot-scraper html https://example.com -o -

# HTML of a specific element only
shot-scraper html https://example.com -s ".main-content" -o -

# Wait for JS to finish before capturing
shot-scraper html https://example.com --wait 3000 -o -

# Save to file
shot-scraper html https://example.com -o page.html
```

### Extract structured data with JavaScript

`shot-scraper javascript` runs JS on the page and returns the result as JSON to stdout.

```bash
# Page title
shot-scraper javascript https://example.com "document.title"

# All links on the page
shot-scraper javascript https://example.com "
  Array.from(document.querySelectorAll('a[href]'), a => ({
    text: a.innerText.trim(),
    href: a.href
  }))
"

# Table data as arrays
shot-scraper javascript https://example.com/data "
  Array.from(document.querySelectorAll('table tr'), row => {
    const cells = row.querySelectorAll('td, th');
    return Array.from(cells, c => c.innerText.trim());
  })
"

# All text content from an element
shot-scraper javascript https://example.com "
  document.querySelector('.article-body').innerText
" --raw
```

**Important:** Object literals must be wrapped in parentheses or they're parsed as code blocks:

```bash
# WRONG — interpreted as code block
shot-scraper javascript https://example.com "{title: document.title}"

# RIGHT — wrapped in parens
shot-scraper javascript https://example.com "({title: document.title, url: location.href})"
```

Use `--raw` / `-r` to get plain text output instead of JSON-encoded strings.

### Wait for dynamic content in JS extractions

The `javascript` subcommand does NOT support `--wait` or `--wait-for`. To wait for content, use an async wrapper with a delay or DOM polling:

```bash
# Fixed delay — use for SPAs that need time to render
shot-scraper javascript https://example.com -i /dev/stdin <<'EOF'
async () => {
  await new Promise(r => setTimeout(r, 3000));
  return {
    title: document.title,
    content: document.querySelector(".main")?.innerText
  };
}
EOF

# Poll for a specific element — more reliable than fixed delay
shot-scraper javascript https://example.com -i /dev/stdin <<'EOF'
async () => {
  for (let i = 0; i < 20; i++) {
    if (document.querySelector(".data-loaded")) break;
    await new Promise(r => setTimeout(r, 500));
  }
  return document.querySelector(".data-loaded")?.innerText;
}
EOF
```

Use `-i /dev/stdin` with a heredoc when JS contains quotes that fight shell escaping. For reusable extractions, save JS to a file and use `-i script.js`.

### Scroll to load lazy/infinite content

```bash
shot-scraper javascript https://example.com -i /dev/stdin <<'EOF'
async () => {
  for (let i = 0; i < 5; i++) {
    window.scrollBy(0, 800);
    await new Promise(r => setTimeout(r, 1500));
  }
  return Array.from(
    document.querySelectorAll(".item"),
    el => el.innerText.trim()
  );
}
EOF
```

### Get the accessibility tree

Returns a structured JSON representation of the page — great for understanding page layout without parsing HTML.

```bash
shot-scraper accessibility https://example.com
shot-scraper accessibility https://example.com | jq '.children[] | select(.role == "link")'
```

### Take screenshots

```bash
# Full page screenshot
shot-scraper https://example.com -o page.png

# Viewport-sized (not full page)
shot-scraper https://example.com -o page.png -w 1440 -h 900

# Specific element
shot-scraper https://example.com -o nav.png -s "nav"

# Retina (2x resolution)
shot-scraper https://example.com -o page.png --retina

# JPEG with quality
shot-scraper https://example.com -o page.jpg --quality 80
```

### Save page as PDF

```bash
shot-scraper pdf https://example.com -o page.pdf
shot-scraper pdf https://example.com -o page.pdf --landscape --print-background
shot-scraper pdf https://example.com -o page.pdf --format letter --media-screen
```

### Capture network traffic (HAR)

```bash
shot-scraper har https://example.com -o traffic.har
shot-scraper har https://example.com -o traffic.har.zip --zip
```

### Manipulate the page before capture

The `--javascript` / `-j` flag on `html`, `shot`, and `pdf` commands runs JS **before** the capture:

```bash
# Remove ads/popups before screenshot
shot-scraper https://example.com -o clean.png -j "
  document.querySelectorAll('.ad, .popup, .cookie-banner').forEach(el => el.remove())
"

# Click a button to load content, then capture HTML
shot-scraper html https://example.com -o - -j "
  document.querySelector('#load-more').click()
" --wait 2000

# Scroll to load lazy content before capture
shot-scraper html https://example.com -o - -j "
  window.scrollTo(0, document.body.scrollHeight)
" --wait 3000
```

### Wait for dynamic content (html/shot/pdf commands)

```bash
# Wait fixed ms after page load
shot-scraper html https://example.com --wait 5000 -o -

# Wait until a JS condition is true (e.g., element exists)
shot-scraper https://example.com -o loaded.png --wait-for "document.querySelector('.data-loaded')"
```

`--wait` and `--wait-for` are available on `html`, `shot`, and `pdf` — but NOT on `javascript` or `accessibility`.

### Handle authenticated / bot-protected pages

Many sites (Reddit, LinkedIn, etc.) block headless browsers at the network level. Spoofing user agents is not enough. Use `shot-scraper auth` to save a real login session:

**Step 1 — save auth cookies (interactive, one-time):**

```bash
shot-scraper auth https://www.reddit.com/login auth-reddit.json
shot-scraper auth https://x.com/login auth-x.json
```

This opens a real browser. Log in manually, then press Enter. Cookies and storage are saved to the JSON file.

**Step 2 — use saved auth on any command:**

```bash
shot-scraper html https://www.reddit.com/r/some/post -a auth-reddit.json -o -
shot-scraper javascript https://x.com/someuser -a auth-x.json "document.title"
shot-scraper https://example.com/dashboard -a auth-x.json -o dash.png
```

**HTTP Basic auth** (no interactive step needed):

```bash
shot-scraper html https://example.com --auth-username user --auth-password pass -o -
```

### Bypass bot detection

Some sites detect headless Chromium via automation flags. These options can help:

```bash
# Disable automation detection
shot-scraper javascript https://example.com \
  --browser-arg '--disable-blink-features=AutomationControlled' \
  "document.title"

# Use a realistic user agent
shot-scraper html https://example.com \
  --user-agent 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36' \
  -o -
```

**Note:** These flags help with basic detection but NOT with aggressive network-level blocking (Reddit, LinkedIn). For those sites, `shot-scraper auth` with real login cookies is the only reliable approach.

### Pipe into other tools

```bash
# Extract, then process with jq
shot-scraper javascript https://example.com "..." | jq '.[] | .title'

# Accessibility tree to find all headings
shot-scraper accessibility https://example.com | jq '.. | select(.role? == "heading")'
```

### Batch screenshots with multi

`shot-scraper multi` reads a YAML file for batch screenshot operations:

```yaml
# shots.yml
- url: https://example.com
  output: home.png
  width: 1440
  height: 900

- url: https://example.com/about
  output: about.png
  selector: ".main-content"

- url: https://example.com/products
  output: products.png
  javascript: "document.querySelector('.cookie-banner')?.remove()"
  wait: 2000
```

```bash
shot-scraper multi shots.yml
```

## Site-Specific Notes

| Site | Status | Notes |
|---|---|---|
| Reddit | Blocks headless browsers | Network-level block. User agent spoofing doesn't help. Use `shot-scraper auth` with real login, or use Reddit's JSON API (`curl URL.json`). |
| X / Twitter | Profile public, tweets gated | Profile metadata (name, bio, location, followers) works unauthenticated. Tweet timeline requires login — X shows `emptyState` for unauthenticated sessions. |
| LinkedIn | Blocks headless browsers | Aggressive bot detection. Requires `shot-scraper auth` with real login session. |
| Hacker News | Works fully | Simple server-rendered HTML. No JS wait needed. |
| Most blogs/docs | Works fully | Standard pages work out of the box. |

When a site blocks headless access, check for:
1. A public API (Reddit `.json` suffix, GitHub API, etc.)
2. An RSS feed
3. `shot-scraper auth` with real login cookies as a last resort

## Tips

- **`shot-scraper javascript` is the most powerful subcommand.** It can extract any data from any page — text, links, tables, metadata, JSON-LD, Open Graph tags. Prefer it over parsing raw HTML.
- **Use `-i` for complex JS.** Inline JS fights shell escaping with CSS attribute selectors (quotes inside quotes). Use `-i script.js` or `-i /dev/stdin` with heredocs instead.
- **Use `--raw` for text content.** Without it, string results are JSON-encoded (wrapped in quotes, escaped). With `-r`, you get plain text.
- **Pipe-friendly by default.** `javascript` and `accessibility` output to stdout. For `html`, `shot`, and `pdf`, use `-o -` to pipe to stdout.
- **`--wait-for` beats `--wait`.** If you know what you're waiting for, use `--wait-for "document.querySelector('.loaded')"` instead of guessing a sleep duration. Available on `html`, `shot`, `pdf` — not on `javascript`.
- **Container setup:** `pip install shot-scraper && shot-scraper install` handles everything. For minimal containers: `shot-scraper install chromium` to skip Firefox/WebKit.
- **Check the HAR first.** If you need API data from a JS-heavy page, `shot-scraper har` captures all network requests. Look for JSON API calls you can hit directly with `curl`.
- **`multi` is screenshots only.** For batch JS extraction or HTML dumps, loop in shell instead.

## Common Mistakes

- **Using `shot-scraper URL` (no subcommand) when you wanted text** — that takes a PNG screenshot. For HTML use `shot-scraper html URL -o -`; for text/data use `shot-scraper javascript URL "..."`.
- **Forgetting `shot-scraper install`** — after `pip install shot-scraper`, you must run `shot-scraper install` to download Chromium. Without it, every command fails.
- **Using `--wait` on `shot-scraper javascript`** — the `javascript` subcommand does NOT support `--wait` or `--wait-for`. Use an `async () => {}` wrapper with `await new Promise(r => setTimeout(r, ms))` instead.
- **Inline JS with CSS attribute selectors** — selectors like `a[href*="/user/"]` break when nested in shell quotes. Use `-i /dev/stdin` with a heredoc or `-i script.js` to avoid escaping hell.
- **Object literals without parentheses** — `shot-scraper javascript URL "{title: document.title}"` is parsed as a code block, not an object. Wrap in parens: `"({title: document.title})"`.
- **Assuming user agent spoofing bypasses bot detection** — sites like Reddit and LinkedIn block at the network/fingerprint level. User agent alone is not enough. Use `shot-scraper auth` for real login sessions.
- **Using `--wait-for` with a CSS selector** — it expects a JS expression, not a selector string. Use `"document.querySelector('.loaded')"` not `".loaded"`.
- **Expecting `multi` to do HTML/JS/PDF** — `multi` only does screenshots. For other operations, use a shell loop.
- **`--retina` with `--scale-factor`** — these are mutually exclusive. Use one or the other.
- **`--omit-background` with JPEG** — transparency requires PNG. Don't combine with `--quality` or `.jpg` output.
- **Not using `-o -` for stdout** — `html`, `shot`, and `pdf` default to writing files. Add `-o -` to pipe to stdout.
- **Treating auth.json as non-sensitive** — it contains session cookies and local storage. Add to `.gitignore`, treat like a credential file.
