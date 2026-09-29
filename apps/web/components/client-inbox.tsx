"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  Bot,
  Calendar as CalendarIcon,
  Check,
  ChevronDown,
  Clock,
  Filter,
  Images,
  Inbox,
  Link2,
  LoaderCircle,
  Paperclip,
  Plus,
  Reply,
  Search,
  Smile,
  UserRound,
  X,
} from "lucide-react";
import { PendingAttachment, RecordButton, useFileDrop, type GalleryImage } from "@/components/attachments";
import { MediaPanel } from "@/components/media-panel";
import { LeadCard } from "@/components/lead-card/lead-card";
import { MessageThread } from "@/components/messages/message-thread";
import { UnifiedComposerTop, type ComposerMode } from "@/components/unified-composer-top";
import { AppointmentModal } from "@/components/appointment-modal";
import { ScheduleMessageModal } from "@/components/schedule-message-modal";
import { ScheduledMessagesBanner } from "@/components/scheduled-messages-banner";
import { VariablesPopover } from "@/components/variables-popover";
import { LeadAvatarButton } from "@/components/lead-card/avatar-button";
import { LeadScopeProvider, agencyLeadScope } from "@/components/lead-card/scope";
import { useLeadPanel } from "@/components/lead-card/use-lead-panel";
import { GrowingTextarea } from "@/components/growing-textarea";
import { ReactionPicker } from "@/components/message-gestures";
import { useToast } from "@/components/toast";
import { EmptyState } from "@/components/ui";
import { ListRowsSkeleton } from "@/components/skeleton";
import { LeadRowActions, pinnedFirst } from "@/components/lead-row-actions";
import { ChannelDots, ChannelIcon, channelLabel as labelForChannel, INBOX_CHANNELS, isSocialChannel, leadChannels } from "@/lib/channels";
import { useAttachmentOwners, useReplyVia } from "@/lib/linked-threads";
import { PhonePauseNotice, SocialReplyNotice, useReplyPolicy } from "@/components/reply-policy";
import { api, apiUrl, messageFrom } from "@/lib/api";
import { formatWhen, isNearBottom } from "@/lib/datetime";
import { useLanguage, useT } from "@/lib/i18n";
import { clientPath } from "@/lib/routes";
import type { Attachment, Conversation, Message, ScheduledMessage } from "@/types";
import type { ContactValues } from "@/lib/contact-variables";

const LIST_WIDTH = {
  min: 240,
  max: 560,
  wide: 360,
  narrow: 260,
  narrowBelow: 900,
  threadMin: 380,
  step: 16,
};
const LEAD_PANEL_WIDTH = 360;

function defaultListWidth(): number {
  if (typeof window === "undefined") return LIST_WIDTH.wide;
  return window.innerWidth <= LIST_WIDTH.narrowBelow ? LIST_WIDTH.narrow : LIST_WIDTH.wide;
}

const EMOJIS = [
  "ðŸ˜€", "ðŸ˜‚", "ðŸ˜Š", "ðŸ˜", "ðŸ˜‰", "ðŸ™‚", "ðŸ˜…", "ðŸ¤",
  "ðŸ™", "ðŸ‘", "ðŸ‘", "ðŸŽ‰", "â¤ï¸", "ðŸ”¥", "âœ¨", "ðŸ˜¢",
  "ðŸ˜®", "ðŸ¤”", "ðŸ‘Œ", "ðŸ’ª", "âœ…", "ðŸ“…", "ðŸ“", "ðŸ“ž",
  "ðŸ’¬", "â­", "ðŸ™Œ", "ðŸ˜Ž", "ðŸ¥³", "ðŸ˜´", "ðŸ‘‹", "ðŸ’¡",
];

interface ClientInboxProps {
  clientId: string;
  clientName?: string;
  portalSlug?: string;
  urlNumber?: number;
}

export function ClientInbox({ clientId, portalSlug, urlNumber }: ClientInboxProps) {
  const { t, lang } = useLanguage();
  const toast = useToast();
  const router = useRouter();
  // Page links use the slug; the API keeps working by UUID.
  const key = portalSlug ?? clientId;

  const [items, setItems] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [loadedInbox, setLoadedInbox] = useState(false);
  const [busy, setBusy] = useState(false);
  const selectedIdRef = useRef<string | null>(null);

  // Search & filter state
  const [search, setSearch] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [unansweredOnly, setUnansweredOnly] = useState(false);
  const [channelFilter, setChannelFilter] = useState("");
  const [sourceOpen, setSourceOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement>(null);

  // Resizer state
  const [listWidth, setListWidth] = useState(defaultListWidth);
  const [resizing, setResizing] = useState(false);
  const inboxRef = useRef<HTMLDivElement | null>(null);

  // Composer & gestures state
  const [draft, setDraft] = useState("");
  const [composerMode, setComposerMode] = useState<ComposerMode>("chat");
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [quoting, setQuoting] = useState<Message | null>(null);
  const [reactingTo, setReactingTo] = useState<string | null>(null);
  const [composerMenu, setComposerMenu] = useState<null | "plus" | "emoji">(null);
  const [variablesOpen, setVariablesOpen] = useState(false);
  const [variablesQuery, setVariablesQuery] = useState("");
  const [appointmentModalOpen, setAppointmentModalOpen] = useState(false);
  const [scheduledModalOpen, setScheduledModalOpen] = useState(false);
  const [scheduledMessages, setScheduledMessages] = useState<ScheduledMessage[]>([]);
  const [mediaOpen, setMediaOpen] = useState(false);

  const composerRef = useRef<HTMLFormElement>(null);
  const replyInputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagesRef = useRef<HTMLDivElement>(null);

  // Threads & policy
  const replyVia = useReplyVia(selected);
  const owners = useAttachmentOwners(selected);
  const policy = useReplyPolicy(replyVia.policyConversation);
  const replyChannel = replyVia.thread?.channel ?? selected?.channel ?? "";

  // Lead panel
  const { open: leadPanelOpen, setOpen: setLeadOpen, overlay: leadOverlay, attachLayout: attachLead } = useLeadPanel();
  const closeLead = useCallback(() => setLeadOpen(false), [setLeadOpen]);
  const leadScope = useMemo(() => agencyLeadScope(clientId), [clientId]);
  const leadOpen = Boolean(selected) && leadPanelOpen;

  const inboxNodeRef = useCallback((node: HTMLDivElement | null) => {
    inboxRef.current = node;
    attachLead(node);
  }, [attachLead]);

  const channelLabel = useCallback((value: string) => labelForChannel(value, t), [t]);
  const channelIcon = useCallback((value: string) => <ChannelIcon channel={value} />, []);

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

  const contactValues: ContactValues = useMemo(() => {
    if (!selected) {
      return { contact_name: "", contact_phone: "", contact_email: "", agent_name: "" };
    }
    return {
      contact_name: selected.contact_name || selected.title || "",
      contact_phone: selected.contact_phone || (isSocialChannel(selected.channel) ? "" : (selected.external_chat_id || "").split("@")[0]),
      contact_email: selected.contact_email || "",
      agent_name: selected.assignee_name || "",
    };
  }, [selected]);

  // Load conversations list
  const loadList = useCallback(async () => {
    try {
      const data = await api<Conversation[]>(`/conversations?client_id=${clientId}`);
      setItems(data);
    } catch {
      // ignore
    }
  }, [clientId]);

  useEffect(() => {
    loadList().finally(() => setLoadedInbox(true));
  }, [loadList]);

  // Periodic refresh
  useEffect(() => {
    const timer = setInterval(() => {
      loadList().catch(() => {});
      const activeId = selectedIdRef.current;
      if (activeId) {
        api<Conversation>(`/conversations/${activeId}`)
          .then((detail) => {
            if (selectedIdRef.current === detail.id) {
              setSelected(detail);
            }
          })
          .catch(() => {});
      }
    }, 8000);
    return () => clearInterval(timer);
  }, [loadList]);

  const markRead = useCallback((id: string) => {
    setItems((rows) => rows.map((row) => (row.id === id ? { ...row, unread: false, unread_count: 0 } : row)));
    api(`/conversations/${id}/read`, { method: "POST" }).catch(() => {});
  }, []);

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

  // Sync selection by URL lead number
  useEffect(() => {
    if (urlNumber === undefined) {
      setSelected(null);
      selectedIdRef.current = null;
      return;
    }
    if (selected?.number === urlNumber) return;
    let cancelled = false;
    api<Conversation>(`/clients/${clientId}/conversations/number/${urlNumber}`)
      .then((detail) => {
        if (cancelled) return;
        selectedIdRef.current = detail.id;
        setSelected(detail);
        if (detail.unread) markRead(detail.id);
        void refreshScheduledMessages(detail.id);
        if (detail.number !== urlNumber) {
          router.replace(clientPath(key, "inbox", detail.number));
        }
      })
      .catch(() => {
        if (!cancelled) router.replace(clientPath(key, "inbox"));
      });
    return () => { cancelled = true; };
  }, [urlNumber, clientId, key, router, markRead, refreshScheduledMessages, selected?.number]);

  async function choose(item: { id: string }) {
    try {
      const detail = await api<Conversation>(`/conversations/${item.id}`);
      selectedIdRef.current = detail.id;
      setSelected(detail);
      if (detail.unread) markRead(detail.id);
      if (detail.number !== urlNumber) {
        router.push(clientPath(key, "inbox", detail.number));
      }
      void refreshScheduledMessages(detail.id);
    } catch (err) {
      toast.error(messageFrom(err));
    }
  }

  function clearSelection() {
    setSelected(null);
    selectedIdRef.current = null;
    if (urlNumber !== undefined) {
      router.push(clientPath(key, "inbox"));
    }
  }

  // Resolving a lead from its row. This list comes from `/conversations`,
  // which does not say who spoke last, so the row cannot show the pending dot
  // here; what it can do is tell the truth on the next read of the list.
  async function resolveRow(item: { id: string }) {
    try {
      await api<Conversation>(`/conversations/${item.id}/status`, { method: "PATCH", body: JSON.stringify({ status: "resolved" }) });
      await loadList();
    } catch (err) {
      toast.error(messageFrom(err));
    }
  }

  // The pin moves the row straight away and the answer confirms it; a failure
  // puts the row back where it was rather than leaving a lie on screen.
  async function togglePinRow(item: Conversation) {
    const pinned = !item.pinned_at;
    const at = pinned ? new Date().toISOString() : null;
    setItems((rows) => pinnedFirst(rows.map((row) => (row.id === item.id ? { ...row, pinned_at: at } : row))));
    try {
      const conv = await api<Conversation>(`/conversations/${item.id}/pin`, { method: "PATCH", body: JSON.stringify({ pinned }) });
      setItems((rows) => pinnedFirst(rows.map((row) => (row.id === item.id ? { ...row, pinned_at: conv.pinned_at ?? null } : row))));
    } catch (err) {
      setItems((rows) => rows.map((row) => (row.id === item.id ? { ...row, pinned_at: item.pinned_at } : row)));
      toast.error(messageFrom(err));
    }
  }

  // Auto-scroll messages
  const wasNearBottomRef = useRef(true);
  useEffect(() => {
    const el = messagesRef.current;
    if (el) {
      const handler = () => { wasNearBottomRef.current = isNearBottom(el); };
      el.addEventListener("scroll", handler, { passive: true });
      return () => el.removeEventListener("scroll", handler);
    }
  }, [selected?.id]);

  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    if (wasNearBottomRef.current) {
      const frame = requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
      return () => cancelAnimationFrame(frame);
    }
  }, [selected?.id, selected?.messages?.at(-1)?.id]);

  // Click outside for filter popover & plus menu
  useEffect(() => {
    if (!filtersOpen) return;
    const close = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent) {
        if (event.key === "Escape") { setFiltersOpen(false); setSourceOpen(false); }
        return;
      }
      if (filterRef.current && !filterRef.current.contains(event.target as Node)) {
        setFiltersOpen(false);
        setSourceOpen(false);
      }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [filtersOpen]);

  useEffect(() => {
    if (!composerMenu) return;
    const close = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent) {
        if (event.key === "Escape") setComposerMenu(null);
        return;
      }
      if (composerRef.current && !composerRef.current.contains(event.target as Node)) {
        setComposerMenu(null);
      }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [composerMenu]);

  // Resizer handlers
  const clampListWidth = (width: number) => {
    const total = (inboxRef.current?.clientWidth ?? 1000) - (leadOpen && !leadOverlay ? LEAD_PANEL_WIDTH : 0);
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
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
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

  // Text insertion for variables & emoji
  function insertTextAtCursor(text: string, replaceTriggerChar?: string) {
    const field = replyInputRef.current;
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

  // Reply handlers
  async function sendNote(content: string) {
    if (!selected || busy || !content.trim()) return;
    setBusy(true);
    try {
      const updated = await api<Conversation>(`/conversations/${selected.id}/notes`, {
        method: "POST",
        body: JSON.stringify({ content: content.trim() }),
      });
      setSelected(updated);
      if (replyInputRef.current) replyInputRef.current.value = "";
      setDraft("");
      setComposerMode("chat");
      await loadList();
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusy(false);
      replyInputRef.current?.focus();
    }
  }

  async function sendAttachment(file?: File) {
    if (!file || !selected || !policy.canAttach || busy) return;
    setBusy(true);
    const caption = (replyInputRef.current?.value || "").trim();
    try {
      const data = new FormData();
      data.append("file", file);
      if (caption) data.append("caption", caption);
      if (replyVia.multi) data.append("via_conversation_id", replyVia.via);
      const updated = await api<Conversation>(`/conversations/${selected.id}/reply-media`, {
        method: "POST",
        body: data,
      });
      setSelected(updated);
      if (replyInputRef.current) replyInputRef.current.value = "";
      setDraft("");
      setPendingFile(null);
      await loadList();
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  const { dropProps, overlay } = useFileDrop(setPendingFile, {
    enabled: policy.canAttach && !busy,
    label: t("chat.dropToSend"),
  });

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
    if (!content.trim()) return;
    setBusy(true);
    try {
      const updated = await api<Conversation>(`/conversations/${selected.id}/reply`, {
        method: "POST",
        body: JSON.stringify({
          content: content.trim(),
          quoted_message_id: quoting?.id ?? null,
          ...replyVia.payload,
        }),
      });
      setSelected(updated);
      form.reset();
      setDraft("");
      setQuoting(null);
      await loadList();
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  async function setMode(next: "ai" | "human") {
    if (!selected) return;
    try {
      const updated = await api<Conversation>(`/conversations/${selected.id}/mode`, {
        method: "PATCH",
        body: JSON.stringify({ mode: next }),
      });
      setSelected(updated);
      await loadList();
    } catch (err) {
      toast.error(messageFrom(err));
    }
  }

  async function sendReaction(message: Message, emoji: string) {
    if (!selected) return;
    const next = emoji === message.reaction ? "" : emoji;
    setReactingTo(null);
    try {
      const updated = await api<Conversation>(`/conversations/${selected.id}/messages/${message.id}/reaction`, {
        method: "POST",
        body: JSON.stringify({ emoji: next, via_conversation_id: message.conversation_id }),
      });
      setSelected(updated);
    } catch (err) {
      toast.error(messageFrom(err));
    }
  }

  async function handleScheduleMessage(content: string, scheduledFor: string) {
    if (!selected) return;
    try {
      await api(`/conversations/${selected.id}/scheduled-messages`, {
        method: "POST",
        body: JSON.stringify({
          content,
          scheduled_for: scheduledFor,
          via_conversation_id: replyVia.multi ? replyVia.via : undefined,
        }),
      });
      setScheduledModalOpen(false);
      toast.success("Mensaje programado correctamente");
      await refreshScheduledMessages(selected.id);
    } catch (err) {
      toast.error(messageFrom(err));
    }
  }

  async function handleCancelScheduledMessage(scheduledId: string) {
    if (!selected) return;
    try {
      await api(`/conversations/${selected.id}/scheduled-messages/${scheduledId}`, { method: "DELETE" });
      toast.success("Mensaje programado cancelado");
      await refreshScheduledMessages(selected.id);
    } catch (err) {
      toast.error(messageFrom(err));
    }
  }

  // Filtered list
  const visibleItems = useMemo(() => {
    let list = items;
    if (unansweredOnly) {
      list = list.filter((item) => Boolean(item.unread || (item.unread_count && item.unread_count > 0)));
    }
    if (channelFilter) {
      list = list.filter((item) => item.channel === channelFilter);
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((item) =>
        (item.title && item.title.toLowerCase().includes(q)) ||
        (item.contact_name && item.contact_name.toLowerCase().includes(q)) ||
        (item.preview && item.preview.toLowerCase().includes(q)) ||
        String(item.number).includes(q)
      );
    }
    return list;
  }, [items, unansweredOnly, channelFilter, search]);

  const sourceOptions = useMemo(() => {
    const set = new Set<string>();
    items.forEach((item) => {
      if (item.channel && item.channel !== "playground") set.add(item.channel);
    });
    return Array.from(set);
  }, [items]);

  const unansweredCount = useMemo(() => {
    return items.filter((item) => Boolean(item.unread || (item.unread_count && item.unread_count > 0))).length;
  }, [items]);

  if (!loadedInbox) return <ListRowsSkeleton rows={5} />;
  if (!items.length) {
    return (
      <EmptyState
        icon={<Inbox />}
        title={t("clients.detail.inboxEmptyTitle")}
        description={t("clients.detail.inboxEmptyDescription")}
      />
    );
  }

  return (
    <div
      ref={inboxNodeRef}
      className={`portal-inbox${selected ? " has-thread" : ""}${leadOpen ? " has-lead" : ""}${leadOverlay ? " lead-overlay" : ""}${resizing ? " is-resizing" : ""}`}
      style={{ "--inbox-list-w": `${listWidth}px`, "--portal-color": "#00876c" } as React.CSSProperties}
    >
      {/* Resizer */}
      <div
        className="inbox-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label={t("portal.inbox.resizeList")}
        aria-valuemin={LIST_WIDTH.min}
        aria-valuemax={LIST_WIDTH.max}
        aria-valuenow={listWidth}
        tabIndex={0}
        title={t("portal.inbox.resizeList")}
        onPointerDown={startResize}
        onPointerMove={moveResize}
        onPointerUp={endResize}
        onPointerCancel={endResize}
        onKeyDown={keyResize}
        onDoubleClick={() => setListWidth(defaultListWidth())}
      />

      {/* Sidebar: conversation list */}
      <aside>
        <div className="inbox-search">
          <Search size={16} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("inbox.searchPlaceholder")}
          />
          <div className="inbox-filter-wrap" ref={filterRef}>
            <button
              type="button"
              className={`inbox-filter-toggle${filtersOpen ? " open" : ""}${channelFilter || unansweredOnly ? " active" : ""}`}
              onClick={() => { setFiltersOpen((v) => !v); setSourceOpen(false); }}
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
                  <button
                    type="button"
                    className={!unansweredOnly ? "selected" : ""}
                    aria-pressed={!unansweredOnly}
                    onClick={() => { setUnansweredOnly(false); }}
                  >
                    <span>{t("inbox.allStatus")}</span>
                    {!unansweredOnly && <Check size={16} />}
                  </button>
                  <button
                    type="button"
                    className={unansweredOnly ? "selected" : ""}
                    aria-pressed={unansweredOnly}
                    onClick={() => { setUnansweredOnly(true); }}
                  >
                    <span>{t("inbox.unanswered")}</span>
                    {unansweredCount > 0 && <em>{unansweredCount}</em>}
                    {unansweredOnly && <Check size={16} />}
                  </button>
                </div>
                <span className="pop-label">{t("inbox.filterSources")}</span>
                <div className="pop-select">
                  <button
                    type="button"
                    className="pop-select-trigger"
                    aria-haspopup="listbox"
                    aria-expanded={sourceOpen}
                    onClick={() => setSourceOpen((v) => !v)}
                  >
                    <span>{channelFilter ? channelLabel(channelFilter) : t("inbox.allSources")}</span>
                    <ChevronDown size={16} />
                  </button>
                  {sourceOpen && (
                    <ul className="pop-select-list" role="listbox" aria-label={t("inbox.filterSources")}>
                      {sourceOptions.length === 0 ? (
                        <li className="pop-select-empty">{t("inbox.connectFirstChannel")}</li>
                      ) : (
                        <>
                          <li>
                            <button
                              type="button"
                              role="option"
                              aria-selected={!channelFilter}
                              onClick={() => { setChannelFilter(""); setSourceOpen(false); }}
                            >
                              <span>{t("inbox.allSources")}</span>
                              {!channelFilter && <Check size={15} />}
                            </button>
                          </li>
                          {sourceOptions.map((value) => (
                            <li key={value}>
                              <button
                                type="button"
                                role="option"
                                aria-selected={channelFilter === value}
                                onClick={() => { setChannelFilter(value); setSourceOpen(false); }}
                              >
                                <span className={`channel-dot ${value}`}>{channelIcon(value)}</span>
                                <span>{channelLabel(value)}</span>
                                {channelFilter === value && <Check size={15} />}
                              </button>
                            </li>
                          ))}
                        </>
                      )}
                    </ul>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {visibleItems.map((item) => (
          <div key={item.id} className="inbox-row">
            <Link
              href={clientPath(key, "inbox", item.number)}
              className={`${selected?.id === item.id ? "active" : ""}${item.unread && selected?.id !== item.id ? " unread" : ""}`}
            >
              <span className="entity-avatar tiny"><UserRound size={15} /></span>
              <span>
                <span className="portal-inbox-row-top">
                  <strong>{item.contact_name || item.title}</strong>
                  {item.unread && selected?.id !== item.id ? (
                    <span className="inbox-unread-count" aria-label={t("inbox.unreadCount", { count: item.unread_count ?? 0 })}>
                      {(item.unread_count ?? 0) > 99 ? "99+" : item.unread_count}
                    </span>
                  ) : (
                    <time>{formatWhen(item.last_inbound_at ?? item.updated_at, lang)}</time>
                  )}
                </span>
                <small className="portal-inbox-preview">{item.preview || t("portal.inbox.list.noMessages")}</small>
                <small className="inbox-row-meta">
                  <span className="lead-number">#{item.number}</span>
                  {leadChannels(item).length > 1 ? (
                    <ChannelDots channels={leadChannels(item)} t={t} />
                  ) : (
                    <>
                      <span className={`channel-dot ${item.channel}`}>{channelIcon(item.channel)}</span> {channelLabel(item.channel)}
                    </>
                  )}
                  {item.account_label && <span className="account-badge" title={item.account_label}>{item.account_label}</span>}
                  <span className={`mini-badge ${item.mode}`}>
                    {item.mode === "human" ? (item.assignee_name || t("portal.inbox.list.humanSupport")) : t("portal.inbox.list.aiAgent")}
                  </span>
                  {item.team_name && <span className="mini-badge team">{item.team_name}</span>}
                </small>
              </span>
            </Link>
            <LeadRowActions
              pinned={Boolean(item.pinned_at)}
              onResolve={() => resolveRow(item)}
              onTogglePin={() => togglePinRow(item)}
              href={clientPath(key, "inbox", item.number)}
            />
          </div>
        ))}
        {!visibleItems.length && <div className="no-conversations">{t("inbox.empty")}</div>}
      </aside>

      {/* Main thread area */}
      <section className="drop-target" {...dropProps}>
        {overlay}
        {!selected && (
          <EmptyState
            icon={<Inbox />}
            title={t("portal.inbox.empty.title")}
            description={t("portal.inbox.empty.description")}
          />
        )}
        {selected && (
          <>
            <header>
              <button
                type="button"
                className="icon-button inbox-back"
                onClick={clearSelection}
                aria-label={t("common.back")}
                title={t("common.back")}
              >
                <ArrowLeft size={16} />
              </button>
              <LeadAvatarButton
                channel={selected.channel}
                open={leadOpen}
                onClick={() => setLeadOpen(!leadPanelOpen)}
              />
              <div>
                <strong>
                  {selected.contact_name || selected.title}
                  <span className="lead-number">#{selected.number}</span>
                </strong>
                <small className="portal-channel-line">
                  {replyVia.multi ? (
                    <ChannelDots channels={[...new Set(replyVia.threads.map((thread) => thread.channel))]} t={t} />
                  ) : (
                    <>{channelIcon(selected.channel)} {channelLabel(selected.channel)}</>
                  )}
                  {selected.account_label && <span className="account-badge" title={selected.account_label}>{selected.account_label}</span>}
                  {selected.channel === "whatsapp_cloud" && !selected.reply_window_open && (
                    <span className="window-pill closed">
                      <Clock size={11} /> {selected.reply_window_until ? t("portal.inbox.window.closed") : t("portal.inbox.window.neverWrote")}
                    </span>
                  )}
                </small>
              </div>

              <div className="thread-actions">
                <button
                  type="button"
                  className="icon-button"
                  title={t("portal.inbox.copyLink")}
                  aria-label={t("portal.inbox.copyLink")}
                  onClick={() => {
                    void navigator.clipboard?.writeText(window.location.href).then(() => toast.success(t("portal.inbox.linkCopied")));
                  }}
                >
                  <Link2 size={16} />
                </button>
                <button
                  className="icon-button"
                  onClick={() => setMediaOpen(true)}
                  title={t("chat.sharedContent")}
                  aria-label={t("chat.sharedContent")}
                >
                  <Images size={16} />
                </button>
              </div>
            </header>

            {/* Messages */}
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
                  replyInputRef.current?.focus();
                },
              }}
              reactionPicker={(message) => reactingTo === message.id ? (
                <ReactionPicker
                  current={message.reaction}
                  removeLabel={t("portal.inbox.conversation.removeReaction")}
                  onPick={(emoji) => sendReaction(message, emoji)}
                />
              ) : null}
              containerRef={messagesRef}
            />

            <PhonePauseNotice conversation={selected} onKeepManual={() => setMode("human")} />
            <SocialReplyNotice conversation={selected} blocked={policy.blocked} humanOnly={policy.humanOnly} />
            {pendingFile && <PendingAttachment file={pendingFile} onCancel={() => setPendingFile(null)} />}
            {quoting && (
              <div className="composer-quote">
                <Reply size={14} />
                <span>
                  <strong>
                    {t("portal.inbox.conversation.replyingTo", {
                      name: quoting.sender_name || (quoting.role === "assistant" ? t("portal.inbox.conversation.agent") : t("portal.inbox.conversation.visitor")),
                    })}
                  </strong>
                  <small>{(quoting.content || "").slice(0, 140)}</small>
                </span>
                <button
                  type="button"
                  onClick={() => setQuoting(null)}
                  aria-label={t("portal.inbox.conversation.cancelReply")}
                  title={t("portal.inbox.conversation.cancelReply")}
                >
                  <X size={14} />
                </button>
              </div>
            )}

            <ScheduledMessagesBanner messages={scheduledMessages} onCancel={handleCancelScheduledMessage} />

            {/* Composer Card */}
            <form
              ref={composerRef}
              onSubmit={reply}
              onReset={() => {
                setDraft("");
                setComposerMode("chat");
              }}
              className="portal-composer composer-card"
            >
              <div className={`composer-box${composerMode === "note" ? " note-mode" : ""}`} style={{ position: "relative" }}>
                <UnifiedComposerTop
                  mode={composerMode}
                  onModeChange={setComposerMode}
                  threads={replyVia.threads}
                  channel={replyChannel}
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
                  contactValues={contactValues}
                  leadNumber={selected.number}
                  dealValue={selected.deal_value}
                  channel={selected.channel}
                />
                <GrowingTextarea
                  ref={replyInputRef}
                  name="content"
                  autoComplete="off"
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
                  required={composerMode === "note" ? true : !pendingFile}
                  disabled={composerMode === "note" ? busy : (!policy.canReply || busy)}
                  placeholder={
                    composerMode === "note"
                      ? (t("inbox.composerNotePlaceholder") || "Escribe una nota interna para el equipo...")
                      : t("portal.inbox.conversation.replyPlaceholder")
                  }
                />
                <div className="composer-bottom">
                  <div className="composer-left">
                    <button
                      type="submit"
                      className={`composer-send${composerMode === "note" ? (draft.trim() ? " note-ready" : "") : (draft.trim() || pendingFile ? " ready" : "")}`}
                      disabled={composerMode === "note" ? (busy || !draft.trim()) : (!policy.canReply || busy || (!draft.trim() && !pendingFile))}
                    >
                      {busy ? (
                        <LoaderCircle className="spin" size={16} />
                      ) : composerMode === "note" ? (
                        t("inbox.composerSaveNote") || "Guardar nota"
                      ) : (
                        t("portal.inbox.composer.send")
                      )}
                    </button>
                    {composerMode !== "note" && (
                      <RecordButton
                        onRecorded={sendAttachment}
                        onError={() => toast.error(t("chat.micDenied"))}
                        disabled={!policy.canRecord || busy}
                        title={t("chat.recordAudio")}
                        titleStop={t("chat.stopRecording")}
                      />
                    )}
                    {(draft.length > 0 || pendingFile || quoting || composerMode === "note") && (
                      <button
                        type="button"
                        className="composer-cancel"
                        onClick={(event) => {
                          event.currentTarget.form?.reset();
                          setPendingFile(null);
                          setQuoting(null);
                          setComposerMode("chat");
                        }}
                      >
                        {t("portal.inbox.composer.cancel")}
                      </button>
                    )}
                  </div>
                  <div className="composer-right">
                    {composerMode !== "note" && (
                      <>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={selected.mode === "ai"}
                          className={`ai-toggle${selected.mode === "ai" ? " on" : ""}`}
                          title={t("portal.inbox.list.aiAgent")}
                          onClick={() => setMode(selected.mode === "ai" ? "human" : "ai")}
                        >
                          <Bot size={15} /> <span>{t("portal.inbox.folders.ai")}</span>
                        </button>
                        <div className="composer-menu">
                          <button
                            type="button"
                            className={`composer-icon plus${composerMenu === "plus" || composerMenu === "emoji" ? " open" : ""}`}
                            title={t("portal.inbox.composer.more")}
                            aria-label={t("portal.inbox.composer.more")}
                            aria-haspopup="menu"
                            aria-expanded={composerMenu === "plus" || composerMenu === "emoji"}
                            onClick={() => setComposerMenu(composerMenu === "plus" || composerMenu === "emoji" ? null : "plus")}
                          >
                            <Plus size={20} />
                          </button>
                          {composerMenu === "plus" && (
                            <div className="composer-menu-list up" role="menu">
                              <button
                                type="button"
                                role="menuitem"
                                disabled={!policy.canReply || busy}
                                onClick={() => setComposerMenu("emoji")}
                              >
                                <Smile size={18} />
                                <span>{t("portal.inbox.composer.emoji")}</span>
                              </button>
                              <button
                                type="button"
                                role="menuitem"
                                onClick={() => { setComposerMenu(null); setAppointmentModalOpen(true); }}
                              >
                                <CalendarIcon size={18} />
                                <span>{t("portal.inbox.composer.schedule")}</span>
                              </button>
                              <button
                                type="button"
                                role="menuitem"
                                onClick={() => { setComposerMenu(null); setScheduledModalOpen(true); }}
                              >
                                <Clock size={18} />
                                <span>{t("inbox.composerScheduleMessage") || "Programar mensaje"}</span>
                              </button>
                              <button
                                type="button"
                                role="menuitem"
                                disabled={!policy.canAttach || busy}
                                onClick={() => { setComposerMenu(null); fileInputRef.current?.click(); }}
                              >
                                <Paperclip size={18} />
                                <span>{t("chat.attachFile")}</span>
                              </button>
                            </div>
                          )}
                          {composerMenu === "emoji" && (
                            <div className="composer-emoji" role="menu">
                              {EMOJIS.map((emoji) => (
                                <button
                                  type="button"
                                  key={emoji}
                                  role="menuitem"
                                  onClick={() => {
                                    insertTextAtCursor(emoji);
                                    setComposerMenu(null);
                                  }}
                                >
                                  {emoji}
                                </button>
                              ))}
                            </div>
                          )}
                          <input
                            ref={fileInputRef}
                            type="file"
                            hidden
                            onChange={(event) => {
                              const file = event.target.files?.[0];
                              if (file) setPendingFile(file);
                              event.currentTarget.value = "";
                            }}
                          />
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </div>
            </form>

            <MediaPanel
              open={mediaOpen}
              onClose={() => setMediaOpen(false)}
              messages={selected.messages ?? []}
              urlFor={attachmentUrl}
            />

            {appointmentModalOpen && selected && (
              <AppointmentModal
                open={appointmentModalOpen}
                onClose={() => setAppointmentModalOpen(false)}
                base={`/clients/${clientId}`}
                conversationId={selected.id}
                contactId={selected.contact_id}
                onSuccess={() => { loadList().catch(() => {}); }}
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
          </>
        )}
      </section>

      {/* Docked or overlay LeadCard */}
      {leadOpen && selected && (
        <LeadScopeProvider scope={leadScope}>
          <LeadCard
            conversationId={selected.id}
            number={selected.number}
            messages={selected.messages ?? []}
            urlFor={attachmentUrl}
            overlay={leadOverlay}
            onClose={closeLead}
            onChanged={() => { loadList().catch(() => {}); }}
            onMerged={(primary) => {
              void choose({ id: primary.conversation_id });
              loadList().catch(() => {});
            }}
            syncKey={selected.messages?.at(-1)?.id}
          />
        </LeadScopeProvider>
      )}
    </div>
  );
}
