import type { ReactNode, RefObject } from "react";
import { Lock, Reply, SmilePlus } from "lucide-react";
import { MessageAttachments, type GalleryImage } from "@/components/attachments";
import { AppointmentActivityCard, isAppointmentActivity } from "@/components/appointment-activity-card";
import { DeliveryTicks } from "@/components/delivery-ticks";
import { MergeAuditCard, isMergeActivity } from "@/components/merge-audit-card";
import { QuotedSnippet, ReactionBadge } from "@/components/message-gestures";
import { RichText } from "@/components/rich-text";
import { activityText } from "@/lib/activity";
import { MessageChannelMark, isSocialChannel } from "@/lib/channels";
import { formatTime } from "@/lib/datetime";
import type { TranslateFn } from "@/lib/i18n";
import type { Attachment, Message } from "@/types";

/** Where a thread is being read. The agency's inbox, the client's inbox in the
 * agency panel and the client portal draw the same entries the same way; the
 * contacts preview is a read-only thread of one contact. */
export type MessageThreadSurface = "agency" | "portal" | "preview";

/** How each surface draws a thread. They are not identical today and this step
 * does not make them so: the agency's inbox has no activity line, the preview
 * shows no internal note and names the AI. Everything a bubble needs to know
 * about where it is lives here, so the next change to a message touches this
 * file once instead of four. */
const SURFACES: Record<MessageThreadSurface, {
  /** The scroll container the thread lives in (see globals.css). */
  container: string;
  /** The element a bubble is. The agency styles its own `.inbox-message`. */
  element: "div" | "article";
  /** The bubble's class list, which is also how the stylesheet finds it. */
  bubble: (role: string, mine: boolean, ai: boolean, grouped: boolean) => string;
  /** An activity entry is a centred line of text, not a bubble. */
  activity: boolean;
  /** An internal note is a card of its own. */
  notes: boolean;
  /** Names the AI next to the sender, the way the preview does. */
  aiTag: boolean;
  /** Spells out why a delivery failed, under the bubble. */
  deliveryError: boolean;
  /** Keeps an activity line from swallowing the entry above it in a group. */
  groupedGuard: boolean;
  /** Whether an outgoing message shows its delivery ticks, and for which channel. */
  ticks: (message: Message, channel?: string) => boolean;
}> = {
  agency: {
    container: "inbox-messages",
    element: "div",
    bubble: (role, _mine, _ai, grouped) => `inbox-message ${role}${grouped ? " grouped" : ""}`,
    activity: false,
    notes: true,
    aiTag: false,
    deliveryError: false,
    groupedGuard: false,
    ticks: (message, channel) => message.role === "assistant" && isSocialChannel(message.channel ?? channel),
  },
  portal: {
    container: "portal-messages",
    element: "article",
    bubble: (role, mine, ai, grouped) => `${role}${mine ? " mine" : ""}${mine && ai ? " ai" : ""}${grouped ? " grouped" : ""}`,
    activity: true,
    notes: true,
    aiTag: false,
    deliveryError: true,
    groupedGuard: true,
    ticks: (message, channel) => {
      const from = message.channel ?? channel;
      return message.role === "assistant" && (from === "whatsapp_cloud" || isSocialChannel(from));
    },
  },
  preview: {
    container: "portal-messages",
    element: "article",
    bubble: (role, mine, ai, grouped) => `${role}${mine ? " mine" : ""}${mine && ai ? " ai" : ""}${grouped ? " grouped" : ""}`,
    activity: true,
    notes: false,
    aiTag: true,
    deliveryError: false,
    groupedGuard: true,
    // The preview reads the lead's own channel, not the one each entry came in on.
    ticks: (message, channel) => message.role === "assistant" && (channel === "whatsapp_cloud" || isSocialChannel(channel)),
  },
};

/** The reply and react buttons that float over a bubble on hover. */
export type BubbleActions = {
  /** The person may answer this lead at all. */
  enabled: boolean;
  onReact: (message: Message) => void;
  onReply: (message: Message) => void;
};

export type MessageThreadProps = {
  messages?: Message[];
  surface: MessageThreadSurface;
  t: TranslateFn;
  lang: string;
  /** Where an attachment of this thread is served; each surface reaches its own door. */
  urlFor: (attachment: Attachment) => string;
  /** Every image of the thread, so the lightbox walks across all of them. */
  gallery?: GalleryImage[];
  /** The open lead's channel, used where an entry carries none of its own. */
  channel?: string;
  /** Marks each bubble with the channel it came in on, for a lead that mixes several. */
  channelMark?: boolean;
  bubbleActions?: BubbleActions;
  /** The emoji palette under a bubble, when the host has one open. */
  reactionPicker?: (message: Message) => ReactNode;
  /** Extra classes on the scroll container. */
  className?: string;
  containerRef?: RefObject<HTMLDivElement | null>;
  /** Rendered above the thread. */
  before?: ReactNode;
  /** Rendered below the thread. */
  after?: ReactNode;
};

/** One conversation's messages: the bubble, the cards that stand in for a
 * merge, a note or an appointment, and the activity lines between them. Every
 * inbox reads a thread through here, so a change to a message lands once. */
export function MessageThread({ messages, surface, t, lang, urlFor, gallery, channel, channelMark, bubbleActions, reactionPicker, className, containerRef, before, after }: MessageThreadProps) {
  const rules = SURFACES[surface];
  const list = messages ?? [];
  const Tag = rules.element;
  return (
    <div className={`${rules.container}${className ? ` ${className}` : ""}`} ref={containerRef}>
      {before}
      {list.map((message, index) => {
        if (isMergeActivity(message)) return <MergeAuditCard key={message.id} message={message} />;
        if (isAppointmentActivity(message)) return <AppointmentActivityCard key={message.id} message={message} />;
        if (rules.activity && message.kind === "activity") {
          return (
            <div key={message.id} className="activity-line">
              <span>{activityText(t, message)}</span>
              <time>{formatTime(message.created_at, lang)}</time>
            </div>
          );
        }
        if (rules.notes && message.kind === "note") {
          return (
            <div key={message.id} className="internal-note-card">
              <div className="internal-note-header">
                <Lock size={12} />
                <span>{message.sender_name || t("inbox.senderAgent")} · {t("inbox.internalNoteBadge")}</span>
                <time>{formatTime(message.created_at, lang)}</time>
              </div>
              <div className="internal-note-content">
                <RichText text={message.content} />
              </div>
            </div>
          );
        }

        const prev = index > 0 ? list[index - 1] : null;
        const grouped = Boolean(prev && (!rules.groupedGuard || prev.kind !== "activity") && prev.role === message.role && prev.sender_name === message.sender_name);
        const stamp = formatTime(message.created_at, lang);
        const hasAudio = message.attachments?.some((a) => a.kind === "audio");
        const mine = message.role === "assistant";
        const mark = channelMark ? <MessageChannelMark channel={message.channel} t={t} /> : null;

        return (
          <Tag key={message.id} className={rules.bubble(message.role, mine, message.sender_type === "ai", grouped)}>
            {!grouped && (
              <small>
                {message.sender_name || (mine ? t("inbox.senderAgent") : t("inbox.senderVisitor"))}
                {rules.aiTag && mine && message.sender_type === "ai" && <span className="preview-ai-tag">AI</span>}
              </small>
            )}
            {bubbleActions && bubbleActions.enabled && (message.channel ?? channel ?? "").startsWith("whatsapp") && (
              <span className="bubble-actions">
                {message.role === "user" && (
                  <button
                    type="button"
                    title={t("portal.inbox.conversation.react")}
                    aria-label={t("portal.inbox.conversation.react")}
                    onClick={() => bubbleActions.onReact(message)}
                  >
                    <SmilePlus size={14} />
                  </button>
                )}
                <button
                  type="button"
                  title={t("portal.inbox.conversation.reply")}
                  aria-label={t("portal.inbox.conversation.reply")}
                  onClick={() => bubbleActions.onReply(message)}
                >
                  <Reply size={14} />
                </button>
              </span>
            )}

            <MessageAttachments attachments={message.attachments} urlFor={urlFor} gallery={gallery} stamp={stamp} />
            {message.content && (
              <p>
                <QuotedSnippet messages={list} quotedId={message.quoted_message_id} />
                <RichText text={message.content} />
                <time className="msg-time">
                  {mark}
                  {stamp}
                  {rules.ticks(message, channel) && <DeliveryTicks status={message.delivery_status} error={message.delivery_error} />}
                </time>
                {rules.deliveryError && mine && message.delivery_status === "failed" && message.delivery_error && (
                  <span className="msg-error">{message.delivery_error}</span>
                )}
              </p>
            )}
            <ReactionBadge emoji={message.reaction} />
            <ReactionBadge emoji={message.incoming_reaction} incoming />
            {!message.content && !hasAudio && message.attachments?.length ? (
              <time className="msg-time bare">
                {mark}
                {stamp}
              </time>
            ) : null}
            {reactionPicker?.(message)}
          </Tag>
        );
      })}
      {after}
    </div>
  );
}
