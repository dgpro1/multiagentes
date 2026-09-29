"use client";

import type { ReactNode } from "react";
import { UserRound } from "lucide-react";
import { ChannelIcon, channelLabel } from "@/lib/channels";
import { useT } from "@/lib/i18n";

/** The contact's avatar in a thread header: the same look as in the list rows
 * (avatar with its channel badge). It is decoration, not a control: the whole
 * header opens the lead card, and a button inside it would be a button inside
 * a button. */
export function LeadAvatar({ channel }: { channel: string }) {
  const t = useT();
  return (
    <span className="inbox-avatar lead-avatar">
      <span className="entity-avatar tiny"><UserRound size={15} /></span>
      <span className={`channel-badge ${channel}`} title={channelLabel(channel, t)}><ChannelIcon channel={channel} /></span>
    </span>
  );
}

/** The zone of a thread header that shows or hides the lead card.
 *
 * Everything the reference makes clickable lives here: the avatar, the contact's
 * name and the channel line, plus the gap between them. It is a `div` with
 * `role="button"` rather than a `<button>`, because the header also holds the
 * team and assignee pickers, and those are real controls that must stay outside
 * this zone and keep their own place in the tab order. A `button` element here
 * would either swallow them or nest them illegally.
 *
 * `children` is everything drawn inside the zone, so a caller cannot put a
 * control in it by accident. */
export function LeadHeaderButton({
  channel,
  open,
  onClick,
  children,
}: {
  channel: string;
  open: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  const t = useT();
  return (
    <div
      className="lead-head-open"
      role="button"
      tabIndex={0}
      aria-expanded={open}
      title={t("lead.toggle")}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onClick();
        }
      }}
    >
      <LeadAvatar channel={channel} />
      {children}
    </div>
  );
}
