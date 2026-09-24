<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## URLs: every screen and every lead has its own address

The URL is the source of truth for what is on screen, the way Kommo works: a link can be shared, opened in a new tab and bookmarked, and reload, Back and Forward keep you where you were. Never hold the current screen, tab or open lead only in `useState`.

- The scheme lives in `lib/routes.ts` (build and read paths with its helpers; do not hand-write paths). Portal: `/portal/{slug}/{inbox|contacts|calendar|pipeline|reports|settings}`, `/inbox/{number}` for a lead, `/contacts/{id}`, `/settings/{tab}`. Agency: `/clients/{id}[/{tab}]`, `/clients/{id}/inbox/{number}`, `/agents/{id}[/{tab}]`.
- A lead is identified by its short `number` inside its client (`Conversation.number`, unique per client), never by the UUID, in any address a person sees.
- Screens are `<Link href>`, not click handlers. Filters live in the query string (`?source=&state=&q=`) and are written with `history.replaceState`.
- A client's own domain serves the portal from the root (`proxy.ts` rewrites it to `/portal/{slug}` keeping the path), so links use `portalBase(slug)`, not a hard-coded `/portal/{slug}`.
- Old links (`?conversation=<id>`, `?tab=`) keep redirecting to the new address.
