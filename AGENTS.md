# AGENTS.md

Guidance for coding agents working in this repository. `AGENTS.md` is the file
agents look for; `CLAUDE.md` is a one-line pointer to it, the same pairing this
repo already uses in `apps/web`.

## Project

One OpenLivery installation serves one agency, which creates and manages AI agents for multiple clients, with a chat playground, client portals, and messaging integrations. First-run setup creates the agency and its owner, then public registration closes permanently. Do not add a setting, endpoint, or UI flow for registering additional agencies. Three services + PostgreSQL:

- `apps/api/` — FastAPI (Python 3.12) + SQLAlchemy + Alembic
- `apps/web/` — Next.js 16 (App Router) + React 19 + TypeScript + Tailwind

WhatsApp QR lines run through a self-hosted Evolution API instance (`docker-compose.yml` brings it up alongside the app); there is no bundled bridge service.

**Language convention (always follow):** all code — routes, identifiers, comments, commit messages, and docs — is written in English, always. The only thing that is localized is the end-user UI, through a typed i18n system (`apps/web/lib/i18n`): English (default) and Spanish for now. Never introduce non-English in code or docs; put user-facing copy behind i18n keys instead. (The system prompt sent to the LLM in `apps/api/app/services/knowledge.py` is a deliberate exception, kept in the customer's language.)

## Repo hygiene

Enable the pre-commit guard once per clone: `git config core.hooksPath .githooks`. It blocks committing local-only files (`work/`, `internal/`, `*.local.md`) and any staged content matching terms in `work/forbidden-words.txt` (gitignored) or a commit-blocking marker (spelled out in `.githooks/pre-commit`; this file cannot quote it without tripping the guard it describes). Keep internal notes/roadmap in `work/` (gitignored) — never in the repo.

## Commands

### Docker (recommended)

A `Makefile` wraps compose: `make up` builds and starts everything; also `make down/logs/migrate/test/help`. Override host ports inline to avoid clashes: `API_PORT=8001 WEB_PORT=3001 make up` (`API_PORT`/`WEB_PORT`/`DB_PORT`/`BIND_HOST`).

```bash
./scripts/generate-docker-env.sh                       # create .env.docker with random secrets
docker compose --env-file .env.docker up --build -d
docker compose --env-file .env.docker logs -f api  # or: web, evolution, db
docker compose --env-file .env.docker exec api pytest -q
```

### Local

```bash
# Backend (needs PostgreSQL and a .env, see .env.example)
cd apps/api
pip install -r requirements.txt
alembic upgrade head                 # migrations must run before starting
uvicorn app.main:app --reload --port 8000

# Frontend
cd apps/web && npm install && npm run dev    # http://localhost:3000
npm run lint                                 # eslint
npm run build
```

WhatsApp QR lines need a running Evolution API instance (`docker compose up evolution evolution-db evolution-redis` covers it locally, or point `EVOLUTION_API_URL`/`EVOLUTION_API_KEY` at any instance) — other channels and the rest of the app work without it.

### Backend tests

Tests need a separate `openlivery_test` database (default URL in `apps/api/tests/conftest.py`, override with `TEST_DATABASE_URL`). Tables are created/dropped per test — never point it at the dev DB.

```bash
cd apps/api
pytest -q
pytest tests/test_flows.py::test_register_login_logout_and_me -v   # single test
```

## Architecture

### Data model (apps/api/app/models.py)

Everything is agency-scoped: `Agency → Users, Clients, AIConnections`; `Client → Agents, WhatsAppChannel, Conversations`; `Agent → Conversations, KnowledgeDocuments`; `Conversation → Messages`. Every router query filters by the authenticated user's `agency_id`. Preserve this ownership boundary in every new endpoint, including for existing data created by older releases.

### Backend layout

- `app/database.py` — the engine and the one place a session is created. Routes receive one through `get_db`, which FastAPI lets a deployment substitute (the test suite does); anything running outside a request calls `new_session()`. Never call `SessionLocal()` elsewhere: it opts that code out of the substitution, so a swapped session never reaches it, and the failure surfaces inside a background task rather than in a response. `tests/test_session_factory.py` enforces this.
- `app/main.py` — app creation, CORS, router registration
- `app/routers/` — one file per domain (auth, agency, clients, agents, connections, conversations, dashboard, portal, whatsapp); `domains.py` holds the public, unauthenticated `/api/public/portal-domain` used by the frontend `proxy.ts` and the gateway's on-demand-TLS `ask` hook to map a client's custom domain to its portal
- `app/services/ai.py` — `chat_completion()` calls `{base_url}/chat/completions` (OpenRouter; models are `vendor/model` slugs) and reads token counts and cost from the usage block; key testing asks `{base_url}/key`
- `app/services/knowledge.py` — PDF text (pypdf on upload) is chunked and embedded; retrieval is semantic (cosine over embeddings stored as JSON) with keyword ranking as a fallback, then assembled into the system prompt
- `app/security.py` — JWT in httpOnly cookies; AI API keys and WhatsApp session state are encrypted with a key derived from `ENCRYPTION_KEY` before hitting the DB
- `app/ratelimit.py` — per-IP in-memory limiter used as a route dependency on public/unauthenticated endpoints (auth + portal login, widget messages); reads the client from `X-Forwarded-For` (set by the gateway); toggle with `RATE_LIMIT_ENABLED` (disabled in tests)
- `migrations/` — Alembic; schema changes require a new migration, and Docker runs `alembic upgrade head` on backend start

### WhatsApp flow

WhatsApp QR lines run through a self-hosted Evolution API instance (`app/services/evolution.py`, one deterministic instance per line named `openlivery-{channel_id}`, events delivered to `POST /api/public/whatsapp/evolution/webhook`). `evolution.enabled()` is true whenever `evolution_api_url`+`evolution_api_key` are configured; without them, WhatsApp QR is simply unavailable (WhatsApp API/Cloud and social channels are unaffected). The webhook handler processes inbound messages, phone-mirrored (`fromMe`) messages, and connection-state changes in-process — nothing calls back into the API over HTTP the way a separate driver process would.

Incoming messages feed the shared pipeline in `app/services/whatsapp_inbound.py`, which waits for a quiet window that restarts on each new visitor message, then answers the whole burst with one reply delivered via `send_channel_message()` (replies are delayed per agent — `reply_delay_min_seconds` / `reply_delay_max_seconds`, a random wait between the two, 6 to 9s by default); with both bounds at 0 the reply returns synchronously instead. Conversations have a `mode` field: switching to `"human"` pauses the AI so an operator answers from the portal.

`app/routers/whatsapp.py`'s `internal_router` (under `/internal/whatsapp`, gated by `WHATSAPP_BRIDGE_TOKEN` via the `X-Bridge-Token` header) is a driver-agnostic internal API the test suite uses to simulate inbound messages, reactions, and delivery confirmations directly over HTTP rather than through a real Evolution webhook payload; nothing in production calls it today.

### Frontend

`apps/web/lib/api.ts` is the single fetch wrapper (cookie auth, `NEXT_PUBLIC_API_URL`); `apps/web/lib/providers.ts` holds per-provider model presets. `apps/web/AGENTS.md` warns that Next.js 16 has breaking changes vs. training data — check `node_modules/next/dist/docs/` before writing non-trivial Next.js code.

### Subagents

`.claude/agents/` holds domain-scoped subagents for the Claude Code `Agent`
tool: `backend-api` (apps/api only) and `frontend-web` (apps/web only). Each
file carries its own non-negotiable rules pulled from this document, so a
session can delegate a clearly single-domain task to one of them directly, or
run both in parallel once an API contract is settled. Prefer the general
session for anything that spans domains or needs judgment about scope.

## Environment gotchas

- `ENCRYPTION_KEY` must never change after secrets are stored — it decrypts AI API keys and WhatsApp sessions.
- The app is served single-origin through a Caddy gateway (`docker/Caddyfile`): `/api/*` → backend, everything else → frontend. The browser uses relative `/api` (`lib/api.ts` falls back to `""`), so `NEXT_PUBLIC_API_URL` is empty by default and only set to point the frontend at an API on a separate origin (baked at build time — rebuild the web image to change it).
- TLS is operator-provided: put your own reverse proxy in front of the gateway port; the stack itself only serves plain HTTP. No bundled TLS/`make deploy`.
- Custom per-client portal domains are opt-in: mount `docker/Caddyfile.ondemand` (on-demand TLS gated by `/api/public/portal-domain`) via a compose override; `apps/web/proxy.ts` (Next.js 16 renamed `middleware`→`proxy`) rewrites a verified custom host to `/portal/[slug]`. `BACKEND_INTERNAL_URL` lets the web container reach the API server-side.
- Ports: gateway `WEB_PORT` (default 3000, the app), backend 8000 (OpenAPI docs at `/docs`, exposed locally for tooling); Evolution API and its Postgres/Redis are not exposed outside the Docker network.
