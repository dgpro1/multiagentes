"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Ban, Bot, Calendar as CalendarIcon, Check, ChevronDown, Clock, Filter, Inbox as InboxIcon, LoaderCircle, MapPin, Paperclip, Plus, Reply, Search, Smile, UserRound, X } from "lucide-react";
import { EmptyState, Modal, PageHead } from "@/components/ui";
import { EMOJIS } from "@/components/client-inbox";
import { PendingAttachment, RecordButton, useFileDrop, type GalleryImage } from "@/components/attachments";
import { LocationComposer } from "@/components/location-composer";
import { LeadCard } from "@/components/lead-card/lead-card";
import { MessageThread } from "@/components/messages/message-thread";
import { UnifiedComposerTop, type ComposerMode } from "@/components/unified-composer-top";
import { AppointmentModal } from "@/components/appointment-modal";
import { ScheduleMessageModal } from "@/components/schedule-message-modal";
import { ScheduledMessagesBanner } from "@/components/scheduled-messages-banner";
import { VariablesPopover } from "@/components/variables-popover";
import { ReactionPicker } from "@/components/message-gestures";
import { LeadHeaderButton } from "@/components/lead-card/avatar-button";
import { LeadScopeProvider, agencyLeadScope } from "@/components/lead-card/scope";
import { useLeadPanel } from "@/components/lead-card/use-lead-panel";
import { GrowingTextarea } from "@/components/growing-textarea";
import { ListRowsSkeleton } from "@/components/skeleton";
import { LeadRowActions, pinnedFirst } from "@/components/lead-row-actions";
import { useToast } from "@/components/toast";
import { ChannelDots, ChannelIcon, channelLabel as labelForChannel, INBOX_CHANNELS, isSocialChannel, leadChannels } from "@/lib/channels";
import { useAttachmentOwners, useReplyVia } from "@/lib/linked-threads";
import { PhonePauseNotice, SocialReplyNotice, useReplyPolicy } from "@/components/reply-policy";
import { api, ApiError, apiUrl, messageFrom } from "@/lib/api";
import { formatWhen, isNearBottom, isSameOpenThread } from "@/lib/datetime";
import { useLanguage, useT } from "@/lib/i18n";
import { agencyInboxLead, agencyInboxPath } from "@/lib/routes";
import type { Agent, Attachment, Client, Conversation, ConversationInbox, Message, ScheduledMessage } from "@/types";

const LIMIT = 30;
const POLL_MS = 8000;

export default function InboxPage() {
  const t = useT();
  const { lang } = useLanguage();
  const toast = useToast();
  const { lead: leadSegments } = useParams<{ lead?: string[] }>();
  const router = useRouter();
  // The address is the source of truth for the open lead, the way Kommo does it:
  // this list spans every client, so a lead here is named by its client and its
  // short number (`/inbox/{slug}/{number}`). Reload, Back/Forward and a shared
  // link all land on the same lead, and the list behind it is still the whole
  // agency, so opening a lead no longer leaves it.
  const leadPath = leadSegments?.join("/") ?? "";
  const [agents, setAgents] = useState<Agent[]>([]);
  const [items, setItems] = useState<ConversationInbox[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [agentId, setAgentId] = useState("");
  const [channel, setChannel] = useState("");
  const [tab, setTab] = useState<"all" | "pending" | "human" | "ai">("all");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [locating, setLocating] = useState(false);
  const [blockingContact, setBlockingContact] = useState(false);
  // The emoji palette under a bubble, and the message being answered: the same
  // two pieces the other inboxes carry over a bubble on hover.
  const [reactingTo, setReactingTo] = useState<string | null>(null);
  const [quoting, setQuoting] = useState<Message | null>(null);
  const [appointmentModalOpen, setAppointmentModalOpen] = useState(false);
  const [scheduledModalOpen, setScheduledModalOpen] = useState(false);
  const [scheduledMessages, setScheduledMessages] = useState<ScheduledMessage[]>([]);
  // A lead that absorbed others has several threads: the composer picks one and every reply names it.
  const replyVia = useReplyVia(selected);
  const owners = useAttachmentOwners(selected);
  const policy = useReplyPolicy(replyVia.policyConversation);
  // The lead card beside the thread; a lead's data is reached through its client.
  const { open: leadPanelOpen, setOpen: setLeadOpen, overlay: leadOverlay, attachLayout: attachLead } = useLeadPanel();
  const closeLead = useCallback(() => setLeadOpen(false), [setLeadOpen]);
  const selectedClientId = selected?.client_id;
  const leadScope = useMemo(() => (selectedClientId ? agencyLeadScope(selectedClientId) : null), [selectedClientId]);
  const leadOpen = Boolean(selected) && leadPanelOpen;

  useEffect(() => { api<Agent[]>("/agents").then(setAgents).catch(() => {}); }, []);
  useEffect(() => { const id = setTimeout(() => setSearch(searchInput), 300); return () => clearTimeout(id); }, [searchInput]);

  const channelLabel = (value: string) => labelForChannel(value, t);
  const channelIcon = (value: string) => <ChannelIcon channel={value} />;

  // Where a row lives: its own address in this inbox, which keeps the whole
  // agency list behind it. A row from a response that carried no slug has no
  // address to build, so it falls back to the thread itself, which this page
  // moves to that address as soon as it reads the lead.
  const leadAddress = useCallback(
    (item: ConversationInbox) => (item.client_slug ? agencyInboxPath(item.client_slug, item.number) : `/inbox?conversation=${item.id}`),
    [],
  );

  const buildParams = useCallback((offsetValue: number) => {
    const params = new URLSearchParams();
    if (agentId) params.set("agent_id", agentId);
    if (channel) params.set("channel", channel);
    if (tab === "human" || tab === "ai") params.set("mode", tab);
    // The tab is about a lead nobody has answered, which is what `pending`
    // answers, not about who has looked at it (`unread`, kept for the count).
    if (tab === "pending") params.set("pending", "1");
    if (search) params.set("search", search);
    params.set("limit", String(LIMIT));
    params.set("offset", String(offsetValue));
    return params.toString();
  }, [agentId, channel, tab, search]);

  const selectedIdRef = useRef<string | null>(null);
  // Which lead the address asked for and the thread now showing agree on. Without
  // it the list refresh would reopen the same lead on every poll.
  const openedLeadRef = useRef("");
  // A lead the server could not produce, so a dead address is not chased twice.
  const failedLeadRef = useRef("");
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

  const loadFirst = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoading(true);
    try {
      const rows = await api<ConversationInbox[]>(`/conversations/inbox?${buildParams(0)}`);
      setItems(rows); setOffset(rows.length); setHasMore(rows.length === LIMIT);
    } catch (err) {
      if (!opts?.silent) toast.error(messageFrom(err));
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }, [buildParams, toast]);

  // The thread was deleted elsewhere (another operator, a cleanup): close it
  // and drop the stale row instead of erroring or polling it forever. The
  // address goes back to the list, which is where a lead that no longer exists
  // can still be understood.
  const closeGoneThread = useCallback((id: string) => {
    openedLeadRef.current = "";
    selectedIdRef.current = null;
    setSelected(null);
    setItems((rows) => rows.filter((row) => row.id !== id));
    toast.error(t("inbox.threadGone"));
    router.replace(agencyInboxPath());
  }, [router, toast, t]);

  const refreshScheduledMessages = useCallback(async (targetId?: string) => {
    const id = targetId || selectedIdRef.current;
    if (!id) {
      setScheduledMessages([]);
      return;
    }
    try {
      const data = await api<ScheduledMessage[]>(`/conversations/${id}/scheduled-messages?status=pending`);
      if (selectedIdRef.current === id) {
        setScheduledMessages(data || []);
      }
    } catch {
      // ignore
    }
  }, []);

  const refreshSelected = useCallback(async () => {
    const id = selectedIdRef.current;
    if (!id) return;
    try {
      const conv = await api<Conversation>(`/conversations/${id}`);
      if (selectedIdRef.current !== id) return;
      // An id of a thread absorbed by another lead answers with the primary.
      if (conv.id !== id) selectedIdRef.current = conv.id;
      setSelected((prev) => {
        if (isSameOpenThread(prev, conv)) return prev;
        setItems((rows) => rows.map((row) => (row.id === id ? { ...row, unread: false, unread_count: 0 } : row)));
        api(`/conversations/${id}/read`, { method: "POST" }).catch(() => {});
        return conv;
      });
      void refreshScheduledMessages(conv.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404 && selectedIdRef.current === id) {
        closeGoneThread(id);
        return;
      }
      // Other poll failures should not interrupt the open thread.
    }
  }, [closeGoneThread, refreshScheduledMessages]);

  useEffect(() => { loadFirst(); }, [loadFirst]);

  // Live refresh of the first page and the open thread (skipped once the user scrolls into older pages).
  useEffect(() => {
    const id = setInterval(() => {
      if (offset <= LIMIT) {
        loadFirst({ silent: true });
        refreshSelected();
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [loadFirst, refreshSelected, offset]);

  async function loadMore() {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const rows = await api<ConversationInbox[]>(`/conversations/inbox?${buildParams(offset)}`);
      setItems((prev) => [...prev, ...rows]); setOffset((o) => o + rows.length); setHasMore(rows.length === LIMIT);
    } catch (err) { toast.error(messageFrom(err)); } finally { setLoadingMore(false); }
  }

  function onScroll(event: React.UIEvent<HTMLElement>) {
    const el = event.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) loadMore();
  }

  const choose = useCallback(async (id: string) => {
    setPendingFile(null);
    if (composerRef.current) composerRef.current.value = "";
    selectedIdRef.current = id;
    try {
      const detail = await api<Conversation>(`/conversations/${id}`);
      selectedIdRef.current = detail.id;
      setSelected(detail);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) { closeGoneThread(id); return; }
      throw err;
    }
    setItems((rows) => rows.map((row) => (row.id === id ? { ...row, unread: false, unread_count: 0 } : row)));
    api(`/conversations/${id}/read`, { method: "POST" }).catch(() => {});
  }, [closeGoneThread]);

  // Resolving a lead from its row: the dot goes and, when the list is filtered
  // to the leads nobody has answered, the row leaves it. It stays in "all",
  // where a resolved lead is still a lead. If the contact writes again the
  // case reopens by itself (services/conversation_state.py) and the dot is back.
  const resolveRow = useCallback(async (row: ConversationInbox) => {
    try {
      await api<Conversation>(`/conversations/${row.id}/status`, { method: "PATCH", body: JSON.stringify({ status: "resolved" }) });
      setItems((rows) => {
        const next = rows.map((item) => (item.id === row.id ? { ...item, awaiting_reply: false } : item));
        return tab === "pending" ? next.filter((item) => item.id !== row.id) : next;
      });
    } catch (err) {
      toast.error(messageFrom(err));
    }
  }, [tab, toast]);

  // The pin moves the row straight away and the answer confirms it; a failure
  // puts the row back where it was rather than leaving a lie on screen.
  const togglePinRow = useCallback(async (row: ConversationInbox) => {    const pinned = !row.pinned_at;
    const at = pinned ? new Date().toISOString() : null;
    setItems((rows) => pinnedFirst(rows.map((item) => (item.id === row.id ? { ...item, pinned_at: at } : item))));
    try {
      const conv = await api<Conversation>(`/conversations/${row.id}/pin`, { method: "PATCH", body: JSON.stringify({ pinned }) });
      setItems((rows) => pinnedFirst(rows.map((item) => (item.id === row.id ? { ...item, pinned_at: conv.pinned_at ?? null } : item))));
    } catch (err) {
      setItems((rows) => rows.map((item) => (item.id === row.id ? { ...item, pinned_at: row.pinned_at } : item)));
      toast.error(messageFrom(err));
    }
  }, [toast]);

  // The lead an address names, found in the list when it is already loaded (a row
  // the operator clicked) and otherwise asked for by the same route the client
  // inbox uses, so a shared link works on a lead this page has not listed.
  const resolveLead = useCallback(async (clientSlug: string, leadNumber: number) => {
    const row = items.find((item) => item.client_slug === clientSlug && item.number === leadNumber);
    if (row) return row;
    const client = await api<Client>(`/clients/by-slug/${encodeURIComponent(clientSlug)}`);
    return api<Conversation>(`/clients/${client.id}/conversations/number/${leadNumber}`);
  }, [items]);

  // Open the lead the address names, and close it when the address stops naming
  // one. Navigating between rows only changes the address, so the list, its
  // filters and its scroll stay exactly as they were.
  useEffect(() => {
    const target = agencyInboxLead(leadPath);
    if (!target) {
      openedLeadRef.current = "";
      // Back on the list, a lead that could not be read is worth asking again:
      // the address that failed may be pasted once more.
      failedLeadRef.current = "";
      selectedIdRef.current = null;
      setSelected(null);
      return;
    }
    const key = `${target.clientSlug}/${target.leadNumber}`;
    // A lead that could not be read is not asked for again: the address already
    // went back to the list, and a second try would only be the same 404.
    if (openedLeadRef.current === key || failedLeadRef.current === key) return;
    openedLeadRef.current = key;
    setPendingFile(null);
    if (composerRef.current) composerRef.current.value = "";
    resolveLead(target.clientSlug, target.leadNumber).then((found) => {
      if (openedLeadRef.current !== key) return;
      // A lead absorbed by another one answers with the lead that replaced it:
      // the address follows it rather than opening a thread nobody sends to.
      if (found.number !== target.leadNumber) {
        router.replace(agencyInboxPath(target.clientSlug, found.number));
        return;
      }
      void choose(found.id);
    }).catch(() => {
      if (openedLeadRef.current !== key) return;
      failedLeadRef.current = key;
      router.replace(agencyInboxPath());
    });
  }, [leadPath, resolveLead, choose, router]);

  // A board card still opens a thread through `?conversation=<id>`. Move it to
  // the lead's own address once, so the address is right from then on.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("conversation");
    if (!id) return;
    api<Conversation>(`/conversations/${id}`).then(async (conv) => {
      const client = await api<Client>(`/clients/${conv.client_id}`);
      router.replace(agencyInboxPath(client.portal_slug, conv.number));
    }).catch(() => {
      // Nothing to point at: drop the stale parameter and stay on the list.
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.hash}`);
    });
  }, [router]);

  async function toggleMode(next: "ai" | "human") {
    if (!selected) return;
    setSelected(await api<Conversation>(`/conversations/${selected.id}/mode`, { method: "PATCH", body: JSON.stringify({ mode: next }) }));
    loadFirst({ silent: true });
  }

  /** React to a message, or take the reaction back. The message may live on any
   * thread of the lead, so the reaction names the thread it belongs to. */
  async function sendReaction(message: Message, emoji: string) {
    if (!selected) return;
    const next = emoji === message.reaction ? "" : emoji;
    setReactingTo(null);
    try {
      setSelected(await api<Conversation>(`/conversations/${selected.id}/messages/${message.id}/reaction`, {
        method: "POST",
        body: JSON.stringify({ emoji: next, via_conversation_id: message.conversation_id }),
      }));
    } catch (err) {
      toast.error(messageFrom(err));
    }
  }

  /** Block the open lead's contact, asked for from the lead card. Blocking
   *  takes the conversation out of every inbox, so the thread is closed and the
   *  list is read again instead of left showing a lead that is gone. */
  async function blockContact(blocked: boolean) {
    if (!selected?.contact_id || !selected.client_id) return;
    setBusy(true);
    try {
      await api(`/clients/${selected.client_id}/contacts/${selected.contact_id}/block`, {
        method: "POST",
        body: JSON.stringify({ blocked }),
      });
      setBlockingContact(false);
      if (blocked) {
        closeLead();
        closeThread();
      }
      loadFirst({ silent: true });
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  const composerRef = useRef<HTMLTextAreaElement>(null);
  // Closing a lead is going back to the list, and the list is the bare address.
  const closeThread = useCallback(() => {
    openedLeadRef.current = "";
    selectedIdRef.current = null;
    setSelected(null);
    router.push(agencyInboxPath());
  }, [router]);
  const [composerMode, setComposerMode] = useState<ComposerMode>("chat");
  const [variablesOpen, setVariablesOpen] = useState(false);
  const [variablesQuery, setVariablesQuery] = useState("");
  const [draft, setDraft] = useState("");
  // The filter popover, its two pickers and the composer's "+" menu, as on a client's own inbox.
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [picker, setPicker] = useState<null | "agent" | "channel">(null);
  const [composerMenu, setComposerMenu] = useState<null | "plus" | "emoji">(null);
  const filterRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!filtersOpen && !composerMenu) return;
    const close = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent) {
        if (event.key === "Escape") { setFiltersOpen(false); setPicker(null); setComposerMenu(null); }
        return;
      }
      const target = event.target as Node;
      if (filterRef.current && !filterRef.current.contains(target)) { setFiltersOpen(false); setPicker(null); }
      if (formRef.current && !formRef.current.contains(target)) setComposerMenu(null);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [filtersOpen, composerMenu]);

  function insertTextAtCursor(text: string, replaceTriggerChar?: string) {
    const field = composerRef.current;
    if (!field) return;
    const start = field.selectionStart ?? field.value.length;
    const end = field.selectionEnd ?? start;
    const full = field.value;
    const before = full.slice(0, start);
    const after = full.slice(end);

    let newBefore = before;
    const lastBracket = before.lastIndexOf("[");
    if (lastBracket !== -1) {
      const textBetween = before.slice(lastBracket + 1);
      if (!textBetween.includes("]") && !textBetween.includes("\n")) {
        newBefore = before.slice(0, lastBracket);
      }
    } else if (replaceTriggerChar && before.endsWith(replaceTriggerChar)) {
      newBefore = before.slice(0, before.length - replaceTriggerChar.length);
    }

    const nextVal = newBefore + text + after;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
    if (setter) {
      setter.call(field, nextVal);
    } else {
      field.value = nextVal;
    }
    field.dispatchEvent(new Event("input", { bubbles: true }));
    setDraft(nextVal);
    setVariablesQuery("");
    setVariablesOpen(false);
    const newPos = newBefore.length + text.length;
    setTimeout(() => {
      field.focus();
      field.setSelectionRange(newPos, newPos);
    }, 0);
  }

  useEffect(() => {
    if (!selected?.id) {
      setScheduledMessages([]);
      return;
    }
    void refreshScheduledMessages(selected.id);
  }, [selected?.id, refreshScheduledMessages]);

  // When there are pending scheduled messages, poll frequently so when the scheduled
  // message is dispatched in background, the banner clears and the message appears live.
  useEffect(() => {
    const id = selected?.id;
    if (!id || scheduledMessages.length === 0) return;
    const timer = setInterval(async () => {
      try {
        const data = await api<ScheduledMessage[]>(`/conversations/${id}/scheduled-messages?status=pending`);
        if (selectedIdRef.current === id) {
          const next = data || [];
          setScheduledMessages(next);
          if (next.length < scheduledMessages.length) {
            void refreshSelected();
            setTimeout(() => { void refreshSelected(); }, 1200);
          }
        }
      } catch {
        // ignore
      }
    }, 2500);
    return () => clearInterval(timer);
  }, [selected?.id, scheduledMessages.length, refreshSelected]);

  async function handleScheduleMessage(content: string, scheduledFor: string) {
    if (!selected) return;
    try {
      await api<ScheduledMessage>(`/conversations/${selected.id}/scheduled-messages`, {
        method: "POST",
        body: JSON.stringify({
          content,
          scheduled_for: scheduledFor,
          via_conversation_id: replyVia.via || undefined,
        }),
      });
      toast.success(t("inbox.scheduleSuccess") || "Mensaje programado con éxito");
      if (draft.trim() === content.trim()) {
        if (composerRef.current) composerRef.current.value = "";
        setDraft("");
      }
      await refreshScheduledMessages();
    } catch (err: unknown) {
      throw new Error(messageFrom(err));
    }
  }

  async function handleCancelScheduledMessage(scheduledId: string) {
    if (!selected) return;
    try {
      await api<ScheduledMessage>(`/conversations/${selected.id}/scheduled-messages/${scheduledId}`, {
        method: "DELETE",
      });
      toast.success(t("inbox.scheduledCancelSuccess") || "Mensaje programado cancelado");
      await refreshScheduledMessages();
    } catch (err: unknown) {
      toast.error(messageFrom(err));
    }
  }

  async function sendNote(content: string) {
    if (!selected || busy || !content.trim()) return;
    setBusy(true);
    try {
      setSelected(await api<Conversation>(`/conversations/${selected.id}/notes`, {
        method: "POST",
        body: JSON.stringify({ content: content.trim() }),
      }));
      if (composerRef.current) composerRef.current.value = "";
      setDraft("");
      setComposerMode("chat");
      loadFirst({ silent: true });
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); composerRef.current?.focus(); }
  }

  async function sendLocation(place: { latitude: number; longitude: number; name: string; address: string }) {
    if (!selected) return;
    setBusy(true);
    try {
      setSelected(await api<Conversation>(`/conversations/${selected.id}/location`, { method: "POST", body: JSON.stringify({ ...place, ...replyVia.payload }) }));
      setLocating(false);
      loadFirst({ silent: true });
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); composerRef.current?.focus(); }
  }
  async function reply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const content = (data.get("content") as string) || draft;
    if (composerMode === "note") {
      await sendNote(content);
      return;
    }
    if (!policy.canReply) return;
    if (pendingFile) {
      const file = pendingFile;
      setPendingFile(null);
      await sendAttachment(file);
      return;
    }
    setBusy(true);
    try {
      setSelected(await api<Conversation>(`/conversations/${selected.id}/reply`, { method: "POST", body: JSON.stringify({ content: content.trim(), quoted_message_id: quoting?.id ?? null, ...replyVia.payload }) }));
      form.reset();
      setDraft("");
      setQuoting(null);
      loadFirst({ silent: true });
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); composerRef.current?.focus(); }
  }

  async function sendAttachment(file?: File) {
    if (!file || !selected || !policy.canAttach || busy) return;
    setBusy(true);
    const caption = (composerRef.current?.value || "").trim();
    try {
      const data = new FormData();
      data.append("file", file);
      if (caption) data.append("caption", caption);
      if (replyVia.multi) data.append("via_conversation_id", replyVia.via);
      setSelected(await api<Conversation>(`/conversations/${selected.id}/reply-media`, { method: "POST", body: data }));
      if (composerRef.current) composerRef.current.value = "";
      loadFirst({ silent: true });
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); composerRef.current?.focus(); }
  }
  const { dropProps, overlay } = useFileDrop(setPendingFile, { enabled: policy.canAttach && !busy, label: t("chat.dropToSend") });

  const selectedId = selected?.id;
  const attachmentUrl = useCallback(
    (attachment: Attachment) => apiUrl(`/conversations/${owners.get(attachment.id) ?? selectedId}/attachments/${attachment.id}`),
    [selectedId, owners],
  );
  const gallery: GalleryImage[] = useMemo(
    () => (selected?.messages ?? []).flatMap((message) =>
      (message.attachments ?? []).filter((a) => a.kind === "image").map((a) => ({ id: a.id, url: attachmentUrl(a), name: a.filename }))
    ),
    [selected, attachmentUrl],
  );

  const tabOptions: { value: typeof tab; label: string }[] = [
    { value: "all", label: t("inbox.allStatus") },
    { value: "pending", label: t("inbox.unanswered") },
    { value: "human", label: t("inbox.statusHuman") },
    { value: "ai", label: t("inbox.statusAi") },
  ];
  const agentName = agents.find((agent) => agent.id === agentId)?.name;

  return <div className="page">
    <PageHead eyebrow={t("inbox.eyebrow")} title={t("inbox.title")} description={t("inbox.description")} />

    {/* The same inbox a client's page and the client portal show, over every
        client of the agency. On a phone the list and the thread take turns on
        screen (the .has-thread rules). */}
    <div className="embedded-portal-view client-inbox-view" style={{ "--portal-color": "#00876c" } as React.CSSProperties}>
    <div
      ref={attachLead}
      className={`portal-inbox${selected ? " has-thread" : ""}${leadOpen ? " has-lead" : ""}${leadOverlay ? " lead-overlay" : ""}`}
      style={{ "--inbox-list-w": "360px", "--portal-color": "#00876c" } as React.CSSProperties}
    >
      <aside onScroll={onScroll}>
        <div className="inbox-search">
          <Search size={16} />
          <input value={searchInput} onChange={(e) => setSearchInput(e.target.value)} placeholder={t("inbox.searchPlaceholder")} />
          <div className="inbox-filter-wrap" ref={filterRef}>
            <button
              type="button"
              className={`inbox-filter-toggle${filtersOpen ? " open" : ""}${agentId || channel || tab !== "all" ? " active" : ""}`}
              onClick={() => { setFiltersOpen((v) => !v); setPicker(null); }}
              title={t("inbox.filters")}
              aria-label={t("inbox.filters")}
              aria-haspopup="dialog"
              aria-expanded={filtersOpen}
            >
              <Filter size={16} />
            </button>
            {filtersOpen && (
              <div className="inbox-filter-pop" role="dialog" aria-label={t("inbox.filterTitle")}>
                <h4>{t("inbox.filterTitle")}</h4>
                <span className="pop-label">{t("inbox.filterChatState")}</span>
                <div className="pop-options">
                  {tabOptions.map((option) => (
                    <button key={option.value} type="button" className={tab === option.value ? "selected" : ""} aria-pressed={tab === option.value} onClick={() => setTab(option.value)}>
                      <span>{option.label}</span>
                      {tab === option.value && <Check size={16} />}
                    </button>
                  ))}
                </div>
                <span className="pop-label">{t("inbox.filterAgent")}</span>
                <div className="pop-select">
                  <button type="button" className="pop-select-trigger" aria-haspopup="listbox" aria-expanded={picker === "agent"} onClick={() => setPicker(picker === "agent" ? null : "agent")}>
                    <span>{agentName ?? t("inbox.allAgents")}</span>
                    <ChevronDown size={16} />
                  </button>
                  {picker === "agent" && (
                    <ul className="pop-select-list" role="listbox" aria-label={t("inbox.filterAgent")}>
                      <li><button type="button" role="option" aria-selected={!agentId} onClick={() => { setAgentId(""); setPicker(null); }}><span>{t("inbox.allAgents")}</span>{!agentId && <Check size={15} />}</button></li>
                      {agents.map((agent) => (
                        <li key={agent.id}><button type="button" role="option" aria-selected={agentId === agent.id} onClick={() => { setAgentId(agent.id); setPicker(null); }}><span>{agent.name}</span>{agentId === agent.id && <Check size={15} />}</button></li>
                      ))}
                    </ul>
                  )}
                </div>
                <span className="pop-label">{t("inbox.filterSources")}</span>
                <div className="pop-select">
                  <button type="button" className="pop-select-trigger" aria-haspopup="listbox" aria-expanded={picker === "channel"} onClick={() => setPicker(picker === "channel" ? null : "channel")}>
                    <span>{channel ? channelLabel(channel) : t("inbox.allSources")}</span>
                    <ChevronDown size={16} />
                  </button>
                  {picker === "channel" && (
                    <ul className="pop-select-list" role="listbox" aria-label={t("inbox.filterSources")}>
                      <li><button type="button" role="option" aria-selected={!channel} onClick={() => { setChannel(""); setPicker(null); }}><span>{t("inbox.allSources")}</span>{!channel && <Check size={15} />}</button></li>
                      {INBOX_CHANNELS.map((value) => (
                        <li key={value}><button type="button" role="option" aria-selected={channel === value} onClick={() => { setChannel(value); setPicker(null); }}><span className={`channel-dot ${value}`}>{channelIcon(value)}</span><span>{channelLabel(value)}</span>{channel === value && <Check size={15} />}</button></li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {loading ? <ListRowsSkeleton rows={7} />
          : items.length ? <>
            {items.map((item) => (
              <div key={item.id} className="inbox-row">
                <Link href={leadAddress(item)} className={`${selected?.id === item.id ? "active" : ""}${item.unread && selected?.id !== item.id ? " unread" : ""}`}>
                  <span className="entity-avatar tiny"><UserRound size={15} /></span>
                  <span>
                    <span className="portal-inbox-row-top">
                      <strong>{item.contact_name || item.title}</strong>
                      {item.unread && selected?.id !== item.id ? (
                        <span className="inbox-unread-count" aria-label={t("inbox.unreadCount", { count: item.unread_count })}>{item.unread_count > 99 ? "99+" : item.unread_count}</span>
                      ) : (
                        <time>{formatWhen(item.last_inbound_at ?? item.updated_at, lang)}</time>
                      )}
                    </span>
                    <small className="portal-inbox-preview">{item.preview || t("inbox.noMessages")}</small>
                    <small className="inbox-row-meta">
                      {item.number !== undefined && <span className="lead-number">#{item.number}</span>}
                      {leadChannels(item).length > 1 ? <ChannelDots channels={leadChannels(item)} t={t} /> : <><span className={`channel-dot ${item.channel}`}>{channelIcon(item.channel)}</span> {channelLabel(item.channel)}</>}
                      {item.account_label && <span className="account-badge" title={item.account_label}>{item.account_label}</span>}
                      {item.visitor_handle && <span className="lead-handle">{item.visitor_handle}</span>}
                      <span className={`mini-badge ${item.mode}`}>{item.mode === "human" ? t("inbox.modeHuman") : item.agent_name || t("inbox.modeAi")}</span>
                    </small>
                  </span>
                </Link>
                <LeadRowActions pinned={Boolean(item.pinned_at)} onResolve={() => resolveRow(item)} onTogglePin={() => togglePinRow(item)} href={leadAddress(item)} />
              </div>
            ))}
            {loadingMore && <div className="no-conversations"><LoaderCircle className="spin" size={15} /></div>}
          </> : <div className="no-conversations">{t("inbox.empty")}</div>}
      </aside>

      <section className="drop-target" {...dropProps}>
        {overlay}
        {!selected ? <EmptyState icon={<InboxIcon />} title={t("portal.inbox.empty.title")} description={t("portal.inbox.empty.description")} />
          : <>
            <header>
              <button type="button" className="icon-button inbox-back" onClick={closeThread} aria-label={t("common.back")} title={t("common.back")}><ArrowLeft size={16} /></button>
              <LeadHeaderButton channel={selected.channel} open={leadOpen} onClick={() => setLeadOpen(!leadPanelOpen)}>
                <div>
                  <strong>{selected.contact_name || selected.title}<span className="lead-number">#{selected.number}</span></strong>
                  <small className="portal-channel-line">
                    {replyVia.multi ? <ChannelDots channels={[...new Set(replyVia.threads.map((thread) => thread.channel))]} t={t} /> : <>{channelIcon(selected.channel)} {channelLabel(selected.channel)}</>}
                    {selected.account_label && <span className="account-badge" title={selected.account_label}>{selected.account_label}</span>}
                    {selected.visitor_handle && <span className="lead-handle">{selected.visitor_handle}</span>}
                    {selected.channel === "whatsapp_cloud" && !selected.reply_window_open && (
                      <span className="window-pill closed"><Clock size={11} /> {selected.reply_window_until ? t("portal.inbox.window.closed") : t("portal.inbox.window.neverWrote")}</span>
                    )}
                  </small>
                </div>
              </LeadHeaderButton>
              <div className="thread-actions" />
            </header>
            <MessageThread
              messages={selected.messages}
              surface="portal"
              t={t}
              lang={lang}
              urlFor={attachmentUrl}
              gallery={gallery}
              channel={selected.channel}
              bubbleActions={{
                enabled: policy.canReply,
                onReact: (message) => setReactingTo(reactingTo === message.id ? null : message.id),
                onReply: (message) => {
                  setQuoting(message);
                  if (message.conversation_id) replyVia.setVia(message.conversation_id);
                  composerRef.current?.focus();
                },
              }}
              reactionPicker={(message) => reactingTo === message.id ? (
                <ReactionPicker current={message.reaction} removeLabel={t("portal.inbox.conversation.removeReaction")} onPick={(emoji) => sendReaction(message, emoji)} />
              ) : null}
              containerRef={messagesRef}
            />
            <PhonePauseNotice conversation={selected} onKeepManual={() => toggleMode("human")} />
            <SocialReplyNotice conversation={selected} blocked={policy.blocked} humanOnly={policy.humanOnly} />
            {pendingFile && <PendingAttachment file={pendingFile} onCancel={() => setPendingFile(null)} />}
            {locating && <LocationComposer busy={busy} disabled={!policy.canReply} onCancel={() => setLocating(false)} onSend={sendLocation} />}
            {quoting && (
              <div className="composer-quote">
                <Reply size={14} />
                <span>
                  <strong>{t("portal.inbox.conversation.replyingTo", { name: quoting.sender_name || (quoting.role === "assistant" ? t("portal.inbox.conversation.agent") : t("portal.inbox.conversation.visitor")) })}</strong>
                  <small>{(quoting.content || "").slice(0, 140)}</small>
                </span>
                <button type="button" onClick={() => setQuoting(null)} aria-label={t("portal.inbox.conversation.cancelReply")} title={t("portal.inbox.conversation.cancelReply")}><X size={14} /></button>
              </div>
            )}
            <ScheduledMessagesBanner messages={scheduledMessages} onCancel={handleCancelScheduledMessage} />
            <form ref={formRef} onSubmit={reply} onReset={() => { setDraft(""); setComposerMode("chat"); }} className="portal-composer composer-card">
              <div className={`composer-box${composerMode === "note" ? " note-mode" : ""}`} style={{ position: "relative" }}>
                <UnifiedComposerTop
                  mode={composerMode}
                  onModeChange={setComposerMode}
                  threads={replyVia.threads}
                  channel={replyVia.thread?.channel ?? selected.channel}
                  via={replyVia.via}
                  onViaChange={replyVia.setVia}
                  onOpenVariables={() => { setVariablesQuery(""); setVariablesOpen((v) => !v); }}
                  onOpenAppointmentModal={() => setAppointmentModalOpen(true)}
                  onOpenScheduleModal={() => setScheduledModalOpen(true)}
                />
                <VariablesPopover
                  open={variablesOpen}
                  onClose={() => { setVariablesOpen(false); setVariablesQuery(""); }}
                  onSelect={(val) => insertTextAtCursor(val)}
                  query={variablesQuery}
                  contactValues={{
                    contact_name: selected.contact_name || selected.title,
                    contact_phone: selected.contact_phone || (isSocialChannel(selected.channel) ? "" : (selected.external_chat_id || "").split("@")[0]),
                    contact_email: selected.contact_email,
                  }}
                  leadNumber={selected.number}
                  dealValue={selected.deal_value}
                  channel={selected.channel}
                />
                <GrowingTextarea
                  ref={composerRef}
                  name="content"
                  autoComplete="off"
                  placeholder={composerMode === "note" ? (t("inbox.composerNotePlaceholder") || "Escribe una nota interna para el equipo...") : t("portal.inbox.conversation.replyPlaceholder")}
                  disabled={composerMode === "note" ? busy : (!policy.canReply || busy)}
                  required={composerMode === "note" ? true : !pendingFile}
                  onChange={(e) => {
                    const val = e.target.value;
                    setDraft(val);
                    const cursor = e.target.selectionStart ?? val.length;
                    const before = val.slice(0, cursor);
                    const lastBracket = before.lastIndexOf("[");
                    if (lastBracket !== -1) {
                      const textBetween = before.slice(lastBracket + 1);
                      if (!textBetween.includes("]") && !textBetween.includes("\n") && textBetween.length <= 30) {
                        setVariablesQuery(textBetween);
                        setVariablesOpen(true);
                        return;
                      }
                    }
                    if (variablesQuery) {
                      setVariablesQuery("");
                      setVariablesOpen(false);
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "[" || e.code === "BracketLeft") {
                      setVariablesOpen(true);
                      setVariablesQuery("");
                    }
                    if (variablesOpen && e.key === "Escape") {
                      setVariablesOpen(false);
                      setVariablesQuery("");
                    }
                  }}
                />
                <div className="composer-bottom">
                  <div className="composer-left">
                    <button
                      type="submit"
                      className={`composer-send${composerMode === "note" ? (draft.trim() ? " note-ready" : "") : (draft.trim() || pendingFile ? " ready" : "")}`}
                      disabled={composerMode === "note" ? (busy || !draft.trim()) : (!policy.canReply || busy || (!draft.trim() && !pendingFile))}
                    >
                      {busy ? <LoaderCircle className="spin" size={16} /> : composerMode === "note" ? (t("inbox.composerSaveNote") || "Guardar nota") : t("portal.inbox.composer.send")}
                    </button>
                    {composerMode !== "note" && <RecordButton onRecorded={sendAttachment} onError={() => toast.error(t("chat.micDenied"))} disabled={!policy.canRecord || busy} title={t("chat.recordAudio")} titleStop={t("chat.stopRecording")} />}
                    {(draft.length > 0 || pendingFile || quoting || composerMode === "note") && (
                      <button type="button" className="composer-cancel" onClick={(event) => { event.currentTarget.form?.reset(); setPendingFile(null); setQuoting(null); setComposerMode("chat"); }}>
                        {t("portal.inbox.composer.cancel")}
                      </button>
                    )}
                  </div>
                  <div className="composer-right">
                    {composerMode !== "note" && <>
                      <button type="button" role="switch" aria-checked={selected.mode === "ai"} className={`ai-toggle${selected.mode === "ai" ? " on" : ""}`} title={t("portal.inbox.list.aiAgent")} onClick={() => toggleMode(selected.mode === "ai" ? "human" : "ai")}>
                        <Bot size={15} /> <span>{t("portal.inbox.folders.ai")}</span>
                      </button>
                      <div className="composer-menu">
                        <button
                          type="button"
                          className={`composer-icon plus${composerMenu ? " open" : ""}`}
                          title={t("portal.inbox.composer.more")}
                          aria-label={t("portal.inbox.composer.more")}
                          aria-haspopup="menu"
                          aria-expanded={Boolean(composerMenu)}
                          onClick={() => setComposerMenu(composerMenu ? null : "plus")}
                        >
                          <Plus size={20} />
                        </button>
                        {composerMenu === "plus" && (
                          <div className="composer-menu-list up" role="menu">
                            <button type="button" role="menuitem" disabled={!policy.canReply || busy} onClick={() => setComposerMenu("emoji")}><Smile size={18} /><span>{t("portal.inbox.composer.emoji")}</span></button>
                            <button type="button" role="menuitem" onClick={() => { setComposerMenu(null); setAppointmentModalOpen(true); }}><CalendarIcon size={18} /><span>{t("portal.inbox.composer.schedule")}</span></button>
                            <button type="button" role="menuitem" onClick={() => { setComposerMenu(null); setScheduledModalOpen(true); }}><Clock size={18} /><span>{t("inbox.composerScheduleMessage") || "Programar mensaje"}</span></button>
                            <button type="button" role="menuitem" disabled={!policy.canAttach || busy} onClick={() => { setComposerMenu(null); fileInputRef.current?.click(); }}><Paperclip size={18} /><span>{t("chat.attachFile")}</span></button>
                            {(replyVia.thread?.channel ?? selected.channel) === "whatsapp" && (
                              <button type="button" role="menuitem" disabled={!policy.canReply || busy} onClick={() => { setComposerMenu(null); setLocating(true); }}><MapPin size={18} /><span>{t("inbox.locationSend")}</span></button>
                            )}
                          </div>
                        )}
                        {composerMenu === "emoji" && (
                          <div className="composer-emoji" role="menu">
                            {EMOJIS.map((emoji) => (
                              <button type="button" key={emoji} role="menuitem" onClick={() => { insertTextAtCursor(emoji); setComposerMenu(null); }}>{emoji}</button>
                            ))}
                          </div>
                        )}
                        <input ref={fileInputRef} type="file" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) setPendingFile(file); event.currentTarget.value = ""; }} />
                      </div>
                    </>}
                  </div>
                </div>
              </div>
            </form>
          </>}
      </section>
      {leadOpen && selected && leadScope && <LeadScopeProvider scope={leadScope}>
        <LeadCard conversationId={selected.id} number={selected.number} messages={selected.messages ?? []} urlFor={attachmentUrl} overlay={leadOverlay} onClose={closeLead} onChanged={() => { loadFirst({ silent: true }); refreshSelected(); }} onMerged={(primary) => { void choose(primary.conversation_id).catch(() => {}); loadFirst({ silent: true }); }} onBlockContact={() => setBlockingContact(true)} syncKey={selected.messages?.at(-1)?.id} />
      </LeadScopeProvider>}
      {blockingContact && selected?.contact_id && (
        <Modal open title={t("portal.contacts.blockTitle", { name: selected.contact_name || selected.title || "" })} onClose={() => setBlockingContact(false)}>
          <div className="modal-form">
            <p className="muted">{t("portal.contacts.blockCopy")}</p>
            <p className="muted">{t("portal.contacts.blockUnblockCopy")}</p>
            <div className="modal-actions">
              <button type="button" className="button" onClick={() => setBlockingContact(false)}>{t("common.cancel")}</button>
              <button type="button" className="button danger" disabled={busy} onClick={() => blockContact(true)}>
                <Ban size={15} /> {t("portal.contacts.block")}
              </button>
            </div>
          </div>
        </Modal>
      )}
      {appointmentModalOpen && selected && selected.client_id && (
        <AppointmentModal
          open={appointmentModalOpen}
          onClose={() => setAppointmentModalOpen(false)}
          base={`/clients/${selected.client_id}`}
          conversationId={selected.id}
          contactId={selected.contact_id}
          onSuccess={() => {
            refreshSelected();
            loadFirst({ silent: true });
          }}
        />
      )}
      {scheduledModalOpen && selected && (
        <ScheduleMessageModal
          open={scheduledModalOpen}
          onClose={() => setScheduledModalOpen(false)}
          onSchedule={handleScheduleMessage}
          initialContent={draft}
        />
      )}
    </div>
    </div>
  </div>;
}
