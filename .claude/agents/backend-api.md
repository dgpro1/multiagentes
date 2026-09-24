---
name: backend-api
description: Use for any work confined to apps/api — FastAPI routers, SQLAlchemy models, Alembic migrations, the app/services/ layer (ai.py, knowledge.py, whatsapp_inbound.py, security.py), and backend tests (pytest against openlivery_test). Not for apps/web changes with no backend counterpart.
---

You work exclusively in `apps/api/` of the OpenLivery repo (FastAPI + SQLAlchemy +
Alembic, Python 3.12). Read `AGENTS.md` at the repo root first — it is the
canonical guidance and takes precedence over anything below if they ever
disagree.

## Non-negotiable rules

- **Agency scoping.** Every router query filters by the authenticated user's
  `agency_id`. `Agency → Users, Clients, AIConnections`; `Client → Agents,
  WhatsAppChannel, Conversations`; `Agent → Conversations, KnowledgeDocuments`;
  `Conversation → Messages`. Never write a query that can return another
  agency's rows, including for data created by older releases.
- **Sessions.** Never call `SessionLocal()` outside `app/database.py`. Routes
  take a session through `get_db` (which the test suite substitutes); code
  running outside a request calls `new_session()`. Calling `SessionLocal()`
  elsewhere opts that code out of the test substitution and the failure
  surfaces inside a background task instead of a response. Enforced by
  `tests/test_session_factory.py`.
- **Secrets.** AI API keys and WhatsApp session state are encrypted with a key
  derived from `ENCRYPTION_KEY` (`app/security.py`) before hitting the DB.
  Never log or return a decrypted secret. Never assume `ENCRYPTION_KEY` can
  change after secrets exist — it can't, they'd become undecryptable.
- **Migrations.** Any schema change ships its own Alembic migration in
  `migrations/versions/`. Docker runs `alembic upgrade head` on backend start,
  so migrations must be forward-only and safe to run against live data.
- **English only.** Routes, identifiers, comments, commit messages — always
  English. User-facing copy never lives in the backend; it goes through the
  frontend's i18n system.
- **No second agency.** First-run setup creates the one agency this
  installation serves; public registration closes permanently after. Never
  add a setting, endpoint, or flow for registering another agency.

## Where things live

- `app/routers/` — one file per domain (auth, agency, clients, agents,
  connections, conversations, dashboard, portal, whatsapp). Public/
  unauthenticated endpoints live under `/api/public/...` and go through
  `app/ratelimit.py`.
- `app/services/` — business logic the routers call into. `ai.py`
  (`chat_completion()` against OpenRouter, `vendor/model` slugs), `knowledge.py`
  (PDF chunking/embedding, semantic + keyword retrieval), `whatsapp_inbound.py`
  (shared inbound pipeline: reply-delay quiet-window batching, fed by the
  Evolution API webhook and the messaging provider — see the "WhatsApp flow"
  section of the root `AGENTS.md`).
- `migrations/` — Alembic; check the most recent few files for the current
  naming/review convention before writing a new one.
- `tests/` — pytest against a separate `openlivery_test` database (see
  `tests/conftest.py`, override with `TEST_DATABASE_URL`); tables are
  created/dropped per test. Never point it at the dev DB. Run with
  `cd apps/api && pytest -q`.

## Before calling something done

1. `pytest -q` from `apps/api/` — full suite, not just the file you touched;
   `app/routers/` and `app/services/` are densely cross-tested.
2. If you touched a model, confirm a migration exists and its upgrade/downgrade
   are both correct.
3. If you touched anything auth- or secret-adjacent, re-check the agency-scoping
   and encryption rules above explicitly — they're the two things this
   codebase cannot tolerate a regression in.
