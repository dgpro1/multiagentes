import type { Conversation } from "@/types";

/** Relative-ish timestamps for inbox rows and bubbles. */
export function formatWhen(iso: string, locale: string = "en"): string {
  const date = new Date(iso);
  const time = date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  const sameDay = date.toDateString() === new Date().toDateString();
  if (sameDay) return time;
  const day = date.toLocaleDateString(locale, { day: "numeric", month: "short" });
  return `${day} · ${time}`;
}

/** Clock time only (e.g. "4:16 PM"), for the WhatsApp-style stamp inside bubbles. */
export function formatTime(iso: string, locale: string = "en"): string {
  return new Date(iso).toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" });
}

const DAY = 86_400_000;

/** The calendar day a message belongs to, in the reader's own timezone, as a
 * comparable number. Two messages are on the same day when this matches, which
 * is what the day separator and the group rule both ask. */
function localDay(iso: string): number {
  const date = new Date(iso);
  return Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / DAY);
}

/** True when both instants fall on the same calendar day, where the reader is. */
export function sameLocalDay(iso: string, other: string): boolean {
  return localDay(iso) === localDay(other);
}

/** The day a message belongs to, as a separator over a thread: "Today",
 * "Yesterday", or the date. "Today" and "Yesterday" are read in the reader's
 * language, so they come from the dictionary, not from here. */
export function isToday(iso: string): boolean {
  return localDay(iso) === localDay(new Date().toISOString());
}

export function isYesterday(iso: string): boolean {
  return localDay(iso) === localDay(new Date().toISOString()) - 1;
}

/** The date of a stamp as `DD/MM/YYYY`, the same digits `formatStamp` leads
 * with. Exposed on its own because the day separator shows a date without an
 * hour, and it must not be a second spelling of the same day. */
export function formatDay(iso: string): string {
  const date = new Date(iso);
  return `${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${date.getFullYear()}`;
}

/** The full stamp above a bubble: `DD/MM/YYYY HH:MM`.
 *
 * The format is deliberately the same in every language and on a 24-hour
 * clock, and it is not the browser's idea of a date: two people reading one
 * lead in two languages have to read the same digits, and a lead is a record
 * rather than a chat to be prettied. */
export function formatStamp(iso: string): string {
  const date = new Date(iso);
  return `${formatDay(iso)} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** True when a poll result should not replace the open thread (avoids scroll jumps). */
export function isSameOpenThread(prev: Conversation | null, next: Conversation): boolean {
  if (!prev || prev.id !== next.id || prev.mode !== next.mode) return false;
  const stateFields = ["status", "assignee_id", "assignee_name", "team_id", "team_name", "reply_window_open", "reply_window_until", "human_reply_window_open", "human_reply_window_until", "reply_block_reason"] as const;
  if (stateFields.some((field) => prev[field] !== next[field])) return false;
  // Who the lead is about, and what it is called. The header and the card read
  // these, so a rename made anywhere (the lead card, the contacts screen) has to
  // reach the open thread: it is the same person, not a new arrival.
  const identityFields = ["title", "contact_name", "contact_phone", "contact_email", "number", "account_label", "deal_value"] as const;
  if (identityFields.some((field) => prev[field] !== next[field])) return false;
  if (JSON.stringify(prev.channel_capabilities) !== JSON.stringify(next.channel_capabilities)) return false;
  // A merge adds threads to the lead without necessarily changing its messages.
  if (JSON.stringify(prev.linked_threads) !== JSON.stringify(next.linked_threads)) return false;
  const prevMessages = prev.messages ?? [];
  const nextMessages = next.messages ?? [];
  if (prevMessages.length !== nextMessages.length) return false;
  // Reactions and delivery ticks mutate in place, so the id alone is not enough.
  const fingerprint = (list: typeof prevMessages) =>
    list.map((m) => `${m.id}|${m.reaction ?? ""}|${m.incoming_reaction ?? ""}|${m.delivery_status ?? ""}|${m.delivery_error ?? ""}|${m.content}`).join(",");
  return fingerprint(prevMessages) === fingerprint(nextMessages);
}

/** True when the scroll container is near the bottom (within a threshold). */
export function isNearBottom(el: HTMLElement, threshold: number = 150): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
}
