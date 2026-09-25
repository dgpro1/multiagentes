# Inbox

> Leer en español: [inbox.md](../es/inbox.md)

The Inbox is a single place to watch every conversation your agents are having, search across them, and step in as a human when the AI needs help. It pulls together conversations from every channel — the playground, [WhatsApp](whatsapp.md) and the [web widget](web-widget.md) — into one list.

## Unified list

Every conversation shows the contact name (or title), a preview of the latest message, the agent that owns it, the channel it came from, and a badge marking whether it is in **AI** or **human** mode. The list is scoped to your agency, so operators only see their own tenant's conversations.

Two filters at the top narrow the view: by **agent** and by **channel** (playground, WhatsApp or widget). These combine with the tabs and search below.

## Search and tabs

A search box runs **server-side**: it matches the conversation title, the contact name, and the content of the latest message. Input is debounced, so results update shortly after you stop typing.

Four tabs filter the list:

- **All** — every conversation.
- **Unread** — conversations whose latest message is from the visitor and hasn't been read since.
- **Human** — conversations currently in human mode.
- **AI** — conversations currently handled by the agent.

## Unread tracking and pagination

Unread is derived from an `operator_read_at` timestamp: a conversation counts as unread when its last message came from the visitor and arrived after you last opened it. Opening a conversation marks it read. The first page refreshes automatically every few seconds so new messages surface without a manual reload. The list loads in pages of 30 and fetches more as you scroll toward the bottom.

## Human takeover

Each conversation carries a `mode` field. In **AI** mode the agent answers automatically. Use **Take control** to switch the conversation to **human** mode: this pauses the AI so an operator can reply in its place. While a conversation is in human mode the AI will not generate replies — attempts to do so are rejected until you hand it back. When you're done, **Return to AI** flips the mode back and the agent resumes.

This is the same `mode` concept used for [WhatsApp](whatsapp.md) conversations, so taking over works consistently regardless of channel.

## Who can take over

Both agency operators (from this Inbox) and client users can take over conversations. Client users do it from the [client portal](client-portal.md), which exposes the same take-control and reply-as-human actions scoped to their client.

## Lead card

Next to a conversation, the lead card shows it as a sales lead (a lead is a conversation): the lead number, the contact block (name, WhatsApp name, phone, e-mail, company, tags, blocked), the pipeline stage, the budget in the client's currency, the responsible person and the client's custom fields. The stage and the budget are changed from the pipeline board (see the [client portal](client-portal.md)); the card only shows them.

- **Responsible** is a label, not an assignment: choosing someone never changes who answers or the AI/human mode. When nobody is chosen it shows the client's **Responsible / director**, set in the client's Details (agency client page or the portal's Details screen). A chosen person who is deactivated falls back to that default.
- **Currency** is also set in Details (USD, EUR, MXN, COP, CLP, ARS, PEN, BRL, UYU, BOB, PYG, DOP, CRC, GTQ or PAB) and applies to every budget of the client.
- **Custom fields** are defined per client (up to 30) as text, number, date, select or checkbox. A field's key and type are fixed once created; deleting a field hides its values without erasing them.

Anyone with the inbox can choose the responsible and fill the fields on a lead. Only portal admins (permission `fields.manage`) and the agency can create, rename, reorder or delete the fields. Agency operators use the same card and the same routes under `/api/conversations/{id}/lead` and `/api/clients/{id}/lead-fields`.


## Merging leads

When the same person writes from two places (WhatsApp and Instagram, or a new number), the two leads can be merged into one. From the lead card choose **Merge**, search for the other lead by name, phone, e-mail or number (`#123`) and confirm. The lead you are in stays as the **primary**; the other one is the **secondary**. Merging is final: it cannot be undone.

- **Budget:** the primary keeps its own. Only when it has none (or 0) does it take the secondary's.
- **Custom fields:** the primary's values are never overwritten; the fields it left empty are filled from the secondary. The same goes for the responsible person, the assignee and the team.
- **Contact:** two different contacts are merged into the primary's (identities, tags and company included); if only the secondary has a contact, the primary adopts it. Tags live on the contact, so they are combined.
- **Conversation:** nothing is deleted. The secondary keeps its channel and its messages and keeps receiving what that channel delivers, but it now belongs to the primary: the inbox shows one lead with the messages of every channel in one timeline, each marked with its channel, and you choose which channel a reply goes out on. Taking control, assigning, moving to a team, resolving and archiving act on the whole lead. If the contact writes again on the secondary's channel, the primary reopens instead of a new lead being created.
- **Number:** the secondary's number becomes an alias of the primary: opening `#<secondary number>` opens the primary.
- **History:** the primary's timeline records the merge with what the secondary had (its budget and custom fields), so nothing is lost.

In the client portal, merging needs the `contacts.manage` permission (portal admins) and the inbox function; searching for a lead to merge only needs the inbox. Agency operators use `GET /api/clients/{id}/leads/merge-candidates` and `POST /api/clients/{id}/leads/merge`; the portal serves the same at `/api/portal/{slug}/leads/...`. Replies accept an optional `via_conversation_id` to choose the channel of a merged lead.
