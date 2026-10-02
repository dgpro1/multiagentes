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

Two services dump on a schedule, each into its own volume (7 daily, 4 weekly, 6 monthly):

| Service | Volume | What it holds |
| --- | --- | --- |
| `db-backup` | `db_backups` | The main database: platform, agencies, every client still central, knowledge PDFs, attachments, logos |
| `evolution-backup` | `evolution_backups` | Evolution API's database: the WhatsApp QR session and pairing state |

Both dump once when they start, so a fresh deploy has a backup immediately. The
image's own healthcheck fails when the schedule stalls, so an unhealthy container
in Coolify is the alarm that backups stopped — turn on Coolify notifications for it.

**A backup on the same server is not a backup.** Two things make it one:

```bash
# Copy the dumps off the machine; a Hetzner Storage Box works well (SFTP or WebDAV):
#   rclone config create hetzner sftp host u123456.your-storagebox.de user u123456
BACKUP_REMOTE=hetzner:openlivery ./scripts/backup-offsite.sh

# Then, in `crontab -e` on the server (outside Coolify, so a Coolify reinstall
# cannot take the off-site copy with it):
#   30 4 * * * cd /opt/openlivery && BACKUP_REMOTE=hetzner:openlivery ./scripts/backup-offsite.sh >> /var/log/openlivery-offsite.log 2>&1
#    0 9 * * * cd /opt/openlivery && ./scripts/backup-offsite.sh --check >> /var/log/openlivery-offsite.log 2>&1
```

`--check` exits non-zero when the newest dump is older than `BACKUP_MAX_AGE_HOURS`
(26 by default), so a backup that quietly stopped becomes a failed job you can be told
about. Use `BACKUP_RSYNC_TARGET=user@host:path` instead of `BACKUP_REMOTE` to sync with
rsync over SSH (port 23 on a Hetzner Storage Box, override with `BACKUP_SSH_PORT`).

**Rehearse the restore**, monthly and after every migration: a backup that was never
restored is a hope, not a backup.

```bash
make restore-test     # loads the newest dump into a throwaway container and counts rows
```

To restore by hand instead:
```bash
docker exec -i <db container> psql -U openlivery openlivery < dump.sql   # after gunzip
```

The uploaded files (`backend_storage`) and the WhatsApp session files
(`evolution_instances`) are not in these dumps: keep Hetzner snapshots on for them. A
real recovery also needs the original `ENCRYPTION_KEY` and `.env.docker` — see
`docs/en/self-hosting.md`, "Backups", for the full export and restore procedures.

## Updating

Work lands on `main`, but the server never sees it directly. On every push to `main` GitHub runs *Tests*; only when they are green does *Publish images* build the images (tagged `latest` and `sha-<7 characters>`) and then advance the **`production`** branch to that commit. Coolify follows `production`, so with *Auto Deploy* on it only ever receives commits that passed the tests and have their images. A red run publishes nothing and `production` stays where it was. Check a commit with `python scripts/ci-status.py <sha>`.

Migrations run on start. Before a deploy that adds one (the changelog says so), take a Hetzner snapshot or make sure last night's `db-backup` dump exists.

## Updating without cutting WhatsApp

WhatsApp QR sessions live in Evolution API (its own database and Redis), not in the API or the web. Updating those two does not touch them; restarting or recreating Evolution does.

- Keep Evolution as a service of its own in Coolify, with its version pinned (`evoapicloud/evolution-api:v2.3.7` today). Update it only on purpose, never as a side effect of redeploying the rest.
- Never delete or recreate the volumes of `evolution`, `evolution-db` or `evolution-redis`: the sessions are in them. Back them up like the main database.
- Deploy at a quiet hour. While the API restarts (seconds), an inbound message can be missed and a reply waiting for its quiet window can be lost.
- Let Coolify start the new container and wait for its health check before stopping the old one.
- Migrations only add (a column, a table); renaming or dropping goes in a later release, so the previous version keeps working if you roll back.
- Take a snapshot or dump before any deploy that carries a migration, and try the change on a copy of the data first when it moves data.
- Deploy only a commit whose `Tests` run is green (the `production` branch already guarantees it).

## Rolling back

If a deploy misbehaves, go back in minutes: in Coolify set `OPENLIVERY_VERSION=sha-<first 7 characters of a good commit>` (the images of every green commit stay in the registry) and redeploy. If the bad version applied a migration, restore the snapshot or dump taken before it instead. Set the variable back to `latest` (or delete it) once a fixed commit is on `production`.

## Do not change

`ENCRYPTION_KEY` (stored secrets become unreadable) and `POSTGRES_PASSWORD` after the first deploy (the database keeps the first one; change it inside Postgres first). Changing `SECRET_KEY` only signs everyone out.

## If something fails

- **502 from the domain:** the `proxy` service must be the one holding the domain, on port 80; check that `web` and `api` show healthy.
- **A deploy stops with "Set ...":** a required variable is empty; the message names it.
- **Login works but cookies are lost:** `COOKIE_SECURE=true` needs the site to be served over `https`; check the domain's certificate.
- **The QR never appears:** the Evolution containers must be healthy; its database and Redis are internal and exclusive to it.
