---
name: gmail-draft
description: "Put an email in the user's Gmail Drafts folder for them to review and send themselves. Use when you have written an email the user should send from their own Gmail (an intro, a follow-up, a reply they asked you to draft). Never sends."
---

# Drafting Email into Gmail

Create a draft in the user's Gmail account with the `gmail_draft.py` CLI in this
skill directory. The draft lands in their Drafts folder; they review it, edit it
and press send themselves. This skill **cannot send** — there is no send command.

Run it from the repo root via the `bash` tool. On success it prints
`{"drafted": true, "id": "..."}`; on failure it prints
`{"error": ..., "hint": ...}` and exits non-zero.

After a successful draft, tell the user it is in their Gmail Drafts folder and
give the recipient and subject so they can find it. Never say it was sent.

## When to use

- You wrote an email that should go out **from the user's own Gmail address**,
  to someone else, after the user has looked at it.

Do not use it to reply on an inbound email thread (the email channel replies
with your final message on its own) or to deliver a report to the user.

## Create a draft

```bash
python skills/gmail-draft/gmail_draft.py draft \
    --to recipient@example.com \
    --subject "Subject line" \
    --body "Body text here"
```

For anything longer than a line or two, write the body to a temp file first —
this avoids shell-quoting problems.

```bash
# (write the content to /tmp/draft-body.txt first, e.g. with the write tool)
python skills/gmail-draft/gmail_draft.py draft \
    --to recipient@example.com \
    --subject "Subject line" \
    --body-file /tmp/draft-body.txt
```

`--to`, `--cc` and `--bcc` are each repeatable and also accept comma-separated
values.

The body is treated as markdown and rendered to an HTML part automatically, with
the text kept as the plain-text fallback. Pass `--html-file` only when you need
custom HTML.

Drafts are always new messages. A draft that replies inside an existing Gmail
thread is not supported.

## If it fails

Report the error to the user and stop. A `Gmail is not configured` or
`Gmail authorization failed` error is an operator fix (below), not something to
work around — do not fall back to sending the email some other way.

The Gmail credentials belong to this script alone. Never read, print, copy or
edit the credentials file, never call the Gmail API yourself (`curl`, Python,
`web_fetch`), and never run the `auth` command.

## Setup (operator, once)

1. In a Google Cloud project: enable the **Gmail API**, configure the OAuth
   consent screen, add the scope `https://www.googleapis.com/auth/gmail.compose`
   and create an OAuth client of type **Desktop app**.
2. At your own shell, in the repo root, on a machine with a browser, run
   `python skills/gmail-draft/gmail_draft.py auth`. Paste the client ID and
   secret when prompted, open the printed URL and approve.

That writes the client and the refresh token to `.gmail-draft.json` (mode 0600)
in the shared context directory, beside `.ws-token`. No restart is needed. Do
**not** put these values in `.env`: every command the agent runs inherits the
environment, and this file is read only by the script.

The refresh token does not expire while the consent screen is published
("In production"). With the consent screen left in "Testing", Google expires it
after 7 days and step 2 has to be repeated.

`gmail.compose` is the narrowest Gmail scope that can create a draft, but the
scope also covers sending. Draft-only is a property of this script, which calls
only `users.drafts.create`. Keeping the token out of the environment narrows who
can use it but is not a hard boundary: the agent runs as the same OS user and
could read the file if it set out to.
