"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Archive, ArchiveRestore, ArrowLeft, Ban, BarChart3, Bot, Building2, Calendar as CalendarIcon, Check, CheckSquare, ChevronDown, Clock, Contact as ContactIcon, FileText, Filter, GitBranch, Images, Inbox, Link2, LoaderCircle, LogOut, MessageSquareText, PanelLeftClose, PanelLeftOpen, Navigation, Paperclip, Plus, Radio, Reply, Search, Smile, Settings, ShieldCheck, SmilePlus, Square, Trash2, UserRound, X, Zap } from "lucide-react";
import { useCannedReplies } from "../canned";
import { ContactsView } from "../contacts";
import { ReportsView } from "../reports";
import { CalendarView } from "@/components/calendar-view";
import { PipelineBoard } from "@/components/pipeline-board";
import { TemplatePicker } from "../templates";
import { SettingsView } from "../settings";
import { AgentsListView } from "@/components/agents/agents-list";
import { AgentWizardView } from "@/components/agents/agent-wizard";
import { AgentDetailView } from "@/components/agents/agent-detail";
import { portalAgentHrefs } from "@/components/agents/scope";
import { ChannelsOverviewView } from "@/components/channels/channels-overview";
import { ChannelScreen } from "@/components/channels/channel-screen";
import { portalChannelHrefs } from "@/components/channels/scope";
import { MessageAttachments, PendingAttachment, RecordButton, useFileDrop, type GalleryImage } from "@/components/attachments";
import { MediaPanel } from "@/components/media-panel";
import { PasswordInput } from "@/components/password-input";
import { RichText } from "@/components/rich-text";
import { GrowingTextarea } from "@/components/growing-textarea";
import { QuotedSnippet, ReactionBadge, ReactionPicker } from "@/components/message-gestures";
import { DeliveryTicks } from "@/components/delivery-ticks";
import { useToast } from "@/components/toast";
import { Alert, EmptyState, Modal } from "@/components/ui";
import { ChannelIcon, channelLabel as labelForChannel, INBOX_CHANNELS, isSocialChannel } from "@/lib/channels";
import { PhonePauseNotice, SocialReplyNotice, useReplyPolicy } from "@/components/reply-policy";
import { api, ApiError, apiUrl, apiWithHeaders, messageFrom } from "@/lib/api";
import { activityText as activityLine } from "@/lib/activity";
import { NAV_COLLAPSED_CLASS, useCollapsibleNav } from "@/lib/sidebar";
import { parsePortalPath, portalBase, portalChannelPath, portalPath, type PortalView } from "@/lib/routes";
import { enabledChannelTypes, hasFeature, permissionFeatureOn, type PortalFeature } from "@/lib/portal-features";
import { formatTime, formatWhen, isNearBottom, isSameOpenThread } from "@/lib/datetime";
import { useLanguage, useT } from "@/lib/i18n";
import type { Attachment, Conversation, Message, PortalChannel, PortalPublic, Team, TemplateSend } from "@/types";
import type { ContactValues } from "@/lib/contact-variables";

const POLL_MS = 8000;

function chime() {
  try {
    const ctx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
    const play = (freq: number, at: number) => {
      const osc = ctx.createOscillator(); const gain = ctx.createGain();
      osc.type = "sine"; osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime + at);
      gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + at + 0.28);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + at); osc.stop(ctx.currentTime + at + 0.3);
    };
    play(880, 0); play(1175, 0.16);
    setTimeout(() => ctx.close().catch(() => {}), 800);
  } catch { /* no audio available */ }
}
const LIMIT = 30;

type Session = { client_id: string; client_name: string; portal_slug: string; agency_name: string; user_id?: string | null; user_name?: string | null; role?: "admin" | "agent" | null; permissions?: string[]; /** The functions the agency switched on for this portal. */ features?: string[] };
type Member = { id: string; name: string; email: string };
type InboxSummary = { open: number; resolved: number; archived: number; human: number; ai: number; unread: number; unanswered: number; mine: number; unassigned: number };
// One refresh of the inbox: the page, its total, the counters and what is mine.
type InboxPayload = { items: Conversation[]; total: number; summary: InboxSummary; mine: { id: string; title: string }[] };
// What a thread's response updates on its row in the list. The preview and
// the unread state come from the next refresh, which runs right behind.
const ROW_FIELDS = ["status", "mode", "assignee_id", "assignee_name", "team_id", "team_name", "taken_over_at", "resolved_at", "archived_at", "phone_pause_until", "reply_window_until", "reply_window_open", "human_reply_window_open", "human_reply_window_until", "reply_block_reason", "updated_at"] as const;
// The conversation list and the thread share the inbox width; the divider between
// them slides, and the width is remembered per browser.
const LIST_WIDTH = { key: "openlivery.portal-inbox-width", min: 240, max: 560, wide: 360, narrow: 260, narrowBelow: 900, threadMin: 380, step: 16 };
// Narrow windows start with a slimmer list, as the layout always did.
function defaultListWidth(): number {
  return window.innerWidth <= LIST_WIDTH.narrowBelow ? LIST_WIDTH.narrow : LIST_WIDTH.wide;
}
function readListWidth(): number {
  try {
    const stored = Number(window.localStorage.getItem(LIST_WIDTH.key));
    return Number.isFinite(stored) && stored >= LIST_WIDTH.min && stored <= LIST_WIDTH.max ? stored : defaultListWidth();
  } catch {
    return defaultListWidth();
  }
}

// Filters travel in the address (?source=&state=&q=) so a filtered view can be shared or reloaded.
function initialParam(name: string): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(name) ?? "";
}

type InboxKind = "all" | "open" | "resolved" | "archived";

export default function PortalPage() {
  const t = useT();
  const { slug } = useParams<{ slug: string }>();
  const [portal, setPortal] = useState<PortalPublic | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => { Promise.all([api<PortalPublic>(`/portal/${slug}`), api<Session>(`/portal/${slug}/me`).catch((err) => { if (err instanceof ApiError && err.status === 401) return null; throw err; })]).then(([info, me]) => { setPortal(info); setSession(me); }).catch((err) => setError(messageFrom(err))).finally(() => setLoading(false)); }, [slug]);
  async function login(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setError(""); const data = new FormData(event.currentTarget); try { setSession(await api<Session>(`/portal/${slug}/login`, { method: "POST", body: JSON.stringify({ email: data.get("email"), password: data.get("password") }) })); } catch (err) { setError(messageFrom(err)); } }
  async function logout() { await api(`/portal/${slug}/logout`, { method: "POST" }); setSession(null); }
  if (loading) return <div className="portal-loader"><LoaderCircle className="spin" /> {t("portal.loader.loading")}</div>;
  if (!portal) return <div className="portal-loader">{error || t("portal.loader.unavailable")}</div>;
  if (!session) return <main className="access-page portal-access" style={{ "--portal-color": portal.agency_brand_color } as React.CSSProperties}><header className="access-topbar"><div className="access-brand portal-access-brand">{portal.agency_logo_url ? <img src={`${portal.agency_logo_url}`} alt={portal.agency_name} /> : <span>{portal.agency_name.slice(0, 1)}</span>}<strong>{portal.agency_name}</strong></div><small>{t("portal.access.secureBadge")}</small></header><div className="access-layout"><section className="access-intro"><span className="access-eyebrow">{t("portal.access.eyebrow")}</span><h1>{portal.portal_title}</h1><p>{t("portal.access.intro")}</p><div className="access-preview portal-preview" aria-hidden="true"><header><div><span className="preview-logo"><Inbox size={16} /></span><strong>{t("portal.access.preview.inbox")}</strong></div><small>{t("portal.access.preview.conversationsCount")}</small></header><div className="portal-preview-thread"><div className="active"><span className="preview-icon"><UserRound size={16} /></span><p><strong>{t("portal.access.preview.newInquiry")}</strong><small>{t("portal.access.preview.newInquiryMeta")}</small></p><em>2</em></div><div><span className="preview-icon"><MessageSquareText size={16} /></span><p><strong>{t("portal.access.preview.salesFollowUp")}</strong><small>{t("portal.access.preview.salesFollowUpMeta")}</small></p></div><div><span className="preview-icon"><Building2 size={16} /></span><p><strong>{t("portal.access.preview.servicesInfo")}</strong><small>{t("portal.access.preview.servicesInfoMeta")}</small></p></div></div><footer><span><Bot size={15} /> {t("portal.access.preview.agentReplying")}</span><strong>{t("portal.access.preview.takeControl")}</strong></footer></div></section><section className="access-form-wrap"><form className="access-card access-form" onSubmit={login}><span className="portal-client-avatar">{portal.client_name.slice(0, 2).toUpperCase()}</span><span className="access-card-label"><ShieldCheck size={15} /> {t("portal.access.form.cardLabel")}</span><h2>{t("portal.access.form.welcome", { name: portal.client_name })}</h2><p>{t("portal.access.form.subtitle")}</p><label>{t("portal.access.form.emailLabel")}<input name="email" type="email" required autoFocus placeholder={t("portal.access.form.emailPlaceholder")} /></label><label>{t("portal.access.form.passwordLabel")}<PasswordInput name="password" required placeholder={t("portal.access.form.passwordPlaceholder")} /></label>{error && <Alert>{error}</Alert>}<button className="button primary full">{t("portal.access.form.submit")}</button><small className="access-security"><ShieldCheck size={14} /> {t("portal.access.form.security", { name: portal.agency_name })}</small></form></section></div></main>;
  return <PortalInbox slug={slug} portal={portal} session={session} logout={logout} />;
}

function PortalInbox({ slug, portal, session, logout }: { slug: string; portal: PortalPublic; session: Session; logout: () => void }) {
  const t = useT();
  const { lang } = useLanguage();
  // The rail is a desktop gesture only; below 901px the stylesheet hands the
  // navigation back to the phone tab bar. See lib/sidebar.ts.
  const { collapsed, toggle } = useCollapsibleNav("portal");
  // What the signed-in person may do (app.portal_permissions in the API).
  // The UI hides what they cannot; the API refuses it regardless.
  const permissions = useMemo(() => new Set(session.permissions ?? []), [session.permissions]);
  // A function the agency switched off is gone for everyone in this portal, whatever their role.
  const enabled = useCallback((key: PortalFeature) => hasFeature(session.features, key), [session.features]);
  const can = useCallback((key: string) => permissions.has(key) && permissionFeatureOn(key, enabled), [permissions, enabled]);
  const base = `/portal/${slug}`;
  const [items, setItems] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [quoting, setQuoting] = useState<Message | null>(null);
  const [reactingTo, setReactingTo] = useState<string | null>(null);
  const [tab, setTab] = useState<"all" | "unread" | "mine" | "ai">("all");
  const [members, setMembers] = useState<Member[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [teamFilter] = useState("");
  const [availability, setAvailability] = useState<"online" | "away">("online");
  useEffect(() => {
    if (!session.user_id) return;
    api<{ id: string; availability: "online" | "away" }[]>(`/portal/${slug}/members`)
      .then((rows) => { const mine = rows.find((row) => row.id === session.user_id); if (mine) setAvailability(mine.availability); })
      .catch(() => {});
  }, [slug, session.user_id]);
  async function toggleAvailability() {
    const next = availability === "online" ? "away" : "online";
    setAvailability(next);
    try { await api(`/portal/${slug}/me`, { method: "PATCH", body: JSON.stringify({ availability: next }) }); }
    catch { setAvailability(availability); }
  }
  useEffect(() => { api<Member[]>(`/portal/${slug}/members`).then(setMembers).catch(() => {}); }, [slug]);
  const toast = useToast();
  // Conversations handed to me: the first poll only learns what is mine,
  // every later one announces what is new, so a transfer is felt at once.
  const mineKnownRef = useRef<Set<string> | null>(null);
  const announceAssignments = useCallback((mine: { id: string; title: string }[]) => {
    if (!session.user_id) return;
    const known = mineKnownRef.current;
    const next = new Set(mine.map((row) => row.id));
    if (known) {
      const fresh = mine.filter((row) => !known.has(row.id));
      if (fresh.length) {
        chime();
        for (const row of fresh) {
          const text = t("portal.inbox.assignment.received", { title: row.title });
          toast.info(text);
          if (typeof Notification !== "undefined" && Notification.permission === "granted") {
            try { new Notification(portal.portal_title, { body: text }); } catch { /* blocked */ }
          }
        }
      }
    }
    mineKnownRef.current = next;
  }, [session.user_id, t, toast, portal.portal_title]);
  useEffect(() => {
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  }, []);
  const [status, setStatus] = useState<InboxKind>("all");
  // Archive and delete confirmations. "all" acts on every conversation the current inbox holds.
  const [archivingAll, setArchivingAll] = useState(false);
  const [deleting, setDeleting] = useState<Conversation | "all" | "picked" | null>(null);
  const [confirmWord, setConfirmWord] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  // Rows ticked in the archive, for deleting several at once.
  const [picked, setPicked] = useState<string[]>([]);
  const [blockingContact, setBlockingContact] = useState(false);
  const [summary, setSummary] = useState<InboxSummary | null>(null);
  // The address is the source of truth for the screen and the open lead: sharing the
  // link, reloading, Back and Forward all land on the same thing (lib/routes.ts).
  const router = useRouter();
  const routeParams = useParams<{ path?: string[] }>();
  const route = parsePortalPath(routeParams.path);
  const featureOfView: Partial<Record<PortalView, PortalFeature>> = { inbox: "inbox", contacts: "contacts", calendar: "calendar", pipeline: "pipeline", reports: "reports", agents: "agents" };
  // Agents and channels also need their permission (admins only); `can()` already folds the function in
  // (channels: at least one channel type switched on).
  const viewOn = route.view === "settings" || (enabled(featureOfView[route.view] ?? "inbox") && (route.view !== "agents" || can("agents.manage")) && (route.view !== "channels" || can("channels.manage")));
  const view: PortalView = viewOn ? route.view : "inbox";
  const urlNumber = route.number;
  const urlBase = useMemo(() => portalBase(slug), [slug]);
  const agentHrefs = useMemo(() => portalAgentHrefs(urlBase), [urlBase]);
  const channelHrefs = useMemo(() => portalChannelHrefs(urlBase), [urlBase]);
  // The channel types the agency switched on: the Channels screen lists and opens only these.
  const channelTypes = useMemo(() => enabledChannelTypes(enabled), [enabled]);
  const channelType = route.channelType && channelTypes.includes(route.channelType) ? route.channelType : undefined;
  const goTo = (target: PortalView, id?: number | string | null, mode: "push" | "replace" = "push") => router[mode](portalPath(urlBase, target, id));
  function clearSelection(mode: "push" | "replace" = "replace") {
    setSelected(null);
    if (urlNumber !== undefined) goTo("inbox", null, mode);
  }
  useEffect(() => {
    if (view === "inbox") api<Team[]>(`/portal/${slug}/teams`).then(setTeams).catch(() => {});
  }, [slug, view]);
  const [channels, setChannels] = useState<PortalChannel[]>([]);
  useEffect(() => { api<PortalChannel[]>(`/portal/${slug}/channels`).then(setChannels).catch(() => {}); }, [slug]);
  const templatesSupported = enabled("templates") && channels.some((c) => c.channel === "whatsapp_cloud" && c.supports_templates);
  const [templateOpen, setTemplateOpen] = useState(false);
  async function openFromContact(conversation: Conversation) {
    // Fetch first, then switch everything in one render: the selection is in
    // place before the list reloads for the conversation's inbox.
    const detail = await api<Conversation>(`/portal/${slug}/conversations/${conversation.id}`);
    setSelected(detail);
    setStatus(conversation.archived_at ? "archived" : "all");
    setTab("all");
    setChannelFilter("");
    goTo("inbox", detail.number);
  }
  function switchStatus(next: InboxKind) {
    if (next === status) return;
    setStatus(next); if (next === "archived") setTab("all"); setPicked([]);
    // Clearing the selection also clears the ref, through the effect that
    // keeps them in sync, before the list reloads for the new inbox.
    clearSelection();
  }
  const [search, setSearch] = useState(() => initialParam("q"));
  const [query, setQuery] = useState(() => initialParam("q"));
  const [channelFilter, setChannelFilter] = useState(() => initialParam("source"));
  const [filtersOpen, setFiltersOpen] = useState(false);
  // "Unanswered": open chats where the contact wrote last and nobody has replied.
  const [unansweredOnly, setUnansweredOnly] = useState(() => initialParam("state") === "unanswered");
  useEffect(() => {
    if (view !== "inbox") return;
    const params = new URLSearchParams(window.location.search);
    const put = (key: string, value: string) => { if (value) params.set(key, value); else params.delete(key); };
    put("source", channelFilter);
    put("state", unansweredOnly ? "unanswered" : "");
    put("q", query);
    const qs = params.toString();
    const next = `${window.location.pathname}${qs ? `?${qs}` : ""}`;
    if (next !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(window.history.state, "", next);
  }, [view, urlNumber, channelFilter, unansweredOnly, query]);
  const [sourceOpen, setSourceOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!filtersOpen) return;
    const close = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent) {
        if (event.key === "Escape") { setFiltersOpen(false); setSourceOpen(false); }
        return;
      }
      if (filterRef.current && !filterRef.current.contains(event.target as Node)) { setFiltersOpen(false); setSourceOpen(false); }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", close); };
  }, [filtersOpen]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [listTotal, setListTotal] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [mediaOpen, setMediaOpen] = useState(false);
  const selectedIdRef = useRef<string | null>(null);

  const channelLabel = (value: string) => labelForChannel(value, t);
  const channelIcon = (value: string) => <ChannelIcon channel={value} />;
  // Search is sent to the server after a short pause, so the list, the tabs
  // and paging all agree on the same filter.
  useEffect(() => {
    const id = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(id);
  }, [search]);
  const buildParams = useCallback((offsetValue: number) => {
    const params = new URLSearchParams();
    if (status === "archived") params.set("archived", "1"); else if (status !== "all") params.set("status", status);
    if (tab === "ai") params.set("mode", "ai");
    if (tab === "mine") params.set("assignee", "me");
    if (tab === "unread") params.set("unread", "1");
    if (unansweredOnly) params.set("unanswered", "1");
    if (teamFilter) params.set("team", teamFilter);
    if (query) params.set("search", query);
    if (channelFilter) params.set("channel", channelFilter);
    params.set("limit", String(LIMIT));
    params.set("offset", String(offsetValue));
    return params.toString();
  }, [tab, status, query, teamFilter, channelFilter, unansweredOnly]);
  // Switching inboxes reloads the list, and until the new rows arrive the
  // state still holds the previous inbox. Filter on render so a resolved
  // conversation never flashes inside Open, or the other way round.
  const visibleItems = useMemo(() => items.filter((item) => (status === "archived" ? Boolean(item.archived_at) : !item.archived_at && (status === "all" || (item.status === "resolved") === (status === "resolved"))) && (!channelFilter || item.channel === channelFilter)), [items, status, channelFilter]);
  // The source list offers only channels this client really has: the connected
  // lines plus any channel a conversation has come in through (the widget has
  // no line to connect). Seen channels accumulate so picking one does not hide
  // the others, and the picked one always stays listed.
  const [seenChannels, setSeenChannels] = useState<string[]>([]);
  useEffect(() => {
    setSeenChannels((prev) => {
      const next = new Set(prev);
      items.forEach((item) => next.add(item.channel));
      return next.size === prev.length ? prev : [...next];
    });
  }, [items]);
  const inboxRef = useRef<HTMLDivElement>(null);
  const [listWidth, setListWidth] = useState(readListWidth);
  const [resizing, setResizing] = useState(false);
  useEffect(() => {
    try { window.localStorage.setItem(LIST_WIDTH.key, String(listWidth)); } catch { /* the width still applies; only the memory of it is lost */ }
  }, [listWidth]);
  const clampListWidth = (width: number) => {
    const total = inboxRef.current?.clientWidth ?? 1000;
    return Math.round(Math.min(Math.max(width, LIST_WIDTH.min), Math.max(LIST_WIDTH.min, Math.min(LIST_WIDTH.max, total - LIST_WIDTH.threadMin))));
  };
  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setResizing(true);
  };
  const moveResize = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId) || !inboxRef.current) return;
    setListWidth(clampListWidth(event.clientX - inboxRef.current.getBoundingClientRect().left));
  };
  const endResize = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setResizing(false);
  };
  const keyResize = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const jump = event.shiftKey ? LIST_WIDTH.step * 3 : LIST_WIDTH.step;
    if (event.key === "ArrowLeft") setListWidth((width) => clampListWidth(width - jump));
    else if (event.key === "ArrowRight") setListWidth((width) => clampListWidth(width + jump));
    else if (event.key === "Home") setListWidth(defaultListWidth());
    else return;
    event.preventDefault();
  };
  // The composer card: what is typed (drives the Send colour and Cancel), and its small menus.
  const [draft, setDraft] = useState("");
  const [composerMenu, setComposerMenu] = useState<null | "action" | "channel" | "plus" | "emoji">(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    if (!composerMenu) return;
    const close = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent) { if (event.key === "Escape") setComposerMenu(null); return; }
      if (composerRef.current && !composerRef.current.contains(event.target as Node)) setComposerMenu(null);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", close); };
  }, [composerMenu]);
  const EMOJIS = ["😀", "😂", "😊", "😍", "😉", "🙂", "😅", "🤝", "🙏", "👍", "👏", "🎉", "❤️", "🔥", "✨", "😢", "😮", "🤔", "👌", "💪", "✅", "📅", "📍", "📞", "💬", "⭐", "🙌", "😎", "🥳", "😴", "👋", "💡"];
  function insertEmoji(emoji: string) {
    const field = replyInputRef.current;
    if (!field) return;
    const start = field.selectionStart ?? field.value.length;
    const end = field.selectionEnd ?? start;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    setter?.call(field, field.value.slice(0, start) + emoji + field.value.slice(end));
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.setSelectionRange(start + emoji.length, start + emoji.length);
    field.focus();
  }
  const sourceOptions = INBOX_CHANNELS.filter((value) => value !== "playground" && (channels.some((line) => line.channel === value) || seenChannels.includes(value) || channelFilter === value));
  const messagesRef = useRef<HTMLDivElement>(null);
  useEffect(() => { selectedIdRef.current = selected?.id ?? null; }, [selected]);
  const wasNearBottomRef = useRef(true);
  useEffect(() => {
    const el = messagesRef.current;
    if (el) { const handler = () => { wasNearBottomRef.current = isNearBottom(el); }; el.addEventListener("scroll", handler, { passive: true }); return () => el.removeEventListener("scroll", handler); }
  }, [selected?.id]);
  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    if (wasNearBottomRef.current) {
      const frame = requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
      return () => cancelAnimationFrame(frame);
    }
  }, [selected?.id, selected?.messages?.at(-1)?.id]);

  const markRead = useCallback((id: string) => {
    setItems((rows) => rows.map((row) => (row.id === id ? { ...row, unread: false, unread_count: 0 } : row)));
    setSummary((prev) => (prev && prev.unread > 0 ? { ...prev, unread: prev.unread - 1 } : prev));
    api(`/portal/${slug}/conversations/${id}/read`, { method: "POST" }).catch(() => {});
  }, [slug]);

  const refresh = useCallback(async () => {
    // One request for the page, the counters and what is mine; the open
    // thread is the only other thing asked for.
    const payload = await api<InboxPayload>(`/portal/${slug}/inbox?${buildParams(0)}`);
    const rows = payload.items;
    setSummary(payload.summary);
    announceAssignments(payload.mine);
    setListTotal(payload.total);
    setItems(rows); setOffset(rows.length); setHasMore(rows.length === LIMIT);
    // Only the thread the person opened is refreshed. The list used to open
    // its first thread on its own when nothing was selected, which read as
    // being dropped into a conversation after switching folders, and marked
    // that thread read unasked; the pane now waits for a choice instead.
    const openId = selectedIdRef.current;
    if (!openId) return;
    const conv = await api<Conversation>(`/portal/${slug}/conversations/${openId}`);
    if (selectedIdRef.current && selectedIdRef.current !== openId) return;
    if (!selectedIdRef.current) { selectedIdRef.current = openId; markRead(openId); }
    setSelected((prev) => {
      if (isSameOpenThread(prev, conv)) return prev;
      // New visitor messages arrived while this thread is on screen: they are read.
      if (prev && rows.find((row) => row.id === openId)?.unread) markRead(openId);
      return conv;
    });
  }, [slug, buildParams, markRead, announceAssignments]);

  useEffect(() => { refresh().catch((err) => setError(messageFrom(err))); }, [refresh]);
  useEffect(() => {
    const id = setInterval(() => { if (offset <= LIMIT) refresh().catch(() => {}); }, POLL_MS);
    return () => clearInterval(id);
  }, [refresh, offset]);

  // A thread's response after an action is the truth about that thread: show
  // it, update its row, and let the list catch up behind instead of holding
  // the person until it has.
  const applyThread = useCallback((conv: Conversation) => {
    setSelected(conv);
    setItems((rows) => rows.map((row) => {
      if (row.id !== conv.id) return row;
      const next = { ...row } as Record<string, unknown>;
      for (const key of ROW_FIELDS) if (key in conv) next[key] = conv[key];
      return next as Conversation;
    }));
    refresh().catch(() => {});
  }, [refresh]);

  async function loadMore() {
    if (!hasMore || loadingMore) return;
    setLoadingMore(true);
    try {
      const { data: rows, headers } = await apiWithHeaders<Conversation[]>(`/portal/${slug}/conversations?${buildParams(offset)}`);
      const count = Number(headers.get("X-Total-Count"));
      if (Number.isFinite(count)) setListTotal(count);
      setItems((prev) => [...prev, ...rows]); setOffset((o) => o + rows.length); setHasMore(rows.length === LIMIT);
    } catch (err) { setError(messageFrom(err)); } finally { setLoadingMore(false); }
  }
  function onListScroll(event: React.UIEvent<HTMLElement>) {
    const el = event.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) loadMore();
  }

  async function choose(item: Conversation) {
    setPendingFile(null);
    setQuoting(null);
    setTemplateOpen(false);
    if (replyInputRef.current) replyInputRef.current.value = "";
    selectedIdRef.current = item.id;
    setReactingTo(null);
    markRead(item.id);
    const detail = await api<Conversation>(`/portal/${slug}/conversations/${item.id}`);
    setSelected(detail);
    if (detail.number !== urlNumber || view !== "inbox") goTo("inbox", detail.number);
  }

  // A board card opens its thread in a new tab; links made before leads had a
  // number still arrive as ?conversation=<id> and are moved to the lead's own address.
  const chooseById = useCallback(async (id: string) => {
    try {
      const detail = await api<Conversation>(`/portal/${slug}/conversations/${id}`);
      selectedIdRef.current = detail.id;
      setSelected(detail);
      markRead(detail.id);
      router.replace(portalPath(urlBase, "inbox", detail.number));
    } catch {
      // Stale link: stay on the list instead of erroring.
    }
  }, [slug, markRead, router, urlBase]);
  // The bare address, a screen that does not exist or one the agency switched off goes to the inbox (keeping any query).
  useEffect(() => {
    if (route.known && viewOn) return;
    router.replace(`${portalPath(urlBase, "inbox")}${window.location.search}`);
  }, [route.known, viewOn, router, urlBase]);
  // A channel page for a type that is off (or that does not exist) goes to the Channels overview.
  useEffect(() => {
    if (!route.known || !viewOn || route.view !== "channels" || !routeParams.path?.[1]) return;
    if (channelType) return;
    router.replace(portalChannelPath(urlBase));
  }, [route.known, route.view, channelType, viewOn, routeParams.path, router, urlBase]);
  // Back, Forward and pasted links: the number in the address decides which lead is open.
  useEffect(() => {
    if (view !== "inbox" || !route.known) return;
    if (urlNumber === undefined) {
      if (selectedIdRef.current) setSelected(null);
      return;
    }
    if (selected?.number === urlNumber) return;
    let cancelled = false;
    api<Conversation>(`/portal/${slug}/conversations/number/${urlNumber}`)
      .then((detail) => {
        if (cancelled) return;
        selectedIdRef.current = detail.id;
        setSelected(detail);
        markRead(detail.id);
      })
      .catch(() => { if (!cancelled) router.replace(portalPath(urlBase, "inbox")); });
    return () => { cancelled = true; };
    // Only the address drives this; `selected` is read to skip a lead already open.
  }, [urlNumber, view, route.known, slug]);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get("conversation");
    if (!id) return;
    params.delete("conversation");
    const clean = `${window.location.pathname}${params.toString() ? `?${params}` : ""}${window.location.hash}`;
    window.history.replaceState(window.history.state, "", clean);
    void chooseById(id);
  }, [chooseById]);
  async function sendReaction(message: Message, emoji: string) {
    if (!selected || (selected.channel !== "whatsapp" && selected.channel !== "whatsapp_cloud")) return;
    setReactingTo(null);
    setError("");
    try {
      setSelected(await api<Conversation>(`/portal/${slug}/conversations/${selected.id}/messages/${message.id}/reaction`, { method: "POST", body: JSON.stringify({ emoji }) }));
    } catch (err) { setError(messageFrom(err)); }
  }
  async function setMode(mode: "ai" | "human") { if (!selected) return; applyThread(await api<Conversation>(`/portal/${slug}/conversations/${selected.id}/mode`, { method: "PATCH", body: JSON.stringify({ mode }) })); }
  async function setArchived(archived: boolean) {
    if (!selected) return;
    setError("");
    try {
      await api<Conversation>(`/portal/${slug}/conversations/${selected.id}/archive`, { method: "PATCH", body: JSON.stringify({ archived }) });
      // The conversation moved to another inbox; leave the selection so the
      // list does not show a row that no longer belongs here.
      setSelected(null);
      await refresh();
    } catch (err) { setError(messageFrom(err)); }
  }
  async function blockContact() {
    if (!selected?.contact_id) return;
    setBulkBusy(true); setError("");
    try {
      await api(`/portal/${slug}/contacts/${selected.contact_id}/block`, { method: "POST", body: JSON.stringify({ blocked: true }) });
      setBlockingContact(false); setSelected(null);
      await refresh();
    } catch (err) { setError(messageFrom(err)); } finally { setBulkBusy(false); }
  }
  async function archiveAllResolved() {
    setBulkBusy(true); setError("");
    try {
      await api<{ count: number }>(`/portal/${slug}/conversations/archive-resolved`, { method: "POST" });
      setArchivingAll(false); setSelected(null);
      await refresh();
    } catch (err) { setError(messageFrom(err)); } finally { setBulkBusy(false); }
  }
  async function deleteConversations() {
    if (!deleting) return;
    setBulkBusy(true); setError("");
    try {
      if (deleting === "all") await api<{ count: number }>(`/portal/${slug}/conversations/delete-archived`, { method: "POST" });
      else if (deleting === "picked") await api<{ count: number }>(`/portal/${slug}/conversations/delete-archived`, { method: "POST", body: JSON.stringify({ ids: picked }) });
      else await api(`/portal/${slug}/conversations/${deleting.id}`, { method: "DELETE" });
      setDeleting(null); setConfirmWord(""); setSelected(null); setPicked([]);
      await refresh();
    } catch (err) { setError(messageFrom(err)); } finally { setBulkBusy(false); }
  }
  async function setConversationTeam(teamId: string) {
    if (!selected) return;
    applyThread(await api<Conversation>(`/portal/${slug}/conversations/${selected.id}/team`, { method: "PATCH", body: JSON.stringify({ team_id: teamId || null }) }));
  }
  async function assignTo(assigneeId: string) {
    if (!selected) return;
    applyThread(await api<Conversation>(`/portal/${slug}/conversations/${selected.id}/assignment`, { method: "POST", body: JSON.stringify({ assignee_id: assigneeId }) }));
  }
  async function replyWithTemplate(payload: TemplateSend) {
    if (!selected || selected.channel !== "whatsapp_cloud") return;
    applyThread(await api<Conversation>(`/portal/${slug}/conversations/${selected.id}/reply-template`, { method: "POST", body: JSON.stringify(payload) }));
  }
  const memberLabel = (member: Member) => (member.id === session.user_id ? t("portal.inbox.assignment.me", { name: member.name }) : member.name);
  // Deleting asks for the word in the UI language, typed by hand.
  const deleteArmed = confirmWord.trim().toLowerCase() === t("portal.inbox.archive.deleteWord").toLowerCase();

  // Reactions and quoted replies travel over WhatsApp only; the web chat has no way to show them.
  const gesturesAvailable = selected?.channel === "whatsapp" || selected?.channel === "whatsapp_cloud";
  const policy = useReplyPolicy(selected);
  const windowClosed = Boolean(selected) && selected?.channel === "whatsapp_cloud" && policy.blocked;
  const canReply = policy.canReply;
  const activityText = (message: Message) => activityLine(t, message);
  async function reply(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!selected || !canReply || busy) return; if (pendingFile) { const file = pendingFile; setPendingFile(null); await sendAttachment(file); return; } const form = event.currentTarget; const data = new FormData(form); setBusy(true); setError(""); try { applyThread(await api<Conversation>(`/portal/${slug}/conversations/${selected.id}/reply`, { method: "POST", body: JSON.stringify({ content: data.get("content"), quoted_message_id: quoting?.id ?? null }) })); form.reset(); canned.reset(); setQuoting(null); } catch (err) { setError(messageFrom(err)); } finally { setBusy(false); } }
  const replyInputRef = useRef<HTMLTextAreaElement>(null);
  // The contact and agent values a saved reply or a template fills itself with.
  const contactValues: ContactValues = {
    contact_name: selected?.contact_name || selected?.title || "",
    contact_phone: isSocialChannel(selected?.channel) ? "" : (selected?.external_chat_id || "").split("@")[0],
    contact_email: selected?.contact_email || "",
    agent_name: session.user_name || "",
  };
  const canned = useCannedReplies({
    slug,
    vars: contactValues,
    onInsert: (text) => {
      const el = replyInputRef.current;
      if (el) { el.value = text; el.focus(); }
    },
  });
  async function sendAttachment(file?: File) {
    if (!file || !selected || !policy.canAttach || busy) return;
    setBusy(true); setError("");
    const caption = (replyInputRef.current?.value || "").trim();
    try {
      const data = new FormData();
      data.append("file", file);
      if (caption) data.append("caption", caption);
      applyThread(await api<Conversation>(`/portal/${slug}/conversations/${selected.id}/reply-media`, { method: "POST", body: data }));
      if (replyInputRef.current) replyInputRef.current.value = "";
      canned.reset();
    } catch (err) { setError(messageFrom(err)); } finally { setBusy(false); }
  }
  const { dropProps, overlay } = useFileDrop(setPendingFile, { enabled: policy.canAttach && !busy, label: t("chat.dropToSend") });

  const selectedId = selected?.id;
  const attachmentUrl = useCallback(
    (attachment: Attachment) => apiUrl(`/portal/${slug}/conversations/${selectedId}/attachments/${attachment.id}`),
    [slug, selectedId],
  );
  const gallery: GalleryImage[] = useMemo(
    () => (selected?.messages ?? []).flatMap((message) =>
      (message.attachments ?? []).filter((a) => a.kind === "image").map((a) => ({ id: a.id, url: attachmentUrl(a), name: a.filename }))
    ),
    [selected, attachmentUrl],
  );
  // The availability switch and sign-out. On a desktop they sit at the foot of
  // the side nav; on a phone that nav becomes a bottom bar with room for the
  // four links only, so the same foot is shown at the top of Settings instead
  // (the stylesheet shows one or the other, never both).
  const navFoot = <div className="portal-nav-foot">{session.user_id && <button className={`availability-toggle ${availability}`} onClick={toggleAvailability} title={availability === "online" ? t("portal.availability.setAway") : t("portal.availability.setOnline")} aria-label={collapsed ? `${session.user_name ?? ""}, ${t(availability === "online" ? "portal.availability.online" : "portal.availability.away")}` : undefined} aria-pressed={availability === "online"}><i /><span className="availability-name"><strong>{session.user_name}</strong><small>{availability === "online" ? t("portal.availability.online") : t("portal.availability.away")}</small></span><span className="availability-switch" aria-hidden="true"><b /></span></button>}<button onClick={logout} aria-label={t("portal.inbox.nav.logout")}><LogOut size={17} /> {t("portal.inbox.nav.logout")}</button></div>;
  return <main className={`portal-app ${collapsed ? NAV_COLLAPSED_CLASS : ""}`} style={{ "--portal-color": portal.agency_brand_color } as React.CSSProperties}><aside id="portal-nav" className="portal-nav"><div className="portal-brand">{portal.client_logo_url || portal.agency_logo_url ? <img src={`${portal.client_logo_url || portal.agency_logo_url}`} alt="Logo" /> : <span>{portal.client_name.slice(0, 1)}</span>}<strong>{portal.client_name}</strong>{/* Folds the column into the icon rail. Hidden below 901px, where the tab bar takes over. */}<button type="button" className="portal-nav-toggle" onClick={toggle} aria-expanded={!collapsed} aria-controls="portal-nav" title={t(collapsed ? "shell.expandSidebar" : "shell.collapseSidebar")} aria-label={t(collapsed ? "shell.expandSidebar" : "shell.collapseSidebar")}>{collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}</button></div><nav><Link className={view === "inbox" ? "active" : ""} href={portalPath(urlBase, "inbox")} title={collapsed ? t("portal.inbox.nav.inbox") : undefined} aria-label={collapsed ? t("portal.inbox.nav.inbox") : undefined}><Inbox size={18} /> {t("portal.inbox.nav.inbox")}{summary && summary.unread > 0 && view !== "inbox" && <em className="nav-count">{summary.unread}</em>}</Link>{enabled("contacts") && <Link className={view === "contacts" ? "active" : ""} href={portalPath(urlBase, "contacts")} title={collapsed ? t("portal.inbox.nav.contacts") : undefined} aria-label={collapsed ? t("portal.inbox.nav.contacts") : undefined}><ContactIcon size={18} /> {t("portal.inbox.nav.contacts")}</Link>}{enabled("calendar") && <Link className={view === "calendar" ? "active" : ""} href={portalPath(urlBase, "calendar")} title={collapsed ? t("clients.detail.tabCalendar") : undefined} aria-label={collapsed ? t("clients.detail.tabCalendar") : undefined}><CalendarIcon size={18} /> {t("clients.detail.tabCalendar")}</Link>}{enabled("pipeline") && <Link className={view === "pipeline" ? "active" : ""} href={portalPath(urlBase, "pipeline")} title={collapsed ? t("clients.detail.tabPipeline") : undefined} aria-label={collapsed ? t("clients.detail.tabPipeline") : undefined}><GitBranch size={18} /> {t("clients.detail.tabPipeline")}</Link>}{can("reports.view") &&<Link className={view === "reports" ? "active" : ""} href={portalPath(urlBase, "reports")} title={collapsed ? t("portal.inbox.nav.reports") : undefined} aria-label={collapsed ? t("portal.inbox.nav.reports") : undefined}><BarChart3 size={18} /> {t("portal.inbox.nav.reports")}</Link>}{enabled("agents") && can("agents.manage") && <Link className={view === "agents" ? "active" : ""} href={portalPath(urlBase, "agents")} title={collapsed ? t("portal.inbox.nav.agents") : undefined} aria-label={collapsed ? t("portal.inbox.nav.agents") : undefined}><Bot size={18} /> {t("portal.inbox.nav.agents")}</Link>}{can("channels.manage") && <Link className={view === "channels" ? "active" : ""} href={portalChannelPath(urlBase)} title={collapsed ? t("portal.inbox.nav.channels") : undefined} aria-label={collapsed ? t("portal.inbox.nav.channels") : undefined}><Radio size={18} /> {t("portal.inbox.nav.channels")}</Link>}<Link className={view === "settings" ? "active" : ""} href={portalPath(urlBase, "settings")} title={collapsed ? t("portal.inbox.nav.settings") : undefined} aria-label={collapsed ? t("portal.inbox.nav.settings") : undefined}><Settings size={18} /> {t("portal.inbox.nav.settings")}</Link></nav>{navFoot}</aside><section className={`portal-main${view === "pipeline" ? " portal-main-pipeline" : view === "agents" ? " portal-main-agents" : view === "channels" && channelType ? " portal-main-channel-page" : view === "inbox" ? " portal-main-inbox" : ""}`}><header><div><small>{t("portal.inbox.header.eyebrow")}</small><h1>{view === "contacts" ? t("portal.inbox.nav.contacts") : view === "settings" ? t("portal.inbox.nav.settings") : view === "reports" ? t("portal.inbox.nav.reports") : view === "agents" ? t("portal.inbox.nav.agents") : view === "channels" ? t("portal.inbox.nav.channels") : view === "calendar" ? t("clients.detail.tabCalendar") : view === "pipeline" ? t("clients.detail.tabPipeline") : portal.portal_title}</h1></div>{view === "inbox" && <span>{t("portal.inbox.header.conversationsCount", { count: items.length })}</span>}</header>{view === "settings" && <div className="portal-foot-mobile">{navFoot}</div>}{view === "settings" ? <SettingsView slug={slug} tab={route.tab} hrefFor={(tab) => portalPath(urlBase, "settings", tab === "preferences" ? null : tab)} templatesSupported={templatesSupported} can={can} /> : view === "reports" && can("reports.view") ? <ReportsView slug={slug} /> : view === "calendar" ? <div className="embedded-portal-view"><CalendarView base={`/portal/${slug}`} canManage={can("calendar.manage")} /></div> : view === "pipeline" ? <div className="embedded-portal-view"><PipelineBoard base={`/portal/${slug}`} canManage={can("pipeline.manage")} /></div> : view === "channels" ? <div className="embedded-portal-view portal-channels">{channelType ? <ChannelScreen key={channelType} type={channelType} apiBase={`/portal/${slug}/manage`} hrefFor={channelHrefs} client={{ id: session.client_id, name: session.client_name }} /> : <ChannelsOverviewView apiBase={`/portal/${slug}/manage`} hrefFor={channelHrefs} client={{ id: session.client_id, name: session.client_name }} types={channelTypes} />}</div> : view === "agents" ? <div className="embedded-portal-view portal-agents">{route.agentId === "new"
            ? <AgentWizardView apiBase={`/portal/${slug}/manage`} hrefFor={agentHrefs} client={{ id: session.client_id, name: session.client_name }} />
            : route.agentId
              ? <AgentDetailView key={route.agentId} id={route.agentId} segments={route.agentSegments} apiBase={`/portal/${slug}/manage`} hrefFor={agentHrefs} client={{ id: session.client_id, name: session.client_name }} />
              : <AgentsListView apiBase={`/portal/${slug}/manage`} hrefFor={agentHrefs} client={{ id: session.client_id, name: session.client_name }} />}</div> : view === "contacts" ? <ContactsView slug={slug} contactId={route.contactId} onOpenContact={(id) => { if ((id ?? undefined) !== route.contactId) goTo("contacts", id); }} channels={channels} openConversation={openFromContact} can={can} agentName={session.user_name || ""} /> : <div ref={inboxRef} className={`portal-inbox${selected ? " has-thread" : ""}${resizing ? " is-resizing" : ""}`} style={{ "--inbox-list-w": `${listWidth}px` } as React.CSSProperties}><div className="inbox-resizer" role="separator" aria-orientation="vertical" aria-label={t("portal.inbox.resizeList")} aria-valuemin={LIST_WIDTH.min} aria-valuemax={LIST_WIDTH.max} aria-valuenow={listWidth} tabIndex={0} title={t("portal.inbox.resizeList")} onPointerDown={startResize} onPointerMove={moveResize} onPointerUp={endResize} onPointerCancel={endResize} onKeyDown={keyResize} onDoubleClick={() => setListWidth(defaultListWidth())} /><aside onScroll={onListScroll}>
      <div className="inbox-search"><Search size={16} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("inbox.searchPlaceholder")} />{status !== "archived" && <div className="inbox-filter-wrap" ref={filterRef}><button type="button" className={`inbox-filter-toggle${filtersOpen ? " open" : ""}${channelFilter || unansweredOnly ? " active" : ""}`} onClick={() => { setFiltersOpen((v) => !v); setSourceOpen(false); }} title={t("inbox.filters")} aria-label={t("inbox.filters")} aria-haspopup="dialog" aria-expanded={filtersOpen}><Filter size={16} /></button>{filtersOpen && <div className="inbox-filter-pop" role="dialog" aria-label={t("inbox.filterTitle")}>
        <h4>{t("inbox.filterTitle")}</h4>
        <span className="pop-label">{t("inbox.filterChatState")}</span>
        <div className="pop-options">
          <button type="button" className={!unansweredOnly ? "selected" : ""} aria-pressed={!unansweredOnly} onClick={() => { setUnansweredOnly(false); clearSelection(); }}><span>{t("inbox.allStatus")}</span>{!unansweredOnly && <Check size={16} />}</button>
          <button type="button" className={unansweredOnly ? "selected" : ""} aria-pressed={unansweredOnly} onClick={() => { setUnansweredOnly(true); clearSelection(); }}><span>{t("inbox.unanswered")}</span>{summary && summary.unanswered > 0 && <em>{summary.unanswered}</em>}{unansweredOnly && <Check size={16} />}</button>
        </div>
        <span className="pop-label">{t("inbox.filterSources")}</span>
        <div className="pop-select">
          <button type="button" className="pop-select-trigger" aria-haspopup="listbox" aria-expanded={sourceOpen} onClick={() => setSourceOpen((v) => !v)}><span>{channelFilter ? channelLabel(channelFilter) : t("inbox.allSources")}</span><ChevronDown size={16} /></button>
          {sourceOpen && <ul className="pop-select-list" role="listbox" aria-label={t("inbox.filterSources")}>
            {sourceOptions.length === 0 ? <li className="pop-select-empty">{t("inbox.connectFirstChannel")}</li> : <>
              <li><button type="button" role="option" aria-selected={!channelFilter} onClick={() => { setChannelFilter(""); clearSelection(); setSourceOpen(false); }}><span>{t("inbox.allSources")}</span>{!channelFilter && <Check size={15} />}</button></li>
              {sourceOptions.map((value) => <li key={value}><button type="button" role="option" aria-selected={channelFilter === value} onClick={() => { setChannelFilter(value); clearSelection(); setSourceOpen(false); }}><span className={`channel-dot ${value}`}>{channelIcon(value)}</span><span>{channelLabel(value)}</span>{channelFilter === value && <Check size={15} />}</button></li>)}
            </>}
          </ul>}
        </div>
      </div>}</div>}</div>
      {status === "archived" ? <div className="archive-head">
        <button type="button" className="text-button" onClick={() => switchStatus("all")}><ArrowLeft size={14} /> {t("portal.inbox.archive.back")}</button>
        <strong><Archive size={14} /> {t("portal.inbox.status.archived")}{summary ? ` · ${summary.archived}` : ""}</strong>
        {visibleItems.length > 0 && can("inbox.delete") && <div className="archive-tools">
          <button type="button" className="text-button" onClick={() => setPicked(picked.length === visibleItems.length ? [] : visibleItems.map((item) => item.id))}>{picked.length === visibleItems.length ? <CheckSquare size={14} /> : <Square size={14} />} {t("portal.inbox.archive.selectAll")}</button>
          <button type="button" className="text-button danger-text" disabled={picked.length === 0} onClick={() => { setConfirmWord(""); setDeleting("picked"); }}><Trash2 size={14} /> {t("portal.inbox.archive.deletePicked", { count: String(picked.length) })}</button>
        </div>}
      </div> : null}
      {visibleItems.map((item) => <div key={item.id} className={status === "archived" ? "inbox-row pickable" : "inbox-row"}>{status === "archived" && <label className="row-pick"><input type="checkbox" checked={picked.includes(item.id)} onChange={(e) => setPicked(e.target.checked ? [...picked, item.id] : picked.filter((id) => id !== item.id))} aria-label={t("portal.inbox.archive.pick")} /></label>}<button onClick={() => choose(item)} className={`${selected?.id === item.id ? "active" : ""}${item.unread && selected?.id !== item.id ? " unread" : ""}`}><span className="entity-avatar tiny"><UserRound size={15} /></span><span><span className="portal-inbox-row-top"><strong>{item.contact_name || item.title}</strong>{item.unread && selected?.id !== item.id ? <span className="inbox-unread-count" aria-label={t("inbox.unreadCount", { count: item.unread_count ?? 0 })}>{(item.unread_count ?? 0) > 99 ? "99+" : item.unread_count}</span> : <time>{formatWhen(item.last_inbound_at ?? item.updated_at, lang)}</time>}</span><small className="portal-inbox-preview">{item.preview || t("portal.inbox.list.noMessages")}</small><small className="inbox-row-meta"><span className="lead-number">#{item.number}</span><span className={`channel-dot ${item.channel}`}>{channelIcon(item.channel)}</span> {channelLabel(item.channel)}{item.account_label && <span className="account-badge" title={item.account_label}>{item.account_label}</span>} <span className={`mini-badge ${item.mode}`}>{item.mode === "human" ? (item.assignee_name || t("portal.inbox.list.humanSupport")) : t("portal.inbox.list.aiAgent")}</span>{item.team_name && <span className="mini-badge team">{item.team_name}</span>}</small></span></button></div>)}
      {!items.length && <div className="no-conversations">{t("inbox.empty")}</div>}
      {status === "resolved" && !loadingMore && !hasMore && visibleItems.length > 1 && can("inbox.delete") && <div className="inbox-archive-link footer"><button type="button" className="text-button" onClick={() => setArchivingAll(true)}><Archive size={13} /> {t("portal.inbox.archive.archiveAll")}</button></div>}
      {items.length > 0 && (hasMore || loadingMore || (listTotal !== null && listTotal > LIMIT)) && <div className="list-foot">
        {loadingMore ? <span><LoaderCircle className="spin" size={14} /> {t("portal.contacts.list.loadingMore")}</span>
          : hasMore ? <span>{t("portal.inbox.list.showing", { shown: items.length, total: listTotal ?? items.length })}</span>
          : <span>{t("portal.inbox.list.allLoaded", { total: listTotal ?? items.length })}</span>}
      </div>}
    </aside><section className="drop-target" {...dropProps}>{overlay}{!selected && <EmptyState icon={<Inbox />} title={t("portal.inbox.empty.title")} description={t("portal.inbox.empty.description")} />}{selected && <><header><button type="button" className="icon-button inbox-back" onClick={() => clearSelection("push")} aria-label={t("common.back")} title={t("common.back")}><ArrowLeft size={16} /></button><div><strong>{selected.contact_name || selected.title}<span className="lead-number">#{selected.number}</span></strong><small className="portal-channel-line">{channelIcon(selected.channel)} {channelLabel(selected.channel)}{selected.account_label && <span className="account-badge" title={selected.account_label}>{selected.account_label}</span>}{selected.archived_at && <span className="mini-badge resolved"><Archive size={11} /> {t("portal.inbox.conversation.archivedBadge")}</span>}{selected.channel === "whatsapp_cloud" && !selected.reply_window_open && <span className="window-pill closed"><Clock size={11} /> {selected.reply_window_until ? t("portal.inbox.window.closed") : t("portal.inbox.window.neverWrote")}</span>}</small></div><div className="thread-actions"><button type="button" className="icon-button" title={t("portal.inbox.copyLink")} aria-label={t("portal.inbox.copyLink")} onClick={() => { void navigator.clipboard?.writeText(`${window.location.origin}${portalPath(urlBase, "inbox", selected.number)}`).then(() => toast.success(t("portal.inbox.linkCopied"))); }}><Link2 size={16} /></button>{enabled("teams") && teams.length > 0 && <label className="assignee-picker team-picker"><span>{t("portal.teams.picker")}</span><select aria-label={t("portal.teams.picker")} title={t("portal.teams.picker")} value={selected.team_id ?? ""} onChange={(e) => setConversationTeam(e.target.value)}><option value="">{t("portal.teams.pickerNone")}</option>{teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select></label>}{selected.mode === "human" && <label className="assignee-picker"><span>{t("portal.inbox.assignment.label")}</span><select aria-label={t("portal.inbox.assignment.label")} title={t("portal.inbox.assignment.label")} value={selected.assignee_id ?? ""} onChange={(e) => e.target.value && assignTo(e.target.value)}>{!selected.assignee_id && <option value="">{t("portal.inbox.assignment.pick")}</option>}{members.map((member) => <option key={member.id} value={member.id}>{memberLabel(member)}</option>)}</select></label>}{selected.archived_at && can("inbox.delete") && <><button className="status-toggle resolved" onClick={() => setArchived(false)}><ArchiveRestore size={15} /> {t("portal.inbox.conversation.restore")}</button><button className="status-toggle danger" onClick={() => { setConfirmWord(""); setDeleting(selected); }}><Trash2 size={15} /> {t("portal.inbox.conversation.delete")}</button></>}{selected.contact_id && !selected.archived_at && can("contacts.manage") && <button className="icon-button" onClick={() => setBlockingContact(true)} title={t("portal.inbox.conversation.blockContact")} aria-label={t("portal.inbox.conversation.blockContact")}><Ban size={16} /></button>}<button className="icon-button" onClick={() => setMediaOpen(true)} title={t("chat.sharedContent")} aria-label={t("chat.sharedContent")}><Images size={16} /></button></div></header><div className="portal-messages" ref={messagesRef}>{selected.messages?.map((message, index) => {
              if (message.kind === "activity") {
                return <div key={message.id} className="activity-line"><span>{activityText(message)}</span><time>{formatTime(message.created_at, lang)}</time></div>;
              }
              const prev = index > 0 ? selected.messages![index - 1] : null;
              const grouped = Boolean(prev && prev.kind !== "activity" && prev.role === message.role && prev.sender_name === message.sender_name);
              const stamp = formatTime(message.created_at, lang);
              const hasAudio = message.attachments?.some((a) => a.kind === "audio");
              const mine = message.role === "assistant";
              return <article key={message.id} className={`${message.role}${mine ? " mine" : ""}${mine && message.sender_type === "ai" ? " ai" : ""}${grouped ? " grouped" : ""}`}>
                {!grouped && <small>{message.sender_name || (message.role === "assistant" ? t("portal.inbox.conversation.agent") : t("portal.inbox.conversation.visitor"))}</small>}
                {canReply && gesturesAvailable && <span className="bubble-actions">
                  {message.role === "user" && <button type="button" title={t("portal.inbox.conversation.react")} aria-label={t("portal.inbox.conversation.react")} onClick={() => setReactingTo(reactingTo === message.id ? null : message.id)}><SmilePlus size={14} /></button>}
                  <button type="button" title={t("portal.inbox.conversation.reply")} aria-label={t("portal.inbox.conversation.reply")} onClick={() => { setQuoting(message); replyInputRef.current?.focus(); }}><Reply size={14} /></button>
                </span>}
                <MessageAttachments attachments={message.attachments} urlFor={attachmentUrl} gallery={gallery} stamp={stamp} />
                {message.content && <p><QuotedSnippet messages={selected.messages ?? []} quotedId={message.quoted_message_id} /><RichText text={message.content} /><time className="msg-time">{stamp}{mine && (selected.channel === "whatsapp_cloud" || isSocialChannel(selected.channel)) && <DeliveryTicks status={message.delivery_status} error={message.delivery_error} />}</time>{mine && message.delivery_status === "failed" && message.delivery_error && <span className="msg-error">{message.delivery_error}</span>}</p>}
                <ReactionBadge emoji={message.reaction} />
                <ReactionBadge emoji={message.incoming_reaction} incoming />
                {!message.content && !hasAudio && message.attachments?.length ? <time className="msg-time bare">{stamp}</time> : null}
                {reactingTo === message.id && <ReactionPicker current={message.reaction} removeLabel={t("portal.inbox.conversation.removeReaction")} onPick={(emoji) => sendReaction(message, emoji)} />}
              </article>;
            })}</div><PhonePauseNotice conversation={selected} onKeepManual={() => setMode("human")} /><SocialReplyNotice conversation={selected} blocked={policy.blocked} humanOnly={policy.humanOnly} />{error && <Alert>{error}</Alert>}{pendingFile && <PendingAttachment file={pendingFile} onCancel={() => setPendingFile(null)} />}{quoting && <div className="composer-quote"><Reply size={14} /><span><strong>{t("portal.inbox.conversation.replyingTo", { name: quoting.sender_name || (quoting.role === "assistant" ? t("portal.inbox.conversation.agent") : t("portal.inbox.conversation.visitor")) })}</strong><small>{(quoting.content || "").slice(0, 140)}</small></span><button type="button" onClick={() => setQuoting(null)} aria-label={t("portal.inbox.conversation.cancelReply")} title={t("portal.inbox.conversation.cancelReply")}><X size={14} /></button></div>}{selected.archived_at ? <div className="portal-composer window-closed archive-bar"><div><strong>{t("portal.inbox.conversation.archivedLocked")}</strong></div></div> : windowClosed ? <div className="portal-composer window-closed"><div><strong>{selected.reply_window_until ? t("portal.inbox.window.closed") : t("portal.inbox.window.neverWrote")}</strong><small>{t("portal.inbox.window.closedHint")}</small></div><button type="button" className="button primary" onClick={() => setTemplateOpen(true)} disabled={!templatesSupported}><FileText size={16} /> {t("portal.inbox.window.sendTemplate")}</button></div> : <form ref={composerRef} onSubmit={reply} onReset={() => setDraft("")} className="portal-composer composer-card">{canned.popup}<div className="composer-box">
      <div className="composer-top">
        <div className="composer-menu"><button type="button" className="composer-pill action" aria-haspopup="menu" aria-expanded={composerMenu === "action"} onClick={() => setComposerMenu(composerMenu === "action" ? null : "action")}>{t("portal.inbox.composer.chat")} <ChevronDown size={14} /></button>{composerMenu === "action" && <div className="composer-menu-list" role="menu"><button type="button" role="menuitem" onClick={() => setComposerMenu(null)}><span>{t("portal.inbox.composer.chat")}</span><Check size={15} /></button></div>}</div>
        <span className="composer-via">{t("portal.inbox.composer.via")}</span>
        <div className="composer-menu"><button type="button" className="composer-pill channel" title={t("portal.inbox.composer.sendThrough")} aria-haspopup="menu" aria-expanded={composerMenu === "channel"} onClick={() => setComposerMenu(composerMenu === "channel" ? null : "channel")}><span className={`channel-dot ${selected.channel}`}>{channelIcon(selected.channel)}</span>{channelLabel(selected.channel)} <ChevronDown size={14} /></button>{composerMenu === "channel" && <div className="composer-menu-list" role="menu"><button type="button" role="menuitem" onClick={() => setComposerMenu(null)}><span className={`channel-dot ${selected.channel}`}>{channelIcon(selected.channel)}</span><span>{channelLabel(selected.channel)}</span><Check size={15} /></button></div>}</div>
        <button type="button" className="composer-icon bolt" title={t("portal.inbox.composer.quick")} aria-label={t("portal.inbox.composer.quick")}><Zap size={16} /></button>
      </div>
      <GrowingTextarea ref={replyInputRef} name="content" autoComplete="off" onChange={(e) => { setDraft(e.target.value); canned.onChange(e.target.value); }} onKeyDown={canned.onKeyDown} required={!pendingFile} disabled={!canReply || busy} placeholder={t("portal.inbox.conversation.replyPlaceholder")} />
      <div className="composer-bottom">
        <div className="composer-left">
          <button type="submit" className={`composer-send${draft.trim() || pendingFile ? " ready" : ""}`} disabled={!canReply || busy || (!draft.trim() && !pendingFile)}>{busy ? <LoaderCircle className="spin" size={16} /> : t("portal.inbox.composer.send")}</button>
          <RecordButton onRecorded={sendAttachment} onError={() => setError(t("chat.micDenied"))} disabled={!policy.canRecord || busy} title={t("chat.recordAudio")} titleStop={t("chat.stopRecording")} />
          {(draft.length > 0 || pendingFile || quoting) && <button type="button" className="composer-cancel" onClick={(event) => { event.currentTarget.form?.reset(); setPendingFile(null); setQuoting(null); canned.reset(); }}>{t("portal.inbox.composer.cancel")}</button>}
        </div>
        <div className="composer-right">
          <button type="button" role="switch" aria-checked={selected.mode === "ai"} className={`ai-toggle${selected.mode === "ai" ? " on" : ""}`} title={t("portal.inbox.list.aiAgent")} onClick={() => setMode(selected.mode === "ai" ? "human" : "ai")}><Bot size={15} /> <span>{t("portal.inbox.folders.ai")}</span></button>
          <div className="composer-menu">
            <button type="button" className={`composer-icon plus${composerMenu === "plus" || composerMenu === "emoji" ? " open" : ""}`} title={t("portal.inbox.composer.more")} aria-label={t("portal.inbox.composer.more")} aria-haspopup="menu" aria-expanded={composerMenu === "plus" || composerMenu === "emoji"} onClick={() => setComposerMenu(composerMenu === "plus" || composerMenu === "emoji" ? null : "plus")}><Plus size={20} /></button>
            {composerMenu === "plus" && <div className="composer-menu-list up" role="menu">
              <button type="button" role="menuitem" disabled={!canReply || busy} onClick={() => setComposerMenu("emoji")}><Smile size={18} /><span>{t("portal.inbox.composer.emoji")}</span></button>
              <button type="button" role="menuitem" onClick={() => setComposerMenu(null)}><CalendarIcon size={18} /><span>{t("portal.inbox.composer.schedule")}</span></button>
              <button type="button" role="menuitem" disabled={!policy.canAttach || busy} onClick={() => { setComposerMenu(null); fileInputRef.current?.click(); }}><Paperclip size={18} /><span>{t("chat.attachFile")}</span></button>
              <button type="button" role="menuitem" onClick={() => setComposerMenu(null)}><Navigation size={18} /><span>{t("portal.inbox.composer.navigate")}</span></button>
            </div>}
            {composerMenu === "emoji" && <div className="composer-emoji" role="menu">{EMOJIS.map((emoji) => <button type="button" key={emoji} role="menuitem" onClick={() => { insertEmoji(emoji); setComposerMenu(null); }}>{emoji}</button>)}</div>}
            <input ref={fileInputRef} type="file" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) setPendingFile(file); event.currentTarget.value = ""; }} />
          </div>
        </div>
      </div>
    </div></form>}<MediaPanel open={mediaOpen} onClose={() => setMediaOpen(false)} messages={selected.messages ?? []} urlFor={attachmentUrl} /><TemplatePicker base={base} open={templateOpen} title={t("portal.inbox.window.sendTemplate")} contactValues={contactValues} onClose={() => setTemplateOpen(false)} onSend={replyWithTemplate} /></>}</section></div>}</section>
    {/* Confirmations for the actions that leave a mark: archiving every resolved conversation, deleting one or several, blocking the contact. */}
    <Modal open={archivingAll} title={t("portal.inbox.archive.archiveAllTitle")} description={t("portal.inbox.archive.archiveAllCopy", { count: String(summary?.resolved ?? visibleItems.length) })} onClose={() => setArchivingAll(false)}>
      <div className="modal-form">{error && <Alert>{error}</Alert>}<div className="modal-actions"><button type="button" className="button" onClick={() => setArchivingAll(false)}>{t("common.cancel")}</button><button type="button" className="button primary" disabled={bulkBusy} onClick={archiveAllResolved}>{bulkBusy ? <LoaderCircle className="spin" size={16} /> : <><Archive size={15} /> {t("portal.inbox.archive.archiveAll")}</>}</button></div></div>
    </Modal>
    <Modal open={deleting !== null} title={deleting === "all" ? t("portal.inbox.archive.deleteAllTitle") : deleting === "picked" ? t("portal.inbox.archive.deletePickedTitle", { count: String(picked.length) }) : t("portal.inbox.archive.deleteTitle", { title: deleting && typeof deleting === "object" ? deleting.contact_name || deleting.title : "" })} description={deleting === "all" ? t("portal.inbox.archive.deleteAllCopy", { count: String(summary?.archived ?? 0) }) : t("portal.inbox.archive.deleteCopy")} onClose={() => setDeleting(null)}>
      <form className="modal-form" onSubmit={(e) => { e.preventDefault(); if (deleteArmed) deleteConversations(); }}>
        <Alert type="error">{t("portal.inbox.archive.deleteUltimatum")}</Alert>
        <label>{t("portal.inbox.archive.typeToConfirm", { word: t("portal.inbox.archive.deleteWord") })}<input value={confirmWord} onChange={(e) => setConfirmWord(e.target.value)} autoFocus autoComplete="off" /></label>
        {error && <Alert>{error}</Alert>}
        <div className="modal-actions"><button type="button" className="button" onClick={() => setDeleting(null)}>{t("common.cancel")}</button><button className="button danger" disabled={bulkBusy || !deleteArmed}>{bulkBusy ? <LoaderCircle className="spin" size={16} /> : <><Trash2 size={15} /> {t("portal.inbox.conversation.delete")}</>}</button></div>
      </form>
    </Modal>
    <Modal open={blockingContact && Boolean(selected?.contact_id)} title={t("portal.contacts.blockTitle", { name: selected?.contact_name || selected?.title || "" })} onClose={() => setBlockingContact(false)}>
      <div className="modal-form"><p className="muted">{t("portal.contacts.blockCopy")}</p><p className="muted">{t("portal.contacts.blockUnblockCopy")}</p>{error && <Alert>{error}</Alert>}<div className="modal-actions"><button type="button" className="button" onClick={() => setBlockingContact(false)}>{t("common.cancel")}</button><button type="button" className="button danger" disabled={bulkBusy} onClick={blockContact}>{bulkBusy ? <LoaderCircle className="spin" size={16} /> : <><Ban size={15} /> {t("portal.contacts.block")}</>}</button></div></div>
    </Modal>
  </main>;
}
