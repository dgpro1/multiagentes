# WhatsApp

> Leer en español: [whatsapp.md](../es/whatsapp.md)

OpenLivery connects a real WhatsApp number to each client so their agent answers conversations automatically. Each client gets its own session, linked by scanning a QR code from the WhatsApp mobile app — no WhatsApp Business API account required.

## The Evolution API driver

WhatsApp QR lines run through a self-hosted [Evolution API](https://docs.evolutionfoundation.com.br) instance (Node, built on [Baileys](https://github.com/WhiskeySockets/Baileys)), started by Docker Compose alongside the rest of the app. OpenLivery creates one deterministic Evolution instance per line (named `openlivery-{channel_id}`), each with its own event webhook, so pairing, reconnects, inbound messages and mirrored phone messages all flow through the same pipeline.

Evolution keeps sessions in its own database (its own PostgreSQL and Redis, brought up alongside the app; not exposed outside the Docker network). The backend keeps a small encrypted marker per channel. WhatsApp QR needs `EVOLUTION_API_URL` and `EVOLUTION_API_KEY` set — see [Configuration](configuration.md); without them, WhatsApp QR is simply unavailable (other channels are unaffected).

## Connect a number

A WhatsApp session belongs to one client, and a client can link several numbers, each answered by the agent you pick (the same agent may answer more than one). To connect one:

1. Open a **client**, go to its **WhatsApp** channel, and pick the agent that should answer incoming messages.
2. Click connect. The backend creates (or reuses) the line's Evolution instance and requests a **QR code**.
3. On the phone that owns the number, open WhatsApp and go to **Settings → Linked devices → Link a device**.
4. Scan the QR code. Once the phone confirms, the channel switches to **connected** and shows the linked number.

The session survives restarts from then on. If the number is unlinked from the phone (or the session is invalidated), the channel returns to disconnected. You can also disconnect from the same page, which logs the device out (the Evolution instance itself is only removed when you delete the line).

## How messages flow

When a contact writes to the number, Evolution posts the event to `POST /api/public/whatsapp/evolution/webhook`, which:

1. Records the message on the client's conversation, retrieves the agent's knowledge, and generates a reply with the assigned agent.
2. Sends the reply back through the same Evolution instance to the contact on WhatsApp.

Images, voice notes, documents and stickers are forwarded too; when the agent has image or audio understanding enabled, images and audio are described or transcribed before reaching the model. See [Knowledge base](knowledge-base.md). A line's panel also has per-line toggles for **groups** (each group becomes its own conversation; the agent answers only when mentioned or replied to) and **calls** (always declined, with an optional explanation message). Locations work both ways: an incoming pin shows as a map card, and an operator can send one from the Inbox composer.

## Human takeover

Every conversation has a `mode`, either `ai` (the default) or `human`. When you switch a conversation to human mode, the AI stops replying to it — the backend still records incoming messages, but generates no automatic answer — so a person can take over and respond directly from the [inbox](inbox.md) or the [client portal](client-portal.md). Switch back to `ai` to hand the conversation to the agent again. Replying from the linked phone itself is mirrored into the conversation the same way.

## Other channels

See [WhatsApp Cloud API](whatsapp-cloud-api.md) for the official Meta API alternative, and the [web widget](web-widget.md) for a channel that needs no phone number at all. Instagram and Facebook Messenger are on the roadmap.

Next: manage live conversations in the [inbox](inbox.md), or let clients handle their own in the [client portal](client-portal.md).
