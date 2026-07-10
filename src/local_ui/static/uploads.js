// uploads.js — pure state machine for eager document ingestion (chat.js).
//
// A staged attachment is uploaded the moment it is attached (one `upload`
// frame per file), so ingestion runs while the user is still typing; the
// composer blocks until every entry reaches "ready". docs/document-ingestion.md.
//
// Entry lifecycle:  staging → uploading → analyzing → ready
//                              ↘ ready (skipped / undeliverable / rejected)
//
// Every failure path lands on "ready": an upload that can't be delivered or
// is rejected falls back to sending the bytes inline with the message (the
// pre-ingestion behavior), and a failed ingestion sends the staged path
// without a card — so the composer can never be blocked forever.

export function beginUpload(entry, uploadId) {
  entry.status = "uploading";
  entry.uploadId = uploadId;
}

export function markUploadUndeliverable(entry) {
  entry.status = "ready";
  delete entry.path;
}

// applyUploadResult(staged, frame) -> true if any entry was updated.
// Entries are matched by uploadId; a multi-file batch matches its `files`
// to the batch's entries in order.
export function applyUploadResult(staged, frame) {
  const batch = staged.filter((e) => e.uploadId === frame.upload_id);
  if (!batch.length) return false;
  if (frame.error) {
    for (const e of batch) {
      e.status = "ready";
      e.error = frame.error;
      delete e.path;
    }
    return true;
  }
  const files = frame.files || [];
  batch.forEach((e, i) => {
    const f = files[i];
    if (!f) { e.status = "ready"; return; }
    e.path = f.path;
    e.status = f.ingest === "pending" ? "analyzing" : "ready";
  });
  return true;
}

// applyDocumentCard(staged, frame) -> true if any entry was updated.
// Both outcomes unblock: "ok" means the card is on disk; "error" means the
// message proceeds with the raw staged document.
export function applyDocumentCard(staged, frame) {
  let hit = false;
  for (const e of staged) {
    if (e.uploadId !== frame.upload_id || e.path !== frame.path) continue;
    e.status = "ready";
    if (frame.status === "error") e.error = frame.error || "ingestion failed";
    hit = true;
  }
  return hit;
}

export function pendingIngest(staged) {
  return staged.some((e) => e.status !== "ready");
}

// toWireAttachments(staged) -> { stagedFiles, inline }
// Uploaded entries ride as path refs (bytes already on the server); entries
// whose upload never landed fall back to inline base64 as before.
export function toWireAttachments(staged) {
  const stagedFiles = [];
  const inline = [];
  for (const e of staged) {
    if (e.path) {
      stagedFiles.push({ path: e.path, filename: e.filename });
    } else {
      inline.push({ filename: e.filename, mime_type: e.mime_type, data: e.data });
    }
  }
  return { stagedFiles, inline };
}
