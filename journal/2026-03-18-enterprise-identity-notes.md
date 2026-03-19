# Enterprise Identity for Bots and Assistants — Design Notes

## Problem Statement

For Curunir to operate in an enterprise environment, it needs an identity. Not a user's identity borrowed via a personal API key, but its own — something IT can provision, audit, scope, and revoke. How does a bot authenticate and get authorized to act on behalf of (or alongside) a user in an enterprise stack?

## The Core Tension

A personal assistant that runs on your laptop with your API keys is simple: it's you. It has your permissions because it uses your credentials. But this model breaks in enterprise:

- **Audit**: Who did what? If the bot uses your token, every action looks like you. There's no way to distinguish "Jac ran that query" from "Jac's bot ran that query."
- **Least privilege**: You might have broad access. The bot should have narrow access. Sharing credentials means the bot inherits your full blast radius.
- **Lifecycle**: When you leave, your credentials get revoked. But maybe the bot should keep running for your team. Or maybe it shouldn't — and someone needs to make that call explicitly.

## How Enterprises Handle This Today

### Service Accounts / Service Principals

The standard pattern. The bot gets its own identity in the IdP (Azure AD, Okta, Google Workspace):

- **Azure AD**: Register an "App Registration," get a client ID + secret (or certificate). Authenticate via OAuth2 client credentials flow. Permissions are granted via API scopes and admin consent.
- **Google Workspace**: Create a service account in GCP IAM. For acting on behalf of users, use domain-wide delegation with scoped OAuth grants.
- **Okta**: Register an OAuth2 service app. Use client credentials or private key JWT for machine-to-machine auth.

The bot authenticates as itself, not as any user. Permissions are granted to the bot's identity directly.

### On-Behalf-Of (Delegated Access)

Sometimes the bot needs to act *as* a user — send an email from their address, access their calendar. This is different from the bot acting on its own.

- **OAuth2 On-Behalf-Of flow**: User authenticates → bot gets a delegated token scoped to what the user consented to. The bot acts as the user, but the token is scoped and auditable.
- **SCIM/impersonation**: Some systems allow admin-granted impersonation. Dangerous, but sometimes necessary (e.g., an IT bot that provisions accounts).

### API Keys / Tokens (The Simple Path)

Many SaaS tools just use API keys: Slack bot tokens, GitHub App installation tokens, Linear API keys. These are effectively service account credentials, but less standardized:

- Each service has its own auth model
- Scoping varies wildly (Slack is granular, some tools are all-or-nothing)
- Rotation and revocation are manual unless you build automation

## What Curunir Needs to Decide

### 1. Identity Model

Does the bot have **one identity** (a single service account that acts on behalf of any user) or **per-user identity** (each user's instance authenticates separately)?

| Approach | Pro | Con |
|---|---|---|
| Single service account | Simple to provision, one set of credentials | All actions attributed to one identity, coarse audit trail |
| Per-user delegated tokens | Actions attributed to the right user, least-privilege per user | Token management complexity, refresh/expiry handling |
| Hybrid (bot identity + user delegation) | Bot acts as itself by default, escalates to user context when needed | Most complex, but most correct |

The hybrid model is probably right: the bot has its own identity for its own actions (scheduling, summarizing, filing), and acquires delegated tokens when it needs to act as a user (sending email, posting in their name).

### 2. Credential Storage

Where do the bot's credentials live?

- **Environment variables**: Simple, works for a single-machine setup. Not great for rotation.
- **Secret manager** (Vault, AWS Secrets Manager, 1Password CLI): Better for rotation and audit. Adds a dependency.
- **Keychain / OS credential store**: Good for a personal-machine bot. macOS Keychain, Linux keyring.
- **Short-lived tokens only**: No stored secrets — use certificate-based auth or workload identity federation. The gold standard, but requires infrastructure.

### 3. Scope and Permissions

What's the minimum set of permissions the bot needs? This is service-specific, but the principle is:

- **Read by default, write by exception.** The bot can read calendars, emails, tasks. It only gets write access to specific things (e.g., create calendar events, send messages in specific channels).
- **No admin access.** Ever. The bot doesn't need to manage users, change org settings, or access audit logs.
- **Scoped to the user's data.** Even with its own identity, the bot should only access data relevant to the user it's assisting. This is an application-level constraint, not just an IdP one.

### 4. Audit Trail

Enterprise security teams will ask: "What did the bot do, when, and why?"

- Every action the bot takes should be logged with: timestamp, action, target resource, on-behalf-of (if delegated), and the prompt/intent that triggered it.
- This is an application concern — the bot needs its own audit log, separate from whatever the downstream services log.
- Bonus: if the bot can explain *why* it took an action (link back to the user request or scheduled task that triggered it), that's a much better audit story.

## Practical First Steps for Curunir

1. **Start with API keys per service** — Slack bot token, GitHub App, calendar API key. This is the pragmatic starting point for a personal assistant.
2. **Store credentials in macOS Keychain or a local secret manager** — not in .env files checked into the repo.
3. **Log every external action** — even before enterprise compliance demands it, build the habit. A simple append-only log file is fine to start.
4. **Design the credential interface as pluggable** — so swapping from "API key in keychain" to "OAuth2 client credentials from Vault" doesn't require rewriting the whole integration layer.
5. **When enterprise deployment becomes real**, register a proper service principal in the org's IdP and migrate to OAuth2 flows with scoped permissions.

## Open Questions

- How does the bot handle **token refresh** gracefully in the middle of a long-running agentic loop? If a token expires mid-task, does the loop pause and re-auth, or fail and retry?
- For **multi-tenant** scenarios (bot serving multiple users in an org), how is user context isolated? Separate credential stores per user? Separate bot instances?
- What's the right abstraction for **permission escalation**? If the bot needs write access it doesn't have, does it ask the user to grant it in real-time, or does it fail and log the gap?
