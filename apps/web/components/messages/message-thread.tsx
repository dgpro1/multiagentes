import { Fragment, type ReactNode, type RefObject } from "react";
import { Lock, Reply, SmilePlus } from "lucide-react";
import { MessageAttachments, type GalleryImage } from "@/components/attachments";
import { AppointmentActivityCard, isAppointmentActivity } from "@/components/appointment-activity-card";
import { DeliveryTicks } from "@/components/delivery-ticks";
import { MergeAuditCard, isMergeActivity } from "@/components/merge-audit-card";
import { QuotedSnippet, ReactionBadge } from "@/components/message-gestures";
import { RichText } from "@/components/rich-text";
import { activityText } from "@/lib/activity";
import { MessageChannelMark, isSocialChannel } from "@/lib/channels";
import { formatDay, formatStamp, formatTime, isToday, isYesterday, sameLocalDay } from "@/lib/datetime";
import type { TranslateFn } from "@/lib/i18n";
import type { Attachment, Message } from "@/types";

/** Where a thread is being read. The agency's inbox, the client's inbox in the
 * agency panel and the client portal draw the same entries the same way; the
 * contacts preview is a read-only thread of one contact. */
export type MessageThreadSurface = "agency" | "portal" | "preview";

/** How each surface draws a thread. A thread reads the same everywhere: an
 * activity line is a line, a note is a card, a bubble carries its stamp, its
 * sender and its channel, and only the preview still names the AI beside the
 * sender, because it is read by somebody who is not an operator. Everything a
 * bubble needs to know about where it is lives here, so the next change to a
 * message touches this file once instead of four. */
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
    bubble: (role, mine, ai, grouped) => `inbox-message ${role}${mine ? " mine" : ""}${mine && ai ? " ai" : ""}${grouped ? " grouped" : ""}`,
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
    notes: true,
    // The preview reads the lead's own channel, not the one each entry came in on.
    aiTag: true,
    deliveryError: true,
    groupedGuard: true,
    ticks: (message, channel) => message.role === "assistant" && (channel === "whatsapp_cloud" || isSocialChannel(channel)),
  },
};

/** Whether `message` continues the group the entry above it started, instead of
 * opening a new one. Same person, same channel, same day: a lead that mixes
 * WhatsApp and Facebook keeps the two apart, because the icon under the bubble
 * is the only thing that says which one a message came in on, and a group hides
 * the name and the icon of everything after its first entry. */
function continuesGroup(prev: Message | null, message: Message, guard: boolean): boolean {
  if (!prev) return false;
  if (guard && prev.kind === "activity") return false;
  return prev.role === message.role
    && prev.sender_name === message.sender_name
    && (prev.channel ?? null) === (message.channel ?? null);
}

/** The separator over a thread: the day a message belongs to, in the reader's
 * language for today and yesterday and as a plain date for anything older. */
function daySeparator(iso: string, t: TranslateFn): string {
  if (isToday(iso)) return t("inbox.dayToday");
  if (isYesterday(iso)) return t("inbox.dayYesterday");
  return formatDay(iso);
}

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
export function MessageThread({ messages, surface, t, lang, urlFor, gallery, channel, bubbleActions, reactionPicker, className, containerRef, before, after }: MessageThreadProps) {
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
                <span>{message.sender_name || t("inbox.senderAgent")} Â· {t("inbox.internalNoteBadge")}</span>
                <time>{formatTime(message.created_at, lang)}</time>
              </div>
              <div className="internal-note-content">
                <RichText text={message.content} />
              </div>
            </div>
          );
        }

        const prev = index > 0 ? list[index - 1] : null;
        // A new day opens a new group: two entries from the same person a week
        // apart are not a continuation of each other, and the separator between
        // them has to carry a name above it.
        const newDay = !prev || !sameLocalDay(prev.created_at, message.created_at);
        const grouped = !newDay && continuesGroup(prev, message, rules.groupedGuard);
        const stamp = formatStamp(message.created_at);
        const hasAudio = message.attachments?.some((a) => a.kind === "audio");
        const mine = message.role === "assistant";
        // The channel is a fact of the message, not a nicety: it says who is
        // actually talking when one lead answers on two channels, and it is
        // shown whether or not the lead mixes them.
        const mark = <MessageChannelMark channel={message.channel} t={t} />;

        return (
          <Fragment key={message.id}>
            {newDay && <div className="msg-day-divider"><span>{daySeparator(message.created_at, t)}</span></div>}
            <Tag className={rules.bubble(message.role, mine, message.sender_type === "ai", grouped)}>
              <small className="msg-meta">
                <time>{stamp}</time>
                <span>{message.sender_name || (mine ? t("inbox.senderAgent") : t("inbox.senderVisitor"))}</span>
                {rules.aiTag && mine && message.sender_type === "ai" && <span className="preview-ai-tag">AI</span>}
              </small>
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

              <MessageAttachments attachments={message.attachments} urlFor={urlFor} gallery={gallery} stamp={formatTime(message.created_at, lang)} />
              {message.content && (
                <p>
                  <QuotedSnippet messages={list} quotedId={message.quoted_message_id} />
                  <RichText text={message.content} />
                  {rules.ticks(message, channel) && <DeliveryTicks status={message.delivery_status} error={message.delivery_error} />}
                </p>
              )}
              <ReactionBadge emoji={message.reaction} />
              <ReactionBadge emoji={message.incoming_reaction} incoming />
              {message.content && rules.deliveryError && mine && message.delivery_status === "failed" && message.delivery_error && (
                <span className="msg-error">{message.delivery_error}</span>
              )}
              {/* The channel sits under the bubble, the way the reference puts it,
                  so two channels in one lead can be told apart at a glance. */}
              <span className="msg-channel-slot">{mark}</span>
              {!message.content && !hasAudio && message.attachments?.length ? (
                <time className="msg-time bare">{stamp}</time>
              ) : null}
              {reactionPicker?.(message)}
            </Tag>
          </Fragment>
        );
      })}
      {after}
    </div>
  );
}
