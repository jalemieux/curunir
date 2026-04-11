"""Gmail operations via Google Workspace service account."""

import base64
import logging
import os
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class GmailError(Exception):
    """Raised when a Gmail API call fails."""


def build_service(service_account_file: str, delegated_user: str):
    """Build a Gmail API service object with domain-wide delegation."""
    credentials = service_account.Credentials.from_service_account_file(
        service_account_file,
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
        subject=delegated_user,
    )
    return build("gmail", "v1", credentials=credentials)


def labels_list(service) -> list[dict]:
    """List all Gmail labels."""
    try:
        resp = service.users().labels().list(userId="me").execute()
        return resp.get("labels", [])
    except HttpError as e:
        raise GmailError(f"Failed to list labels: {e}") from e


def labels_create(label: str, service) -> None:
    """Create a Gmail label."""
    try:
        service.users().labels().create(
            userId="me", body={"name": label}
        ).execute()
    except HttpError as e:
        raise GmailError(f"Failed to create label '{label}': {e}") from e


def search(query: str, service, max_results: int = 20) -> list[dict]:
    """Search Gmail threads matching a query."""
    try:
        resp = service.users().threads().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        return resp.get("threads", [])
    except HttpError as e:
        raise GmailError(f"Failed to search: {e}") from e


def thread_get(thread_id: str, service) -> dict:
    """Get a thread by ID, with messages normalized to flat format."""
    try:
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="full"
        ).execute()
    except HttpError as e:
        raise GmailError(f"Failed to get thread {thread_id}: {e}") from e
    thread["messages"] = [_normalize_message(m) for m in thread.get("messages", [])]
    return thread


def send_email(
    to: str, subject: str, body: str, service,
    cc: str | None = None, bcc: str | None = None,
    body_html: str | None = None,
    attachments: list[str] | None = None,
) -> None:
    """Send a new email, optionally with HTML body and/or file attachments."""
    try:
        # Build body part
        if body_html:
            body_part = MIMEMultipart("alternative")
            body_part.attach(MIMEText(body, "plain"))
            body_part.attach(MIMEText(body_html, "html"))
        else:
            body_part = MIMEText(body, "plain")

        # Wrap with attachments if needed
        if attachments:
            msg = MIMEMultipart("mixed")
            msg.attach(body_part)
            for path in attachments:
                part = MIMEBase("application", "octet-stream")
                with open(path, "rb") as f:
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition", "attachment",
                    filename=os.path.basename(path),
                )
                msg.attach(part)
        else:
            msg = body_part

        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
    except HttpError as e:
        raise GmailError(f"Failed to send email: {e}") from e


def send_reply(
    to: str, subject: str, body: str, reply_to_message_id: str, service,
    attachments: list[str] | None = None,
) -> None:
    """Send a reply to a message, optionally with file attachments."""
    try:
        # Get the original message to extract threadId and Message-ID header
        orig = service.users().messages().get(
            userId="me", id=reply_to_message_id, format="metadata",
            metadataHeaders=["Message-ID"],
        ).execute()
        thread_id = orig.get("threadId", "")
        orig_message_id = _get_header(
            orig.get("payload", {}).get("headers", []), "Message-ID"
        )

        if attachments:
            msg = MIMEMultipart()
            msg.attach(MIMEText(body, "plain"))
            for path in attachments:
                part = MIMEBase("application", "octet-stream")
                with open(path, "rb") as f:
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition", "attachment",
                    filename=os.path.basename(path),
                )
                msg.attach(part)
        else:
            msg = MIMEText(body, "plain")

        msg["To"] = to
        msg["Subject"] = subject
        if orig_message_id:
            msg["In-Reply-To"] = orig_message_id
            msg["References"] = orig_message_id

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        service.users().messages().send(
            userId="me", body={"raw": raw, "threadId": thread_id}
        ).execute()
    except HttpError as e:
        raise GmailError(f"Failed to send reply: {e}") from e


def thread_modify(thread_id: str, add_label: str, service) -> None:
    """Modify a thread (add a label)."""
    try:
        # Resolve label name to ID
        labels = labels_list(service)
        label_id = next(
            (l["id"] for l in labels if l["name"] == add_label), None
        )
        if label_id is None:
            raise GmailError(f"Label '{add_label}' not found")
        service.users().threads().modify(
            userId="me", id=thread_id,
            body={"addLabelIds": [label_id]},
        ).execute()
    except HttpError as e:
        raise GmailError(f"Failed to modify thread {thread_id}: {e}") from e


def download_attachments(thread_id: str, message: dict, out_dir: str, service) -> None:
    """Download attachments from a message to a directory.

    Writes files with their original filename (no prefix).
    """
    payload = message.get("payload", message)
    parts = _find_attachment_parts(payload)

    for part in parts:
        filename = part.get("filename", "")
        if not filename:
            continue
        att_id = part.get("body", {}).get("attachmentId")
        if not att_id:
            continue
        try:
            att = service.users().messages().attachments().get(
                userId="me", messageId=message["id"], id=att_id
            ).execute()
            data = base64.urlsafe_b64decode(att["data"])
            filepath = os.path.join(out_dir, filename)
            with open(filepath, "wb") as f:
                f.write(data)
        except HttpError as e:
            logger.warning("Failed to download attachment %s: %s", filename, e)


def _find_attachment_parts(payload: dict) -> list[dict]:
    """Recursively find all MIME parts that have a filename (attachments)."""
    parts = []
    for part in payload.get("parts", []):
        if part.get("filename"):
            parts.append(part)
        if part.get("parts"):
            parts.extend(_find_attachment_parts(part))
    return parts


# --- Message normalization ---

def _get_header(headers: list[dict], name: str) -> str:
    """Extract a header value by name (case-insensitive)."""
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode_body(payload: dict) -> str:
    """Extract plain-text body from a Gmail message payload."""
    if not payload.get("parts"):
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return ""

    for preferred in ("text/plain", "text/html"):
        for part in payload["parts"]:
            if part.get("mimeType") == preferred:
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return ""


def _extract_attachments(payload: dict) -> list[dict]:
    """Extract attachment metadata from a Gmail message payload (recursive)."""
    attachments = []
    for part in payload.get("parts", []):
        filename = part.get("filename", "")
        if filename:
            attachments.append({
                "filename": filename,
                "mimeType": part.get("mimeType", "application/octet-stream"),
                "size": part.get("body", {}).get("size", 0),
            })
        if part.get("parts"):
            attachments.extend(_extract_attachments(part))
    return attachments


def _normalize_message(raw: dict) -> dict:
    """Convert a raw Gmail API message into flat format expected by EmailChannel."""
    payload = raw.get("payload", {})
    headers = payload.get("headers", [])
    attachments = _extract_attachments(payload)
    return {
        "id": raw["id"],
        "thread_id": raw.get("threadId", ""),
        "from": _get_header(headers, "From"),
        "to": _get_header(headers, "To"),
        "subject": _get_header(headers, "Subject"),
        "body": _decode_body(payload),
        "attachments": attachments if attachments else [],
    }
