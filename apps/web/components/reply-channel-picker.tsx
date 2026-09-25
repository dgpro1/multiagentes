"use client";

import { ChannelIcon, threadName } from "@/lib/channels";
import { useT } from "@/lib/i18n";
import type { LinkedThread } from "@/types";

/** "Reply via [channel]": which of a merged lead's threads the next reply goes through. */
export function ReplyChannelPicker({ threads, value, onChange }: { threads: LinkedThread[]; value: string; onChange: (conversationId: string) => void }) {
  const t = useT();
  const current = threads.find((thread) => thread.conversation_id === value) ?? threads[0];
  return <div className="channel-picker-head">
    <span>{t("lead.replyVia")}</span>
    {current && <span className={`channel-dot ${current.channel}`}><ChannelIcon channel={current.channel} /></span>}
    <select value={current?.conversation_id ?? ""} onChange={(event) => onChange(event.target.value)} aria-label={t("lead.replyVia")}>
      {threads.map((thread) => <option key={thread.conversation_id} value={thread.conversation_id}>{threadName(thread, t)}</option>)}
    </select>
  </div>;
}
