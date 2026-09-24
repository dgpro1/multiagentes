"use client";

import { UserRound } from "lucide-react";
import { ChannelIcon, channelLabel } from "@/lib/channels";
import { useT } from "@/lib/i18n";

/** The contact's avatar in a thread header: the same look as in the list rows
 * (avatar with its channel badge), and the control that shows or hides the lead card. */
export function LeadAvatarButton({ channel, open, onClick }: { channel: string; open: boolean; onClick: () => void }) {
  const t = useT();
  return <button type="button" className="inbox-avatar lead-avatar-btn" onClick={onClick} aria-pressed={open} aria-expanded={open} title={t("lead.toggle")} aria-label={t("lead.toggle")}>
    <span className="entity-avatar tiny"><UserRound size={15} /></span>
    <span className={`channel-badge ${channel}`} title={channelLabel(channel, t)}><ChannelIcon channel={channel} /></span>
  </button>;
}
