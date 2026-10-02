# Agency backend

> Leer en español: [agency-backend.md](../es/agency-backend.md)

By default a client's data lives in the central database of the installation and its files in a bucket the client connects. With the **agency backend** an agency can look after both for all its clients, in its own Supabase project and its own Cloudflare R2 bucket.

The platform owner switches it on per agency (module `agency_backend`, off by default, in the agency's Plan tab). Without it the agency sees none of this.

## Connect once

In **Settings → Agency backend**:

- **Supabase.** Create an access token in your Supabase account (Account → Access Tokens), paste it and pick the project from the list. If the platform owner registered a Supabase OAuth app, you can authorize through Supabase's consent screen instead. Either way HunterAI never needs the project's password: it creates a database role per client through the Management API. The token is stored encrypted and you can revoke it in Supabase whenever you like. Use a plan that does not pause the project for inactivity, and a region close to the HunterAI servers (Settings warns when it is not).
- **Cloudflare R2.** Paste one Cloudflare API token with the permissions Workers R2 Storage: Edit and API Tokens: Edit. HunterAI creates the bucket and a second key that can use only that bucket; the token you paste is used once and not kept. If you prefer, enter the account ID, a bucket and an S3 API token by hand. Either way the bucket is probed with a write, a read and a delete before anything is kept, and the secret is never shown again.

## New clients start here

Once the agency has connected its Supabase project, every client it creates (from the panel or the API) starts in that project, and once it has connected its bucket, its files start in that bucket: whichever of the two is ready is used. Clients that already existed stay where they are until you move them. Creating a client never fails because of this: if Supabase or the bucket does not answer, the client starts in HunterAI as before and the reason shows on its Database tab. The home page lists "Connect your backend" as the first step while the module is on and nothing is connected yet.

## Choose where a client lives

On the client's **Database** tab there are three places for its data:

| Place | What it is |
|---|---|
| **HunterAI** | The central database of the installation. Where every client starts. |
| **Your agency** | A schema of your Supabase project, with a database role that can use that schema and nothing else, so one client's connection can never read another's rows. |
| **The client's own** | The client's Supabase project, connected with its consent. |

Moving copies every table to the destination and checks the row counts table by table before anything changes. The client's channels pause for a few seconds; what arrives meanwhile is kept and processed right after. A failure leaves the client exactly where it was. Moving between two places that are not HunterAI goes through it, in one request.

When a client leaves your project, a **safety copy** of its data stays in its schema (the tab shows since when) until you drop it.

## Files

A client's files can be in its own bucket or in the agency's, under a folder of its own (every object key already starts with the agency and client ids). Moving them copies every object, checks it by size, and only then switches. The originals stay in the bucket they came from. Moving into the agency's bucket needs the module; taking files out never does.

Files follow the data to and from the agency's bucket when you choose a place, and the tab has a separate control for them in case that second step needs a retry.

## Turning the module off

Nobody is cut off. Clients already in the agency's project or bucket keep working and can be moved out; only new moves in are refused. The platform panel lists who is inside before it turns the module off.

## For the platform owner

In **Infrastructure** the platform sees where every client's data and files live and can move them itself. Every move is recorded in the audit log with where it came from, where it went and how many rows or files.
