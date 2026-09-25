"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Images, Inbox as InboxIcon, LoaderCircle, Lock, MapPin, Search, UserRound } from "lucide-react";
import { PageHead } from "@/components/ui";
import { AttachButton, MessageAttachments, PendingAttachment, RecordButton, useFileDrop, type GalleryImage } from "@/components/attachments";
import { LocationComposer } from "@/components/location-composer";
import { MediaPanel } from "@/components/media-panel";
import { LeadCard } from "@/components/lead-card/lead-card";
import { MergeAuditCard, isMergeActivity } from "@/components/merge-audit-card";
import { UnifiedComposerTop, type ComposerMode } from "@/components/unified-composer-top";
import { AppointmentModal } from "@/components/appointment-modal";
import { AppointmentActivityCard, isAppointmentActivity } from "@/components/appointment-activity-card";
import { VariablesPopover } from "@/components/variables-popover";
import { LeadAvatarButton } from "@/components/lead-card/avatar-button";
import { LeadScopeProvider, agencyLeadScope } from "@/components/lead-card/scope";
import { useLeadPanel } from "@/components/lead-card/use-lead-panel";
import { DeliveryTicks } from "@/components/delivery-ticks";
import { RichText } from "@/components/rich-text";
import { GrowingTextarea } from "@/components/growing-textarea";
import { QuotedSnippet, ReactionBadge } from "@/components/message-gestures";
import { ListRowsSkeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { ChannelDots, ChannelIcon, channelLabel as labelForChannel, INBOX_CHANNELS, isSocialChannel, leadChannels, MessageChannelMark } from "@/lib/channels";
import { useAttachmentOwners, useReplyVia } from "@/lib/linked-threads";
import { PhonePauseNotice, SocialReplyNotice, useReplyPolicy } from "@/components/reply-policy";
import { api, ApiError, apiUrl, messageFrom } from "@/lib/api";
import { formatTime, formatWhen, isNearBottom, isSameOpenThread } from "@/lib/datetime";
import { useLanguage, useT } from "@/lib/i18n";
import type { Agent, Attachment, Conversation, ConversationInbox } from "@/types";

const LIMIT = 30;
const POLL_MS = 8000;

export default function InboxPage() {
  const t = useT();
  const { lang } = useLanguage();
  const toast = useToast();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [items, setItems] = useState<ConversationInbox[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [agentId, setAgentId] = useState("");
  const [channel, setChannel] = useState("");
  const [tab, setTab] = useState<"all" | "unread" | "human" | "ai">("all");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [locating, setLocating] = useState(false);
  const [mediaOpen, setMediaOpen] = useState(false);
  const [appointmentModalOpen, setAppointmentModalOpen] = useState(false);
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

  const buildParams = useCallback((offsetValue: number) => {
    const params = new URLSearchParams();
    if (agentId) params.set("agent_id", agentId);
    if (channel) params.set("channel", channel);
    if (tab === "human" || tab === "ai") params.set("mode", tab);
    if (tab === "unread") params.set("unread", "1");
    if (search) params.set("search", search);
    params.set("limit", String(LIMIT));
    params.set("offset", String(offsetValue));
    return params.toString();
  }, [agentId, channel, tab, search]);

  const selectedIdRef = useRef<string | null>(null);
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
  // and drop the stale row instead of erroring or polling it forever.
  const closeGoneThread = useCallback((id: string) => {
    selectedIdRef.current = null;
    setSelected(null);
    setItems((rows) => rows.filter((row) => row.id !== id));
    toast.error(t("inbox.threadGone"));
  }, [toast, t]);

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
    } catch (err) {
      if (err instanceof ApiError && err.status === 404 && selectedIdRef.current === id) {
        closeGoneThread(id);
        return;
      }
      // Other poll failures should not interrupt the open thread.
    }
  }, [closeGoneThread]);

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

  // A board card opens its thread in a new tab through ?conversation=<id>.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get("conversation");
    if (!id) return;
    params.delete("conversation");
    const clean = `${window.location.pathname}${params.toString() ? `?${params}` : ""}${window.location.hash}`;
    window.history.replaceState(window.history.state, "", clean);
    void choose(id).catch(() => {});
  }, [choose]);

  async function toggleMode(next: "ai" | "human") {
    if (!selected) return;
    setSelected(await api<Conversation>(`/conversations/${selected.id}/mode`, { method: "PATCH", body: JSON.stringify({ mode: next }) }));
    loadFirst({ silent: true });
  }

  const composerRef = useRef<HTMLTextAreaElement>(null);
  const [composerMode, setComposerMode] = useState<ComposerMode>("chat");
  const [variablesOpen, setVariablesOpen] = useState(false);
  const [variablesQuery, setVariablesQuery] = useState("");
  const [draft, setDraft] = useState("");

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
      setSelected(await api<Conversation>(`/conversations/${selected.id}/reply`, { method: "POST", body: JSON.stringify({ content: content.trim(), ...replyVia.payload }) }));
      form.reset();
      setDraft("");
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

  return <div className="page">
    <PageHead eyebrow={t("inbox.eyebrow")} title={t("inbox.title")} description={t("inbox.description")} />

    <div className="toolbar filters">
      <div className="filter-select"><span>{t("inbox.filterAgent")}</span><select aria-label={t("inbox.filterAgent")} value={agentId} onChange={(e) => setAgentId(e.target.value)}><option value="">{t("inbox.allAgents")}</option>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></div>
      <div className="filter-select"><span>{t("inbox.filterChannel")}</span><select aria-label={t("inbox.filterChannel")} value={channel} onChange={(e) => setChannel(e.target.value)}><option value="">{t("inbox.allChannels")}</option>{INBOX_CHANNELS.map((value) => <option key={value} value={value}>{channelLabel(value)}</option>)}</select></div>
    </div>

    {/* On a phone the list and the thread take turns on screen (see the
        .has-thread rules); a desktop shows both side by side and ignores it. */}
    <div ref={attachLead} className={`inbox-layout${selected ? " has-thread" : ""}${leadOpen ? " has-lead" : ""}${leadOverlay ? " lead-overlay" : ""}`}>
      <aside className="inbox-list" onScroll={onScroll}>
        <div className="inbox-search"><Search size={16} /><input value={searchInput} onChange={(e) => setSearchInput(e.target.value)} placeholder={t("inbox.searchPlaceholder")} /></div>
        <div className="inbox-tabs">
          <button className={tab === "all" ? "active" : ""} onClick={() => setTab("all")}>{t("inbox.tabAll")}</button>
          <button className={tab === "unread" ? "active" : ""} onClick={() => setTab("unread")}>{t("inbox.tabUnread")}</button>
          <button className={tab === "human" ? "active" : ""} onClick={() => setTab("human")}>{t("inbox.statusHuman")}</button>
          <button className={tab === "ai" ? "active" : ""} onClick={() => setTab("ai")}>{t("inbox.statusAi")}</button>
        </div>
        {loading ? <ListRowsSkeleton rows={7} />
          : items.length ? <>
            {items.map((item) => (
              <button key={item.id} className={`inbox-row ${selected?.id === item.id ? "active" : ""} ${item.unread ? "unread" : ""}`} onClick={() => choose(item.id)}>
                <span className="inbox-avatar">
                  <span className="entity-avatar tiny"><UserRound size={15} /></span>
                  <span className={`channel-badge ${item.channel}`} title={channelLabel(item.channel)}>{channelIcon(item.channel)}</span>
                </span>
                <span className="inbox-row-body">
                  <span className="inbox-row-top"><strong>{item.contact_name || item.title}</strong><time>{formatWhen(item.last_inbound_at ?? item.updated_at, lang)}</time></span>
                  <small className="inbox-row-preview">{item.preview || t("inbox.noMessages")}</small>
                  <small className="inbox-row-meta">{item.agent_name} · {leadChannels(item).length > 1 ? <ChannelDots channels={leadChannels(item)} t={t} /> : channelLabel(item.channel)}{item.account_label && <span className="account-badge" title={item.account_label}>{item.account_label}</span>} <span className={`mini-badge ${item.mode}`}>{item.mode === "human" ? t("inbox.modeHuman") : t("inbox.modeAi")}</span></small>
                </span>
                {item.unread_count > 0 && selected?.id !== item.id && <span className="inbox-unread-count" aria-label={t("inbox.unreadCount", { count: item.unread_count })}>{item.unread_count > 99 ? "99+" : item.unread_count}</span>}
              </button>
            ))}
            {loadingMore && <div className="no-conversations"><LoaderCircle className="spin" size={15} /></div>}
          </> : <div className="no-conversations">{t("inbox.empty")}</div>}
      </aside>

      <section className="inbox-thread drop-target" {...dropProps}>
        {overlay}
        {!selected ? <div className="empty-state"><div className="empty-icon"><InboxIcon /></div><h3>{t("inbox.empty")}</h3><p>{t("inbox.selectPrompt")}</p></div>
          : <>
            <header>
              <button type="button" className="icon-button inbox-back" onClick={() => { selectedIdRef.current = null; setSelected(null); }} aria-label={t("common.back")} title={t("common.back")}><ArrowLeft size={16} /></button>
              <LeadAvatarButton channel={selected.channel} open={leadOpen} onClick={() => setLeadOpen(!leadPanelOpen)} />
              <div><strong>{selected.contact_name || selected.title}</strong><small>{channelLabel(selected.channel)}{selected.account_label && <> <span className="account-badge" title={selected.account_label}>{selected.account_label}</span></>}</small></div>
              <div className="thread-actions">
                <button className="icon-button" onClick={() => setMediaOpen(true)} title={t("chat.sharedContent")} aria-label={t("chat.sharedContent")}><Images size={16} /></button>
                <button className={`mode-toggle ${selected.mode}`} onClick={() => toggleMode(selected.mode === "ai" ? "human" : "ai")}>{selected.mode === "ai" ? t("inbox.takeControl") : t("inbox.returnToAi")}</button>
              </div>
            </header>
            <div className="inbox-messages" ref={messagesRef}>
              {selected.messages?.map((message, index) => {
                if (isMergeActivity(message)) return <MergeAuditCard key={message.id} message={message} />;
                if (isAppointmentActivity(message)) return <AppointmentActivityCard key={message.id} message={message} />;
                const stamp = formatTime(message.created_at, lang);
                if (message.kind === "note") {
                  return (
                    <div key={message.id} className="internal-note-card">
                      <div className="internal-note-header">
                        <Lock size={12} />
                        <span>{message.sender_name || t("inbox.senderAgent")} · {t("inbox.internalNoteBadge")}</span>
                        <time>{stamp}</time>
                      </div>
                      <div className="internal-note-content">
                        <RichText text={message.content} />
                      </div>
                    </div>
                  );
                }
                const prev = index > 0 ? selected.messages![index - 1] : null;
                const grouped = Boolean(prev && prev.role === message.role && prev.sender_name === message.sender_name);
                const hasAudio = message.attachments?.some((a) => a.kind === "audio");
                return (
                  <div key={message.id} className={`inbox-message ${message.role}${grouped ? " grouped" : ""}`}>
                    {!grouped && <small>{message.sender_name || (message.role === "assistant" ? t("inbox.senderAgent") : t("inbox.senderVisitor"))}</small>}
                    <MessageAttachments attachments={message.attachments} urlFor={attachmentUrl} gallery={gallery} stamp={stamp} />
                    {message.content && <p><QuotedSnippet messages={selected.messages ?? []} quotedId={message.quoted_message_id} /><RichText text={message.content} /><time className="msg-time">{replyVia.multi && <MessageChannelMark channel={message.channel} t={t} />}{stamp}{message.role === "assistant" && isSocialChannel(message.channel ?? selected.channel) && <DeliveryTicks status={message.delivery_status} error={message.delivery_error} />}</time></p>}
                    <ReactionBadge emoji={message.reaction} />
                    <ReactionBadge emoji={message.incoming_reaction} incoming />
                    {!message.content && !hasAudio && message.attachments?.length ? <time className="msg-time bare">{replyVia.multi && <MessageChannelMark channel={message.channel} t={t} />}{stamp}</time> : null}
                  </div>
                );
              })}
            </div>
            <PhonePauseNotice conversation={selected} onKeepManual={() => toggleMode("human")} /><SocialReplyNotice conversation={selected} blocked={policy.blocked} humanOnly={policy.humanOnly} />
            {pendingFile && <PendingAttachment file={pendingFile} onCancel={() => setPendingFile(null)} />}
            {locating && <LocationComposer busy={busy} disabled={!policy.canReply} onCancel={() => setLocating(false)} onSend={sendLocation} />}
            <div className={`composer-box${composerMode === "note" ? " note-mode" : ""}`} style={{ position: "relative", margin: "10px 16px 14px" }}>
              <UnifiedComposerTop
                mode={composerMode}
                onModeChange={setComposerMode}
                threads={replyVia.threads}
                channel={replyVia.thread?.channel ?? selected.channel}
                via={replyVia.via}
                onViaChange={replyVia.setVia}
                onOpenVariables={() => { setVariablesQuery(""); setVariablesOpen((v) => !v); }}
                onOpenAppointmentModal={() => setAppointmentModalOpen(true)}
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
              <form className="inbox-composer" style={{ border: "none", padding: "8px 12px 10px", margin: 0 }} onSubmit={reply}>
                {(replyVia.thread?.channel ?? selected.channel) === "whatsapp" && composerMode !== "note" && <button type="button" className="icon-button" title={t("inbox.locationSend")} aria-label={t("inbox.locationSend")} disabled={!policy.canReply || busy} onClick={() => setLocating((open) => !open)}><MapPin size={17} /></button>}
                {composerMode !== "note" && <AttachButton onFile={setPendingFile} disabled={!policy.canAttach || busy} title={t("chat.attachFile")} />}
                {composerMode !== "note" && <RecordButton onRecorded={sendAttachment} onError={() => toast.error(t("chat.micDenied"))} disabled={!policy.canRecord || busy} title={t("chat.recordAudio")} titleStop={t("chat.stopRecording")} />}
                <GrowingTextarea
                  ref={composerRef}
                  name="content"
                  placeholder={
                    composerMode === "note"
                      ? (t("inbox.composerNotePlaceholder") || "Escribe una nota interna para el equipo...")
                      : selected.mode === "human"
                      ? t("inbox.composerHuman")
                      : t("inbox.composerLocked")
                  }
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
                <button
                  className={composerMode === "note" ? (draft.trim() ? "button primary small" : "button small") : ""}
                  style={composerMode === "note" ? { backgroundColor: draft.trim() ? "#f59e0b" : undefined, borderColor: draft.trim() ? "#f59e0b" : undefined, color: draft.trim() ? "#fff" : undefined } : undefined}
                  disabled={composerMode === "note" ? (busy || !draft.trim()) : (!policy.canReply || busy)}
                >
                  {composerMode === "note" ? (t("inbox.composerSaveNote") || "Guardar nota") : t("inbox.send")}
                </button>
              </form>
            </div>
            <MediaPanel open={mediaOpen} onClose={() => setMediaOpen(false)} messages={selected.messages ?? []} urlFor={attachmentUrl} />
          </>}
      </section>
      {leadOpen && selected && leadScope && <LeadScopeProvider scope={leadScope}>
        <LeadCard conversationId={selected.id} number={selected.number} messages={selected.messages ?? []} urlFor={attachmentUrl} overlay={leadOverlay} onClose={closeLead} onChanged={() => { loadFirst({ silent: true }); refreshSelected(); }} onMerged={(primary) => { void choose(primary.conversation_id).catch(() => {}); loadFirst({ silent: true }); }} syncKey={selected.messages?.at(-1)?.id} />
      </LeadScopeProvider>}
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
    </div>
  </div>;
}
