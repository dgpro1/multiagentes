# Public API

> Leer en español: [api.md](../es/api.md)

Third parties build on the same resources as the panel, under a versioned prefix with a stable contract: **`/api/v1`**. The interactive schema lives at `/docs` on the backend (port 8000 locally). What is covered here, and what answers from which door, is tracked in [api-coverage.md](../api-coverage.md): a panel screen without an API route is a bug.

## Authentication

Two doors, one agency. A cookie session (signed-in person) holds every scope; an API credential is held to its agency, its scopes, and — when confined — its client.

**Long-lived tokens.** An *integration* (Settings → API integrations, or the client's API tab) is one connection: a name, its scopes, optionally a single client. Issuing a token returns the secret **once** (`ol_…`, shown never again; only a prefix and a SHA-256 digest are stored). Send it as `Authorization: Bearer ol_…` or `X-API-Key: ol_…`. Tokens expire after 1 day to 5 years and are revocable; a revoked or expired token answers `401`.

**OAuth 2.0.** An integration doubles as an OAuth client: the agency registers its redirect URLs (https; http only for loopback) and takes a client secret, a person approves scopes on `/oauth/authorize`, and the third party exchanges the single-use code (10 minutes) for a 1-hour access token plus a rotating 30-day refresh token:

| Step | Route |
| --- | --- |
| Look the client up | `GET /api/oauth/client?client_id=` |
| Approve or deny (signed-in person) | `POST /api/oauth/authorize` |
| Exchange code, refresh | `POST /api/oauth/token` |
| Revoke | `POST /api/oauth/revoke` |

A granted subset narrower than the integration travels with the grant. Auth codes are never valid as bearers.

**Confinement.** A client-limited token reaches only paths naming its client; anything else answers `404`. A route opens to tokens only by declaring a scope — anything declaring nothing stays closed — and a route guarded only by `.read` scopes is always a `GET`, so a read-only credential is exactly that.

## Scopes

| Scope | What it opens |
| --- | --- |
| `clients.read` | See the clients and their settings. |
| `clients.write` | Create and edit clients. |
| `agents.read` | See the agents and their configuration. |
| `agents.write` | Create, edit and delete agents. |
| `agents.knowledge` | Read and change an agent's knowledge base. |
| `agents.tools` | Read and change an agent's tools and MCP servers (panel only today). |
| `channels.read` | See the channels and whether they are connected. |
| `channels.manage` | Connect, configure and remove channels (panel only today). |
| `inbox.read` | Read conversations and their messages. |
| `inbox.reply` | Reply as a person. |
| `inbox.manage` | Take over from the AI and hand it back, resolve and reopen. |
| `contacts.read` | See the contacts. |
| `contacts.manage` | Create and edit contacts. |
| `tags.read` | See the contact tags. |
| `tags.manage` | Create, rename, recolor and delete contact tags. |
| `teams.read`, `teams.manage` | See / manage teams (panel only today). |
| `templates.read`, `templates.manage` | WhatsApp templates (panel only today). |
| `canned.manage` | Saved replies (panel only today). |
| `pipeline.read` | See the pipeline stages and the board. |
| `pipeline.manage` | Manage stages, move deals, create leads. |
| `calendar.read` | See the calendar of a client. |
| `calendar.manage` | Add and remove calendar members. |
| `reports.read` | See the reports. |
| `integrations.manage` | Create and revoke API credentials (panel API, not versioned). |
| `webhooks.manage` | Webhook subscriptions (panel API, not versioned). |

Presets: `read_only` (every `.read`), `operator` (reads plus reply, manage and the inbox), `full` (everything). `GET /api/integrations/scopes` lists the catalogue with presets.

## Conventions

**Envelope.** Every resource carries `_links.self`. Errors wear `{title, type, status, detail}` with `type` pointing at `/api/v1/docs/errors#<slug>`; validation failures add `validation-errors: [{field, message}]`:

```json
{"title": "Not found", "type": "/api/v1/docs/errors#not-found", "status": 404, "detail": "Client not found"}
```

Known slugs: `bad-request`, `unauthorized`, `forbidden`, `not-found`, `conflict`, `validation-failed`, `rate-limited`. Without any credential the envelope still answers (`401`, `"You are not signed in"`); a token lacking the scope answers `403` naming it (`"This API token does not hold: contacts.manage"`).

**Pagination.** List routes take `page` (from 1) and `limit`, capped at **250** items. Answers carry `data`, `page`, `limit`, `total` and `_links` with `self`/`prev`/`next`. Datetimes are ISO-8601 with offset.

## Resources

Every route below lives under `/api/v1` and names its client, except the agency-wide client collection.

**Clients** (`clients.read`, writes `clients.write`)

| Method & path | What it does |
| --- | --- |
| `GET /clients` | List the agency's clients, paginated. |
| `POST /clients` | Create a client (idempotent, see below). |
| `GET /clients/{id}` | One client. |
| `PATCH /clients/{id}` | Edit name, industry, timezone and the rest. Changing the industry drops a business type that no longer belongs to it. |

**Contacts** (`contacts.read`, writes `contacts.manage`)

| Method & path | What it does |
| --- | --- |
| `GET /clients/{id}/contacts?search=` | Search by name or phone, paginated. |
| `POST /clients/{id}/contacts` | Create (phone with country code; duplicate phone is `409`; idempotent). |
| `GET /clients/{id}/contacts/{contact_id}` | One contact. |
| `PATCH /clients/{id}/contacts/{contact_id}` | Edit name, phone, email, notes. |

**Conversations** (`inbox.read`, reply `inbox.reply`, takeover `inbox.manage`)

| Method & path | What it does |
| --- | --- |
| `GET /clients/{id}/conversations?status=` | `open` or `resolved`, paginated. |
| `GET /clients/{id}/conversations/{conversation_id}` | One conversation. |
| `GET /clients/{id}/conversations/{conversation_id}/messages` | The thread oldest-first, paginated. `kind` tells a `message` from an `activity` note. |
| `POST /clients/{id}/conversations/{conversation_id}/reply` | Answer as the operator (`{"content"}`). The case must be in human hands (`PATCH …/mode` first) and open; social lines queue through the durable outbox. Idempotent. |
| `PATCH /clients/{id}/conversations/{conversation_id}/mode` | `{"mode": "human"}` takes over, `{"mode": "ai"}` hands back, with the panel's own trace in the thread. |
| `PATCH /clients/{id}/conversations/{conversation_id}/status` | `{"status": "resolved"}` resolves, `"open"` reopens. |

Every conversation (a *lead* in the panel) carries `number`, its short number inside the client (#1, #2, #3…, never reused and unique per client), and `_links.html`, the address of its screen in the panel (`/clients/{id}/inbox/{number}`). The number is what the screens' URLs use; the UUID stays the identifier of the API routes. `conversation.resolved`, `deal.moved` and `message.received` webhooks carry `number` too.

**Pipeline** (`pipeline.read`, writes `pipeline.manage`)

| Method & path | What it does |
| --- | --- |
| `GET /clients/{id}/pipeline/board` | Stages, cards and the unassigned count. |
| `GET /clients/{id}/pipeline/stages` | The stages. |
| `POST /clients/{id}/pipeline/stages` | Create a stage (idempotent). |
| `PATCH /clients/{id}/pipeline/stages/{stage_id}` | Rename or recolor. |
| `POST /clients/{id}/pipeline/stages/reorder` | `{"stage_ids": [...]}` in the new order. |
| `DELETE /clients/{id}/pipeline/stages/{stage_id}` | Delete a stage. |
| `PATCH /clients/{id}/conversations/{conversation_id}/pipeline` | Move a deal: `{"pipeline_stage_id", "deal_value"}`. |
| `POST /clients/{id}/pipeline/leads` | A quick lead: contact plus an open, human-held case in a stage. Nothing is sent. Idempotent. |

**Tags** (`tags.read`, writes `tags.manage`)

| Method & path | What it does |
| --- | --- |
| `GET /clients/{id}/tags` | The catalogue with contact counts. |
| `POST /clients/{id}/tags` | Create (idempotent; duplicate name is `409`). |
| `PATCH /clients/{id}/tags/{tag_id}` | Rename or recolor. Routing a tag to a team or a person stays a panel gesture. |
| `DELETE /clients/{id}/tags/{tag_id}` | Delete; links go with it. |

**Agents** (`agents.read`, writes `agents.write`)

| Method & path | What it does |
| --- | --- |
| `GET /clients/{id}/agents` | The client's agents, paginated. |
| `POST /clients/{id}/agents` | Create (the body's `client_id` must match the path; idempotent). |
| `GET /clients/{id}/agents/{agent_id}` | One agent with its configuration. |
| `PATCH /clients/{id}/agents/{agent_id}` | Edit; agents never move clients through this API (`409`). |
| `DELETE /clients/{id}/agents/{agent_id}` | Delete configuration and knowledge, keep conversations. `409` while it answers a channel. |
| `GET /clients/{id}/agents/{agent_id}/prompt` | What the model receives on every message, minus per-message knowledge. |

**Knowledge** (list `agents.read`, writes `agents.knowledge`)

| Method & path | What it does |
| --- | --- |
| `GET /clients/{id}/agents/{agent_id}/documents` | Uploaded PDFs with status, size and index state. |
| `POST /clients/{id}/agents/{agent_id}/documents` | Multipart `file` (PDF only, 20 MB). Best-effort indexing, never a failed upload. |
| `DELETE /clients/{id}/agents/{agent_id}/documents/{document_id}` | Delete with its chunks. |
| `POST /clients/{id}/agents/{agent_id}/documents/reindex` | Re-embed everything with the agent's current model (`502` without a working key). |
| `GET /clients/{id}/agents/{agent_id}/qa` | Q&A pairs. |
| `POST /clients/{id}/agents/{agent_id}/qa` | Add a pair (idempotent). |
| `DELETE /clients/{id}/agents/{agent_id}/qa/{qa_id}` | Delete a pair. |

**Channels** (`channels.read`)

| Method & path | What it does |
| --- | --- |
| `GET /clients/{id}/channels` | Every line in one place — QR and API WhatsApp numbers, Instagram/Messenger accounts, web chat — normalized to `type/label/status/connected`. Read-only: connecting a line stays a panel gesture. |

**Calendar** (read `calendar.read`, members `calendar.manage`; events live in Google, fetched live)

| Method & path | What it does |
| --- | --- |
| `GET /clients/{id}/calendar` | Connection state and members with fresh links. |
| `GET /clients/{id}/calendar/events?start=&end=` | Events in the range (at most 62 days), with per-member `errors`. |
| `POST /clients/{id}/calendar/members` | Add a member (idempotent). |
| `DELETE /clients/{id}/calendar/members/{member_id}` | Remove a member. |

**Reports** (`reports.read`, all scoped to the client's numbers)

| Method & path | What it does |
| --- | --- |
| `GET /clients/{id}/reports/costs?from=&to=` | Totals plus by agent, model and day. Optional `agent_id` (must belong to the client), `model`, `tz`. |
| `GET /clients/{id}/reports/replies?from=&to=` | One line per reply, newest first, paginated. The CSV export stays in the panel. |
| `GET /clients/{id}/reports/operations?from=&to=` | Attendance totals, timing and volume. |

## Credential management (panel API, not versioned)

Integrations, tokens, OAuth clients and webhook subscriptions live on the panel API under `/api/integrations` — same cookie or a token holding `integrations.manage` / `webhooks.manage` — because credentials are managed by people, not by integrations:

| Method & path | What it does |
| --- | --- |
| `GET /api/integrations/scopes` | The scope catalogue with presets. |
| `GET /api/integrations`, `POST /api/integrations` | List / create (name, preset or scopes, optional client). |
| `PATCH /api/integrations/{id}`, `DELETE /api/integrations/{id}` | Edit / delete (tokens die with it). |
| `GET /api/integrations/{id}/tokens`, `POST /api/integrations/{id}/tokens` | List / issue (`expires_in_days` 1–1825, secret once). |
| `DELETE /api/integrations/{id}/tokens/{token_id}` | Revoke. |
| `POST /api/integrations/{id}/oauth-client` | Register/rotate the OAuth client (secret once). |
| `GET /api/integrations/{id}/webhooks`, `POST /api/integrations/{id}/webhooks` | List / subscribe (`url` https, `events`; secret once). |
| `DELETE /api/integrations/{id}/webhooks/{subscription_id}` | Unsubscribe; pending deliveries are dropped. |
| `GET /api/integrations/{id}/webhooks/{subscription_id}/deliveries?status_filter=` | The log: `pending`, `sent`, `failed`. |
| `POST /api/integrations/{id}/webhooks/{subscription_id}/deliveries/{delivery_id}/replay` | Requeue from scratch. |

## Webhooks

A subscription forwards `message.received`, `conversation.resolved` and `deal.moved` to your URL. Each delivery posts the event with `id`, `event`, `occurred_at` and `data`, and signs the raw body with HMAC-SHA256 in `X-Signature`:

```python
hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest() == request.headers["X-Signature"]
```

Delivery is at-least-once and unordered. A failure climbs the 5/15/15/60-minute ladder, then rests as `failed` in the log for a manual replay. Deleting or revoking the integration stops its deliveries. The UI lives on each integration's row (Settings → API integrations, or the client's API tab).

## Idempotency

Writes that create rows accept `Idempotency-Key: <uuid>`: the first request stores its answer for 24 hours, a retry with the same body replays it marked `"api_replay": true` instead of acting twice, the same key with another body is `422`, and a key still in flight is `409`. Keys are scoped per credential, so integrations never replay each other.

## Limits

- `page`/`limit` capped at 250 items; tokens get 7 requests per second (`429` with `retry_after`).
- PDF uploads: 20 MB; calendar event ranges: 62 days; reports clamp to a year.
- OAuth codes live 10 minutes, access tokens 1 hour, refresh tokens 30 days rotating.

## Next steps

- [api-coverage.md](../api-coverage.md) — every panel screen and the API route behind it, enforced by a test.
- [Inbox](inbox.md) — the panel side of the conversations above.
- [Reports](reports.md) — the panel side of costs, replies and operations.
