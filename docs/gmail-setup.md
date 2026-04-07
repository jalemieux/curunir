# Gmail Channel Setup (Service Account)

The email channel uses a Google Workspace service account with domain-wide delegation to access Gmail. No interactive OAuth flows, no token renewal, no external CLI tools — just a JSON key file.

## Prerequisites

- A **Google Workspace** domain (not a personal @gmail.com account — domain-wide delegation requires Workspace)
- Admin access to the Google Workspace Admin Console
- A GCP project with billing enabled

## Step 1: Create a Service Account

1. Go to [GCP Console → IAM & Admin → Service Accounts](https://console.cloud.google.com/iam-admin/serviceAccounts)
2. Click **Create Service Account**
3. Name it (e.g. `curunir`) and click **Create and Continue**
4. Skip the optional roles/access steps — click **Done**

## Step 2: Create a JSON Key

> **Note:** If your organization enforces the `iam.disableServiceAccountKeyCreation` policy, you'll need to temporarily override it:
> - Go to **IAM & Admin → Organization Policies**
> - Search for `iam.disableServiceAccountKeyCreation`
> - Click **Manage Policy** → Override parent's policy → Set enforcement to **Off**
> - You may need the **Organization Policy Administrator** role at the org level to do this
> - Re-enable the policy after creating the key

1. Click into the service account you just created
2. Go to the **Keys** tab → **Add Key → Create new key → JSON**
3. Download and store the key securely (e.g. `secrets/service-account.json`)

## Step 3: Enable the Gmail API

1. Go to [APIs & Services → Library](https://console.cloud.google.com/apis/library)
2. Search for **Gmail API** and click **Enable**

## Step 4: Configure Domain-Wide Delegation

1. In the service account details, go to **Details → Advanced settings**
2. Copy the **Client ID** (a numeric string)
3. Check **Enable Google Workspace Domain-wide Delegation** and save
4. Go to [Google Workspace Admin Console](https://admin.google.com/)
5. Navigate to **Security → Access and data control → API controls → Domain-wide delegation**
6. Click **Add new**
7. Enter the **Client ID** from above
8. For OAuth scopes, enter:
   ```
   https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.send,https://www.googleapis.com/auth/gmail.modify
   ```
9. Click **Authorize**

This grants the service account permission to read, send, and modify email on behalf of any user in your domain.

## Step 5: Configure Curunir

Set environment variables in your `.env`:

```bash
EMAIL_ENABLED=true
GOOGLE_SERVICE_ACCOUNT_FILE=./secrets/service-account.json
GOOGLE_DELEGATED_USER=you@yourdomain.com
EMAIL_ALLOWED_SENDERS=alice@example.com,bob@example.com
```

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAIL_ENABLED` | `false` | Enable the email channel |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | — | Path to the service account JSON key file |
| `GOOGLE_DELEGATED_USER` | — | Workspace email address the bot impersonates |
| `EMAIL_ALLOWED_SENDERS` | — | Comma-separated allowed sender addresses (empty = allow all) |
| `EMAIL_POLL_INTERVAL` | `60` | Seconds between inbox polls |
| `EMAIL_PROCESSED_LABEL` | `agent/processed` | Gmail label applied to processed threads |

### Docker

Mount the key file as a read-only volume in `docker-compose.yml`:

```yaml
services:
  curunir:
    volumes:
      - ./secrets/service-account.json:/secrets/service-account.json:ro
    environment:
      - GOOGLE_SERVICE_ACCOUNT_FILE=/secrets/service-account.json
```

## Troubleshooting

### "403 Forbidden" or "Delegation denied"

- Verify the Client ID in Workspace Admin matches the service account
- Verify all three OAuth scopes are listed (readonly, send, modify)
- Delegation changes can take up to 24 hours to propagate (usually minutes)

### "Service account not found" or key errors

- Check that the JSON key file path is correct and readable
- Verify the file is valid JSON: `python -c "import json; json.load(open('secrets/service-account.json'))"`

### "Invalid grant" or "User not found"

- `GOOGLE_DELEGATED_USER` must be a real user in your Workspace domain
- Personal @gmail.com accounts cannot use domain-wide delegation

### Emails not sending

- Confirm the Gmail API is enabled in the GCP project
- Verify `EMAIL_ENABLED=true` in your `.env`
- Set `LOG_LEVEL=DEBUG` to see Gmail API call details in the logs
