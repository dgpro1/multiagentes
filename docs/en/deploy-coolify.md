# Deploy with Coolify on a Hetzner server

> Leer en español: [deploy-coolify.md](../es/deploy-coolify.md)

Coolify runs the stack from `docker-compose.coolify.yml`. Its proxy terminates TLS and reaches the app through the `proxy` service; nothing publishes a host port, secrets come from Coolify's environment variables, and a deploy with a missing secret stops before anything starts.

## Before you start

- **Server:** at least 4 GB of RAM and 2 vCPUs (for example a Hetzner CX22/CPX21). The compose sets memory limits (database 768 MB, API 1 GB, web 512 MB, Evolution 768 MB and small ones for the rest); raise them with `DB_MEMORY`, `API_MEMORY`, `WEB_MEMORY` and `EVOLUTION_MEMORY` on a bigger server.
- **Domain:** an `A` record (`app.example.com`) pointing at the server's IP. Client portals on their own domains are optional and need the same record plus `docs/en/client-portal.md`.
- **Images:** GitHub Actions ("Publish images", `.github/workflows/publish-images.yml`) builds `ghcr.io/<owner>/openlivery-api` and `-web` on every push to `main`, so the server never compiles. Check in the repository's **Actions** tab that the run is green and that both packages exist. If the packages are private, either make them public or run `docker login ghcr.io -u <user> -p <token with read:packages>` once on the server.
- If your images live under another owner, set `OPENLIVERY_IMAGE_PREFIX` (default `ghcr.io/dgpro1/openlivery`).

## Steps

1. **Generate the environment** on your computer and check it:
   ```bash
   ./scripts/generate-coolify-env.sh app.example.com > coolify.env
   python scripts/check-production-env.py coolify.env
   ```
   Keep `ENCRYPTION_KEY` in a password manager: it decrypts the stored AI keys and WhatsApp sessions and must never change. Delete `coolify.env` after pasting it.
2. **Create the resource** in Coolify: *New resource → Docker Compose → your Git repository*, branch **`production`**, compose file `/docker-compose.coolify.yml`. (`production` is created and advanced by GitHub only after the tests are green; see *Updating*.) (Use the repository, not "empty compose": the gateway reads `docker/Caddyfile` from it.)
3. **Environment variables:** open *Environment variables → Developer view* and paste the block. `FRONTEND_URL` must be the public `https` address.
4. **Domain:** on the `proxy` service set `https://app.example.com` (port 80 inside). Coolify issues the certificate. Leave every other service without a domain.
5. **Deploy.** Wait until every service is healthy. The API applies the database migrations on start.
6. **First run:** open the domain; the first-run setup creates the agency and its owner, then public registration closes for good.
7. **Optional services:**
   - *Google Calendar:* register `https://app.example.com/api/calendar/oauth/callback` as an authorized redirect URI in Google Cloud and fill `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
   - *Messaging channels (WhatsApp API, Instagram, Messenger):* fill `MESSAGING_PROVIDER_API_KEY`; the webhook address is built from `FRONTEND_URL`, so it must be public.

## Backups

The `db-backup` service writes a compressed dump of the main database every day into the `db_backups` volume (7 daily, 4 weekly, 6 monthly). A backup on the same server is not a backup: turn on Hetzner backups or snapshots and copy that volume off the server. To restore:
```bash
docker exec -i <db container> psql -U openlivery openlivery < dump.sql   # after gunzip
```
The uploaded files (`backend_storage`) and the WhatsApp sessions (`evolution_*` volumes) are also worth including in your server snapshots.

## Updating

Work lands on `main`, but the server never sees it directly. On every push to `main` GitHub runs *Tests*; only when they are green does *Publish images* build the images (tagged `latest` and `sha-<7 characters>`) and then advance the **`production`** branch to that commit. Coolify follows `production`, so with *Auto Deploy* on it only ever receives commits that passed the tests and have their images. A red run publishes nothing and `production` stays where it was. Check a commit with `python scripts/ci-status.py <sha>`.

Migrations run on start. Before a deploy that adds one (the changelog says so), take a Hetzner snapshot or make sure last night's `db-backup` dump exists.

## Rolling back

If a deploy misbehaves, go back in minutes: in Coolify set `OPENLIVERY_VERSION=sha-<first 7 characters of a good commit>` (the images of every green commit stay in the registry) and redeploy. If the bad version applied a migration, restore the snapshot or dump taken before it instead. Set the variable back to `latest` (or delete it) once a fixed commit is on `production`.

## Do not change

`ENCRYPTION_KEY` (stored secrets become unreadable) and `POSTGRES_PASSWORD` after the first deploy (the database keeps the first one; change it inside Postgres first). Changing `SECRET_KEY` only signs everyone out.

## If something fails

- **502 from the domain:** the `proxy` service must be the one holding the domain, on port 80; check that `web` and `api` show healthy.
- **A deploy stops with "Set ...":** a required variable is empty; the message names it.
- **Login works but cookies are lost:** `COOKIE_SECURE=true` needs the site to be served over `https`; check the domain's certificate.
- **The QR never appears:** the Evolution containers must be healthy; its database and Redis are internal and exclusive to it.
