-- Tenant revision t0002: conversations.pinned_at, the lead pin added to the
-- central schema by migration 0079_conversation_pinned. A client's own
-- database needs its own copy, or a lead of that client would be missing the
-- column the inbox orders and filters on and the row would simply not load.

ALTER TABLE conversations ADD COLUMN pinned_at TIMESTAMP WITH TIME ZONE;

-- Partial, for the same reason as the central index: the inbox reads every
-- lead and only a few are pinned.
CREATE INDEX ix_conversations_pinned ON conversations (pinned_at) WHERE pinned_at IS NOT NULL;
