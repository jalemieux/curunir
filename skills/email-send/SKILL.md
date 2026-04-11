---
name: email-send
description: "Send an email using the Gmail API via bash — supports plain text, HTML, attachments, and replies"
---

# Sending Email

Send emails via the Gmail API using `src.channels.gmail` through bash.
The sender address is `$GOOGLE_DELEGATED_USER`, configured at the environment level.

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
