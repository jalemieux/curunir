---
name: email-send
description: "Send an email using gog CLI via bash — supports plain text, HTML, attachments"
---

# Sending Email

Use the `gog` CLI to send emails via the bash tool.
The sending account is configured in the `GOG_ACCOUNT` environment variable.

## Basic Send

```bash
gog gmail send \
  --to "recipient@example.com" \
  --subject "Subject line" \
  --body "Plain text body here" \
  --account "$GOG_ACCOUNT"
```

## HTML Body

Use `--body-html` for rich formatting. You can provide both plain text and HTML —
the recipient's client will choose which to display:

```bash
gog gmail send \
  --to "recipient@example.com" \
  --subject "Subject line" \
  --body "Plain text fallback" \
  --body-html "<h1>Hello</h1><p>Rich content here</p>" \
  --account "$GOG_ACCOUNT"
```

## Long Body from File

For long content, write to a temp file first, then use `--body-file`:

```bash
gog gmail send \
  --to "recipient@example.com" \
  --subject "Subject line" \
  --body-file /tmp/email-body.txt \
  --account "$GOG_ACCOUNT"
```

## Multiple Recipients, CC, BCC

```bash
gog gmail send \
  --to "one@example.com,two@example.com" \
  --cc "cc@example.com" \
  --bcc "bcc@example.com" \
  --subject "Subject line" \
  --body "Content" \
  --account "$GOG_ACCOUNT"
```

## Attachments

Use `--attach` for each file:

```bash
gog gmail send \
  --to "recipient@example.com" \
  --subject "Report attached" \
  --body "See attached." \
  --attach /path/to/report.pdf \
  --attach /path/to/data.csv \
  --account "$GOG_ACCOUNT"
```

## Replying to a Thread

When replying to an existing email thread, use `--reply-to-message-id`:

```bash
gog gmail send \
  --reply-to-message-id "MESSAGE_ID" \
  --to "recipient@example.com" \
  --subject "Re: Original subject" \
  --body "Reply content" \
  --account "$GOG_ACCOUNT"
```

## Tips

- Always use `$GOG_ACCOUNT` for the `--account` flag — never hardcode the address.
- For reports or long-form content, write the body to a temp file and use `--body-file`
  to avoid shell quoting issues.
- Quote the `--body` and `--subject` values to handle special characters.
