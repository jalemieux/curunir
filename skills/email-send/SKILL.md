---
name: email-send
description: "Send a NEW outbound email to a recipient who is not already in the active email thread. Do NOT use to reply on an inbound email thread — the email channel replies automatically with your final assistant message."
---

# Sending Email

Send emails via the deadsimple.email API using `src.channels.deadsimple` through bash. The sending inbox and API key come from environment variables (`DEADSIMPLE_INBOX_ID`, `DEADSIMPLE_API_KEY`), so snippets read them at runtime.

## Recipient allowlist

When `EMAIL_RESTRICT_OUTBOUND` is `true` (the default), `send_email` will **raise `DeadsimpleError`** if any `to`/`cc`/`bcc` address isn't in `EMAIL_ALLOWED_SENDERS`. In practice this means you can only email the user. Don't try to route around it — if a send is blocked, that's intentional; report it and stop. To genuinely lift the restriction the operator sets `EMAIL_RESTRICT_OUTBOUND=false` in the environment.

## When NOT to use this skill

If the current conversation arrived over the email channel (the user message begins with `[channel: email, ...]`), the email channel will automatically send your final assistant message as a reply in that thread. Do **not** also call `send_email` from this skill — you will send two emails. Just write your reply as your normal final response; the channel delivers it.

Use this skill only for:
- Sending a new email to a different recipient (not the inbound sender)
- Sending outbound email from a non-email session (CLI, scheduled task)

## Basic Send

```bash
python3 -c "
import asyncio
from src.channels.deadsimple import build_client_from_env

async def main():
    client = build_client_from_env()
    try:
        await client.send_email(
            to='recipient@example.com',
            subject='Subject line',
            text_body='Plain text body here',
        )
        print('Sent.')
    finally:
        await client.aclose()

asyncio.run(main())
"
```

## HTML Body

Provide both plain text and HTML — the recipient's client chooses which to display:

```bash
python3 -c "
import asyncio
from src.channels.deadsimple import build_client_from_env

async def main():
    client = build_client_from_env()
    try:
        await client.send_email(
            to='recipient@example.com',
            subject='Subject line',
            text_body='Plain text fallback',
            html_body='<h1>Hello</h1><p>Rich content here</p>',
        )
        print('Sent.')
    finally:
        await client.aclose()

asyncio.run(main())
"
```

## Long Body from File

For long content, write to a temp file first, then read it in:

```bash
python3 -c "
import asyncio
from src.channels.deadsimple import build_client_from_env

async def main():
    client = build_client_from_env()
    try:
        with open('/tmp/email-body.txt') as f:
            body = f.read()
        await client.send_email(
            to='recipient@example.com',
            subject='Subject line',
            text_body=body,
        )
        print('Sent.')
    finally:
        await client.aclose()

asyncio.run(main())
"
```

## Multiple Recipients, CC, BCC

```bash
python3 -c "
import asyncio
from src.channels.deadsimple import build_client_from_env

async def main():
    client = build_client_from_env()
    try:
        await client.send_email(
            to=['one@example.com', 'two@example.com'],
            cc=['cc@example.com'],
            bcc=['bcc@example.com'],
            subject='Subject line',
            text_body='Content',
        )
        print('Sent.')
    finally:
        await client.aclose()

asyncio.run(main())
"
```

## Attachments

```bash
python3 -c "
import asyncio
from src.channels.deadsimple import build_client_from_env

async def main():
    client = build_client_from_env()
    try:
        await client.send_email(
            to='recipient@example.com',
            subject='Report attached',
            text_body='See attached.',
            attachment_paths=['/path/to/report.pdf', '/path/to/data.csv'],
        )
        print('Sent.')
    finally:
        await client.aclose()

asyncio.run(main())
"
```

## Tips

- Always let the script read env vars at runtime — never hardcode credentials or addresses.
- For reports or long-form content, write the body to a temp file and read it in to avoid shell quoting issues.
- Replies inside an email-channel thread are handled automatically; use this skill only for new outbound mail.
