---
name: email-send
description: "Send a NEW outbound email to a recipient who is not already in the active email thread. Do NOT use to reply on an inbound email thread — the email channel replies automatically with your final assistant message."
---

# Sending Email

Send a new outbound email with the `email_send.py` CLI in this skill directory.
It sends via Fastmail SMTP; the sending inbox, credentials, and recipient
allowlist all come from the environment (`FASTMAIL_INBOX`, `FASTMAIL_USER`,
`FASTMAIL_PASSWORD`, `EMAIL_RESTRICT_OUTBOUND`, `EMAIL_ALLOWED_SENDERS`), so you
never pass or hardcode credentials.

Run it from the repo root via the `bash` tool. On success it prints
`{"sent": true, "id": "..."}`; on failure it prints `{"error": ..., "hint": ...}`
and exits non-zero.

## When NOT to use this skill

If the current conversation arrived over the email channel (the user message
begins with `[channel: email, ...]`), the email channel **automatically** sends
your final assistant message as the reply in that thread. Do **not** also run
this CLI — you would send two emails. Just write your reply as your normal final
response.

Use this skill only for:
- Sending a new email to a different recipient (not the inbound sender)
- Sending outbound email from a non-email session (CLI, local UI, scheduled task)

## Recipient allowlist

When `EMAIL_RESTRICT_OUTBOUND` is `true` (the default), a send to any address
not in `EMAIL_ALLOWED_SENDERS` fails with an error (exit 1). In practice you can
only email the user. Don't try to route around it — if a send is blocked, that's
intentional; report it and stop. Lifting the restriction is an operator action
(`EMAIL_RESTRICT_OUTBOUND=false`).

## Basic send

```bash
python skills/email-send/email_send.py send \
    --to recipient@example.com \
    --subject "Subject line" \
    --body "Plain text body here"
```

## Long body from a file (preferred for reports / long-form)

Write the body to a temp file first — this avoids shell-quoting problems with
long or multi-line content.

```bash
# (write the content to /tmp/email-body.txt first, e.g. with the write tool)
python skills/email-send/email_send.py send \
    --to recipient@example.com \
    --subject "Subject line" \
    --body-file /tmp/email-body.txt
```

## HTML body (automatic)

You do **not** need to render HTML yourself. By default the CLI renders the
plain-text body — which it treats as markdown — into a styled HTML part and
sends both, so headings, bold, links, and lists display cleanly in mail clients
like Gmail. Just write markdown in `--body`/`--body-file`; the pretty HTML is
produced at the transport boundary.

Pass `--html-file` **only** when you need custom styling that the default
renderer doesn't provide. When set, it overrides the auto-render: `--html-file`
becomes the HTML part and `--body`/`--body-file` the text part.

```bash
python skills/email-send/email_send.py send \
    --to recipient@example.com \
    --subject "Subject line" \
    --body-file /tmp/email-body.txt \
    --html-file /tmp/email-body.html  # optional — only for custom styling
```

## Multiple recipients, CC, BCC

`--to`, `--cc`, and `--bcc` are each repeatable and also accept comma-separated
values.

```bash
python skills/email-send/email_send.py send \
    --to one@example.com --to two@example.com \
    --cc cc@example.com \
    --bcc bcc@example.com \
    --subject "Subject line" \
    --body "Content"
```

## Attachments

`--attach` is repeatable.

```bash
python skills/email-send/email_send.py send \
    --to recipient@example.com \
    --subject "Report attached" \
    --body "See attached." \
    --attach /path/to/report.pdf --attach /path/to/data.csv
```

## Tips

- Run from the repo root (`/app` in the container) so the script can import the
  email client.
- For reports or long-form content, write the body to a temp file and pass
  `--body-file` to avoid shell quoting issues.
- Replies inside an email-channel thread are handled automatically; use this
  skill only for new outbound mail.
- The CLI never hardcodes credentials — it reads them from the environment at
  runtime.
