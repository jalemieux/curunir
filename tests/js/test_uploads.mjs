// Zero-dependency node test for the eager-upload state machine (uploads.js).
//
// Same convention as test_connection_outbox.mjs: standalone assertions over
// pure helpers, no test runner. Run directly:
//
//   node tests/js/test_uploads.mjs

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const mod = await import(
  path.resolve(here, "../../src/local_ui/static/uploads.js")
);
const {
  beginUpload,
  markUploadUndeliverable,
  applyUploadResult,
  applyDocumentCard,
  pendingIngest,
  toWireAttachments,
} = mod;

function entry(name = "doc.txt") {
  return {
    filename: name, mime_type: "text/plain", data: "QUJD", size: 3,
    status: "staging",
  };
}

// --- beginUpload / undeliverable -------------------------------------------

{
  const e = entry();
  beginUpload(e, "u1");
  assert.equal(e.status, "uploading");
  assert.equal(e.uploadId, "u1");
  assert.ok(pendingIngest([e]), "uploading blocks send");
}

{
  const e = entry();
  beginUpload(e, "u1");
  markUploadUndeliverable(e);
  assert.equal(e.status, "ready");
  assert.equal(e.path, undefined, "no path → inline fallback");
  assert.ok(!pendingIngest([e]), "undeliverable upload must not block send forever");
}

// --- applyUploadResult ------------------------------------------------------

// pending ingestion → analyzing (still blocks send), path recorded.
{
  const e = entry("big.txt");
  beginUpload(e, "u2");
  applyUploadResult([e], {
    upload_id: "u2",
    files: [{ filename: "big.txt", path: "/uploads/x/big.txt", ingest: "pending" }],
  });
  assert.equal(e.status, "analyzing");
  assert.equal(e.path, "/uploads/x/big.txt");
  assert.ok(pendingIngest([e]));
}

// skipped (small/image) → ready immediately.
{
  const e = entry("tiny.txt");
  beginUpload(e, "u3");
  applyUploadResult([e], {
    upload_id: "u3",
    files: [{ filename: "tiny.txt", path: "/uploads/x/tiny.txt", ingest: "skipped" }],
  });
  assert.equal(e.status, "ready");
  assert.ok(!pendingIngest([e]));
}

// server-side rejection → ready with inline fallback (no path).
{
  const e = entry();
  beginUpload(e, "u4");
  applyUploadResult([e], { upload_id: "u4", error: "attachment[0]: invalid base64" });
  assert.equal(e.status, "ready");
  assert.equal(e.path, undefined);
  assert.ok(e.error);
}

// frames for other uploads leave the entry untouched.
{
  const e = entry();
  beginUpload(e, "u5");
  applyUploadResult([e], { upload_id: "other", files: [] });
  assert.equal(e.status, "uploading");
}

// --- applyDocumentCard ------------------------------------------------------

{
  const e = entry("big.txt");
  beginUpload(e, "u6");
  applyUploadResult([e], {
    upload_id: "u6",
    files: [{ filename: "big.txt", path: "/u/big.txt", ingest: "pending" }],
  });
  applyDocumentCard([e], { upload_id: "u6", path: "/u/big.txt", status: "ok" });
  assert.equal(e.status, "ready");
  assert.ok(!e.error);
  assert.ok(!pendingIngest([e]));
}

// ingestion failure still unblocks (raw-document fallback), error noted.
{
  const e = entry("bad.txt");
  beginUpload(e, "u7");
  applyUploadResult([e], {
    upload_id: "u7",
    files: [{ filename: "bad.txt", path: "/u/bad.txt", ingest: "pending" }],
  });
  applyDocumentCard([e], {
    upload_id: "u7", path: "/u/bad.txt", status: "error", error: "model unavailable",
  });
  assert.equal(e.status, "ready");
  assert.ok(e.error.includes("model unavailable"));
  assert.ok(!pendingIngest([e]));
}

// --- toWireAttachments ------------------------------------------------------

// Uploaded entries ride as staged path refs; never re-send bytes.
{
  const a = entry("up.txt");
  beginUpload(a, "u8");
  applyUploadResult([a], {
    upload_id: "u8",
    files: [{ filename: "up.txt", path: "/u/up.txt", ingest: "skipped" }],
  });
  const b = entry("inline.txt"); // upload never delivered
  beginUpload(b, "u9");
  markUploadUndeliverable(b);

  const wire = toWireAttachments([a, b]);
  assert.deepEqual(wire.stagedFiles, [{ path: "/u/up.txt", filename: "up.txt" }]);
  assert.deepEqual(wire.inline, [
    { filename: "inline.txt", mime_type: "text/plain", data: "QUJD" },
  ]);
}

console.log("test_uploads.mjs: all assertions passed");
