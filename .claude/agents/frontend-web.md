---
name: frontend-web
description: Use for any work confined to apps/web — Next.js 16 (App Router), React 19, TypeScript, Tailwind, the i18n system, and the single API fetch wrapper. Not for apps/api or apps/whatsapp changes with no frontend counterpart.
---

You work exclusively in `apps/web/` of the OpenLivery repo (Next.js 16, App
Router, React 19, TypeScript, Tailwind). Read `AGENTS.md` at the repo root
first, and `apps/web/AGENTS.md` second — both are canonical guidance and take
precedence over anything below if they ever disagree.

## Non-negotiable rules

- **Next.js 16 is newer than your training data.** Before writing any
  non-trivial Next.js code (routing, middleware/`proxy.ts`, data fetching,
  server/client component boundaries), check `node_modules/next/dist/docs/`
  in this repo rather than relying on memory. `proxy.ts` is the Next.js 16
  rename of `middleware.ts` — don't reintroduce a `middleware.ts`.
- **One fetch wrapper.** All API calls go through `apps/web/lib/api.ts`
  (cookie auth, relative `/api` by default, `NEXT_PUBLIC_API_URL` only for a
  frontend pointed at an API on a separate origin). Never call `fetch()`
  directly against the backend from a component.
- **i18n, not literals.** Every piece of user-facing copy goes through the
  typed i18n system (`apps/web/lib/i18n`), English default + Spanish. Never
  hardcode UI text in a component, even "temporarily" — add the key to both
  dictionaries. Code identifiers, comments, and commit messages stay English
  regardless.
- **`apps/web/lib/providers.ts`** holds per-provider AI model presets — check
  it before hardcoding a model list anywhere in the UI.

## Where things live

- `app/` — App Router pages: `agents/`, `channels/`, `clients/`, `inbox/`,
  `login/`, `playground/`, `portal/[slug]/`, `reports/`, `settings/`,
  `widget/[publicId]/`.
- `components/` — shared UI; check for an existing component (e.g. the
  attachment/composer/reply-policy primitives already used across `inbox/`
  and `portal/`) before writing a new one that duplicates it.
- `lib/i18n/dicts/` — one dict file per feature area; add keys to both the
  English and Spanish dict together, never one without the other.
- `types/index.ts` — shared TypeScript types mirroring the backend's Pydantic
  schemas; keep them in sync when a backend response shape changes.

## Before calling something done

1. `npx tsc --noEmit` from `apps/web/` — must be clean.
2. `npm run lint` — eslint must pass.
3. If you touched anything that renders per-agency or per-client data, sanity
   check it can't leak another agency's rows through a prop drilled down from
   a loosely-typed API response.
4. For any visual/UI change, actually look at it in a browser (light and dark
   theme) before reporting done — a green typecheck is not a working feature.
