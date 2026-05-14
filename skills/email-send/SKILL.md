---
name: email-send
description: "Send a NEW outbound email to a recipient who is not already in the active email thread. Do NOT use to reply on an inbound email thread — the email channel replies automatically with your final assistant message."
---

# Sending Email

Send emails via the Gmail API using `src.channels.gmail` through bash.
The sender address is `$GOOGLE_DELEGATED_USER`, configured at the environment level.

## Recipient allowlist

When `EMAIL_RESTRICT_OUTBOUND` is `true` (the default), `send_email` / `send_reply`
will **raise `GmailError`** if any `to`/`cc`/`bcc` address isn't in
`EMAIL_ALLOWED_SENDERS`. In practice this means you can only email the user.
Don't try to route around it (constructing raw Gmail API calls yourself, etc.) —
if a send is blocked, that's intentional; report it and stop. To genuinely lift
the restriction the operator sets `EMAIL_RESTRICT_OUTBOUND=false` in the
environment.

## When NOT to use this skill

If the current conversation arrived over the email channel (the user message
begins with `[channel: email, ...]`), the email channel will automatically send
your final assistant message as a reply in that thread. Do **not** also call
`send_reply` from this skill — you will send two emails. Just write your reply
as your normal final response; the channel delivers it.

Use this skill only for:
- Sending a new email to a different recipient (not the inbound sender)
- Sending outbound email from a non-email session (CLI, scheduled task)

## Basic Send

```bash
python3 -c "
from src.channels.gmail import build_service, send_email
import os
service = build_service(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], os.environ['GOOGLE_DELEGATED_USER'])
send_email(to='recipient@example.com', subject='Subject line', body='Plain text body here', service=service)
print('Sent.')
"
```

## HTML Body

Provide both plain text and HTML — the recipient's client chooses which to display:

```bash
python3 -c "
from src.channels.gmail import build_service, send_email
import os
service = build_service(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], os.environ['GOOGLE_DELEGATED_USER'])
send_email(
    to='recipient@example.com',
    subject='Subject line',
    body='Plain text fallback',
    body_html='<h1>Hello</h1><p>Rich content here</p>',
    service=service,
)
print('Sent.')
"
```

## Long Body from File

For long content, write to a temp file first, then read it in:

```bash
python3 -c "
from src.channels.gmail import build_service, send_email
import os
service = build_service(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], os.environ['GOOGLE_DELEGATED_USER'])
with open('/tmp/email-body.txt') as f:
    body = f.read()
send_email(to='recipient@example.com', subject='Subject line', body=body, service=service)
print('Sent.')
"
```

## Multiple Recipients, CC, BCC

```bash
python3 -c "
from src.channels.gmail import build_service, send_email
import os
service = build_service(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], os.environ['GOOGLE_DELEGATED_USER'])
send_email(
    to='one@example.com,two@example.com',
    cc='cc@example.com',
    bcc='bcc@example.com',
    subject='Subject line',
    body='Content',
    service=service,
)
print('Sent.')
"
```

## Attachments

```bash
python3 -c "
from src.channels.gmail import build_service, send_email
import os
service = build_service(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], os.environ['GOOGLE_DELEGATED_USER'])
send_email(
    to='recipient@example.com',
    subject='Report attached',
    body='See attached.',
    attachments=['/path/to/report.pdf', '/path/to/data.csv'],
    service=service,
)
print('Sent.')
"
```

## Replying to a Thread

When replying to an existing email thread, use `send_reply` with the original message ID:

```bash
python3 -c "
from src.channels.gmail import build_service, send_reply
import os
service = build_service(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], os.environ['GOOGLE_DELEGATED_USER'])
send_reply(
    to='recipient@example.com',
    subject='Re: Original subject',
    body='Reply content',
    reply_to_message_id='MESSAGE_ID',
    service=service,
)
print('Sent.')
"
```

## Tips

- Always let the script read env vars at runtime — never hardcode credentials or addresses.
- For reports or long-form content, write the body to a temp file and read it in
  to avoid shell quoting issues.
- Quote string arguments carefully when embedding in `python3 -c`.
