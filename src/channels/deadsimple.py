"""HTTP client for deadsimple.email's REST API.

Scope: only the endpoints the email channel needs. Auth, 429 retry, and
outbound-recipient allowlisting live here so the channel layer stays
focused on translating between deadsimple messages and curunir messages.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class DeadsimpleError(Exception):
    """Raised when a deadsimple API call fails."""


class DeadsimpleClient:
    def __init__(
        self,
        api_key: str,
        api_base: str,
        inbox_id: str,
        allowed_recipients: list[str],
        restrict_outbound: bool,
        timeout_sec: float = 30.0,
    ):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.inbox_id = inbox_id
        self.allowed_recipients = allowed_recipients
        self.restrict_outbound = restrict_outbound
        self._http = httpx.AsyncClient(timeout=timeout_sec)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """One-shot request with a single 429 retry honoring X-RateLimit-Reset."""
        url = f"{self.api_base}{path}"
        for attempt in (0, 1):
            try:
                resp = await self._http.request(
                    method,
                    url,
                    headers=self._headers(idempotency_key),
                    json=json_body,
                    params=params,
                )
            except httpx.HTTPError as e:
                raise DeadsimpleError(f"HTTP error on {method} {path}: {e}") from e

            if resp.status_code == 429 and attempt == 0:
                reset = resp.headers.get("X-RateLimit-Reset")
                delay = max(1.0, float(reset) - time.time()) if reset else 5.0
                logger.warning("deadsimple 429 on %s %s, sleeping %.1fs", method, path, delay)
                await asyncio.sleep(min(delay, 60.0))
                continue

            if resp.status_code >= 400:
                raise DeadsimpleError(
                    f"deadsimple {method} {path} returned {resp.status_code}: {resp.text[:500]}"
                )

            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError as e:
                raise DeadsimpleError(f"non-JSON response from {method} {path}: {e}") from e

        raise DeadsimpleError(f"giving up on {method} {path} after 429 retry")

    # --- public methods (filled in by later tasks) ---

    async def validate_inbox(self) -> dict[str, Any]:
        """Confirm the configured inbox exists and is accessible. Returns inbox JSON."""
        return await self._request("GET", f"/v1/inboxes/{self.inbox_id}")

    async def list_messages(self, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        """Single page of messages, newest-first per the API's default ordering."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self._request(
            "GET", f"/v1/inboxes/{self.inbox_id}/messages", params=params
        )

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """Full message detail: text_body, html_body, attachments[], etc."""
        resp = await self._request(
            "GET", f"/v1/inboxes/{self.inbox_id}/messages/{message_id}"
        )
        return resp.get("data", resp)

    async def download_attachment(
        self, message_id: str, attachment_id: str, dest: Path
    ) -> None:
        """Two-step download: ask for a signed URL, GET the bytes, write to dest."""
        resp = await self._request(
            "GET",
            f"/v1/inboxes/{self.inbox_id}/messages/{message_id}/attachments/{attachment_id}",
        )
        data = resp.get("data", resp)
        url = data.get("download_url") or data.get("url")
        if not url:
            raise DeadsimpleError(
                f"attachment {attachment_id} response missing download_url: {data}"
            )

        try:
            r = await self._http.get(url)
        except httpx.HTTPError as e:
            raise DeadsimpleError(f"attachment fetch failed: {e}") from e
        if r.status_code >= 400:
            raise DeadsimpleError(
                f"attachment fetch returned {r.status_code} for {attachment_id}"
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)

    def _check_recipients_allowed(self, *recipients: str | None) -> None:
        if not self.restrict_outbound or not self.allowed_recipients:
            return
        flat: list[str] = []
        for r in recipients:
            if not r:
                continue
            flat.extend(addr.strip() for addr in r.split(",") if addr.strip())
        blocked = [r for r in flat if not any(a in r for a in self.allowed_recipients)]
        if blocked:
            raise DeadsimpleError(
                f"Outbound email blocked: recipient(s) {blocked} not in allowlist "
                f"({self.allowed_recipients}). Set EMAIL_RESTRICT_OUTBOUND=false to disable."
            )

    async def send_reply(
        self, *, in_reply_to: str, to: str, text_body: str
    ) -> dict[str, Any]:
        """Threaded reply, server auto-sets In-Reply-To/References. No attachments supported."""
        self._check_recipients_allowed(to)
        return await self._request(
            "POST",
            f"/v1/inboxes/{self.inbox_id}/messages/{in_reply_to}/reply",
            json_body={"text_body": text_body},
            idempotency_key=f"reply-{in_reply_to}",
        )

    async def send_with_attachments(
        self,
        *,
        in_reply_to: str,
        to: str,
        subject: str,
        text_body: str,
        attachment_paths: list[str],
    ) -> dict[str, Any]:
        """Send via /messages with explicit threading + inline base64 attachments."""
        self._check_recipients_allowed(to)
        atts: list[dict[str, Any]] = []
        for p in attachment_paths:
            path = Path(p)
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            atts.append({
                "filename": path.name,
                "content_type": _guess_content_type(path.name),
                "data": data,
            })
        body = {
            "to": [to],
            "subject": subject,
            "text_body": text_body,
            "in_reply_to": in_reply_to,
            "references": [in_reply_to],
            "attachments": atts,
        }
        return await self._request(
            "POST",
            f"/v1/inboxes/{self.inbox_id}/messages",
            json_body=body,
            idempotency_key=f"send-attach-{in_reply_to}",
        )


def _guess_content_type(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"
