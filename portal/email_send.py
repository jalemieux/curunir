"""Send transactional sign-in emails via Postmark.

Single function: send_signin_email(email, link). Wraps Postmark's
`/email` HTTP endpoint via httpx. If `EMAIL_API_KEY` is empty, the
function logs the email instead of sending — useful for local dev.
"""

import logging

import httpx

from portal.config import settings


logger = logging.getLogger(__name__)
POSTMARK_URL = "https://api.postmarkapp.com/email"


async def send_signin_email(email: str, link: str) -> None:
    body_html = (
        f"<p>You've been invited to Curunir.</p>"
        f"<p><a href=\"{link}\">Click here to sign in</a></p>"
        f"<p>This link is reusable — keep the email if you sign in on multiple devices.</p>"
    )
    body_text = (
        f"You've been invited to Curunir.\n\n"
        f"Click here to sign in: {link}\n\n"
        f"This link is reusable — keep the email if you sign in on multiple devices."
    )

    if not settings.email_api_key:
        logger.warning(
            "EMAIL_API_KEY unset; would have emailed %s with link %s", email, link
        )
        return

    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            POSTMARK_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Postmark-Server-Token": settings.email_api_key,
            },
            json={
                "From": settings.email_from,
                "To": email,
                "Subject": "Sign in to Curunir",
                "HtmlBody": body_html,
                "TextBody": body_text,
                "MessageStream": "outbound",
            },
        )
        resp.raise_for_status()
