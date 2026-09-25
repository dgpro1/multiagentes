import { useCallback, useMemo, useState } from "react";
import type { Conversation, LinkedThread } from "@/types";

// A lead that absorbed others (a merge) shows one thread made of several channel
// threads. These helpers read that shape from a conversation detail, and keep
// working when the API does not send it (an older response is one thread).

/** The lead's channel threads, the primary first; a lone thread when the response has none. */
export function threadsOf(conversation: Conversation | null): LinkedThread[] {
  if (!conversation) return [];
  if (conversation.linked_threads?.length) return conversation.linked_threads;
  return [{
    conversation_id: conversation.id,
    channel: conversation.channel,
    label: null,
    account_label: conversation.account_label ?? null,
    is_primary: true,
    mode: conversation.mode,
    last_inbound_at: conversation.last_inbound_at ?? null,
  }];
}

/** Which of the lead's threads a reply goes through: `reply_via_default` until the operator picks another. */
export function useReplyVia(conversation: Conversation | null) {
  const threads = useMemo(() => threadsOf(conversation), [conversation]);
  // The pick is kept with the lead it was made on, so opening another lead falls back to its default.
  const [choice, setChoice] = useState<{ lead: string; via: string } | null>(null);
  const multi = threads.length > 1;
  const known = (id?: string | null) => Boolean(id) && threads.some((thread) => thread.conversation_id === id);
  const via = choice && conversation && choice.lead === conversation.id && known(choice.via)
    ? choice.via
    : known(conversation?.reply_via_default) ? conversation!.reply_via_default! : conversation?.id ?? "";
  const thread = threads.find((item) => item.conversation_id === via) ?? threads[0] ?? null;
  // The detail's reply-window fields describe the primary thread. Another thread has its own window
  // that the API enforces on send, so the composer is not locked by the primary's window for it.
  const policyConversation = useMemo<Conversation | null>(() => {
    if (!conversation || !thread || thread.is_primary) return conversation;
    return { ...conversation, channel: thread.channel, reply_window_open: true, reply_window_until: null, human_reply_window_open: false, human_reply_window_until: null, reply_block_reason: null };
  }, [conversation, thread]);
  const lead = conversation?.id;
  const setVia = useCallback((id: string) => { if (lead) setChoice({ lead, via: id }); }, [lead]);
  return {
    threads,
    /** More than one thread: the composer offers the choice and every reply names its thread. */
    multi,
    via,
    thread,
    /** What useReplyPolicy should judge: the lead itself, or a stand-in for another thread. */
    policyConversation,
    setVia,
    /** The field every reply route accepts; empty for a lead with a single thread. */
    payload: (multi ? { via_conversation_id: via } : {}) as { via_conversation_id?: string },
  };
}

/** Which thread each attachment belongs to: its file is served under that thread, not under the lead's primary. */
export function useAttachmentOwners(conversation: Conversation | null): Map<string, string> {
  return useMemo(() => {
    const owners = new Map<string, string>();
    for (const message of conversation?.messages ?? []) {
      if (!message.conversation_id) continue;
      for (const attachment of message.attachments ?? []) owners.set(attachment.id, message.conversation_id);
    }
    return owners;
  }, [conversation]);
}
