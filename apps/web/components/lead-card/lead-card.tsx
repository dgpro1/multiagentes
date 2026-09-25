"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, Copy, GitMerge, Link2, MoreHorizontal, Settings2, UserRound, X } from "lucide-react";
import { InlineInput, parseAmount } from "@/components/lead-card/inline-input";
import { FieldManager } from "@/components/lead-card/field-manager";
import { AppointmentsSection } from "@/components/lead-card/appointments-section";
import { useLeadScope } from "@/components/lead-card/scope";
import { MergeDialog } from "@/components/merge-leads/merge-dialog";
import { SharedContentList } from "@/components/shared-content";
import { TagEditor } from "@/components/tag-editor";
import { ListRowsSkeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { api, messageFrom } from "@/lib/api";
import { ChannelIcon, channelLabel } from "@/lib/channels";
import { formatMoney } from "@/lib/currencies";
import { useLanguage, useT } from "@/lib/i18n";
import { tagStyle } from "@/lib/tags";
import type { Attachment, ContactTag, LeadCard as LeadCardData, LeadField, LeadStage, LeadValue, Message } from "@/types";

type Member = { id: string; name: string };
// The card as loaded for one conversation; kept with its id so a card of the previous conversation is never shown for the next.
type Loaded = { id: string; card: LeadCardData | null; error: string | null };

/** Runs `onEscape` on Escape while `active` (menus that close before the panel does). */
function useEscape(active: boolean, onEscape: () => void) {
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onEscape(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, onEscape]);
}

/** The side panel of an open conversation, Kommo-style: the lead's number and
 * tags, its stage in the pipeline, who is responsible, its budget, the client's
 * custom fields and the contact's details, plus the files shared in the chat.
 * Where the data lives (agency or portal) comes from the LeadScope above it. */
export function LeadCard({ conversationId, number, messages, urlFor, overlay = false, onClose, onChanged, onMerged, syncKey }: {
  conversationId: string;
  /** The lead's number, shown while the card is still loading. */
  number: number;
  /** The open thread's messages: the Files tab lists what was shared in them. */
  messages: Message[];
  urlFor: (attachment: Attachment) => string;
  /** True when the panel covers the screen: it takes focus and closes on Escape. */
  overlay?: boolean;
  onClose: () => void;
  /** Called after a save that may show elsewhere (the contact's name in the list, say). */
  onChanged?: () => void;
  /** Called after this lead was merged with another; receives the primary lead, which the host opens and reloads. */
  onMerged?: (primary: LeadCardData) => void;
  /** Changes when the thread does (a new message); the card reloads quietly, since the agent may have moved the lead. */
  syncKey?: string | number | null;
}) {
  const t = useT();
  const { lang } = useLanguage();
  const toast = useToast();
  const scope = useLeadScope();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [stages, setStages] = useState<LeadStage[] | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [catalog, setCatalog] = useState<ContactTag[]>([]);
  const [tab, setTab] = useState<"main" | "files">("main");
  const [managing, setManaging] = useState(false);
  const [merging, setMerging] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [tagsBusy, setTagsBusy] = useState(false);
  // Bumped when a save fails, to put every input back to what the server holds.
  const [rev, setRev] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);

  const card = loaded && loaded.id === conversationId ? loaded.card : null;
  const error = loaded && loaded.id === conversationId ? loaded.error : null;

  useEffect(() => {
    let cancelled = false;
    api<LeadCardData>(scope.leadPath(conversationId))
      .then((next) => { if (!cancelled) setLoaded({ id: conversationId, card: next, error: null }); })
      .catch((err) => { if (!cancelled) setLoaded((prev) => (prev && prev.id === conversationId && prev.card ? prev : { id: conversationId, card: null, error: messageFrom(err) })); });
    return () => { cancelled = true; };
  }, [scope, conversationId, attempt, syncKey]);

  const refresh = useCallback(async () => {
    try {
      const next = await api<LeadCardData>(scope.leadPath(conversationId));
      setLoaded({ id: conversationId, card: next, error: null });
    } catch { /* the card on screen stays; the next reload tries again */ }
  }, [scope, conversationId]);

  // What the pickers offer: pipeline stages, people, and the tag catalog. A part the
  // client's functions do not include (or the person may not read) is simply left out.
  const loadCatalog = useCallback(() => {
    api<ContactTag[]>(scope.tagCatalogPath).then(setCatalog).catch(() => setCatalog([]));
  }, [scope]);
  useEffect(() => {
    let cancelled = false;
    api<LeadStage[]>(scope.stagesPath).then((rows) => { if (!cancelled) setStages(rows); }).catch(() => { if (!cancelled) setStages(null); });
    api<Member[]>(scope.membersPath).then((rows) => { if (!cancelled) setMembers(rows); }).catch(() => { if (!cancelled) setMembers([]); });
    api<ContactTag[]>(scope.tagCatalogPath).then((rows) => { if (!cancelled) setCatalog(rows); }).catch(() => { if (!cancelled) setCatalog([]); });
    return () => { cancelled = true; };
  }, [scope]);

  // As an overlay the panel is a dialog of its own: focus goes in, Escape closes it
  // (unless a menu, the field manager or the image preview has it first).
  useEffect(() => {
    if (overlay) closeRef.current?.focus();
  }, [overlay]);
  useEffect(() => {
    if (!overlay) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (document.querySelector(".lightbox, .modal-backdrop, .menu-backdrop")) return;
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [overlay, onClose]);
  useEscape(menuOpen, () => setMenuOpen(false));

  function failed(err: unknown) {
    toast.error(messageFrom(err));
    setRev((r) => r + 1);
    void refresh();
  }

  async function patchLead(body: Record<string, unknown>) {
    try {
      const next = await api<LeadCardData>(scope.leadPath(conversationId), { method: "PATCH", body: JSON.stringify(body) });
      setLoaded({ id: conversationId, card: next, error: null });
      onChanged?.();
    } catch (err) { failed(err); }
  }

  // The pipeline route takes the whole state, so the value that is not being changed travels along.
  async function savePipeline(stageId: string | null, dealValue: number | null) {
    setLoaded((prev) => prev && prev.card && { ...prev, card: { ...prev.card, deal_value: dealValue, stage: stageId ? stages?.find((stage) => stage.id === stageId) ?? prev.card.stage : null } });
    try {
      await api(scope.pipelinePath(conversationId), { method: "PATCH", body: JSON.stringify({ pipeline_stage_id: stageId, deal_value: dealValue }) });
      await refresh();
      onChanged?.();
    } catch (err) { failed(err); }
  }

  async function saveContact(body: Record<string, string | null>) {
    const id = card?.contact.id;
    if (!id) return;
    try {
      await api(scope.contactPath(id), { method: "PATCH", body: JSON.stringify(body) });
      await refresh();
      onChanged?.();
    } catch (err) { failed(err); }
  }

  // Tags belong to the contact; the whole set is sent, as in the contacts screen.
  async function applyTags(ids: string[]) {
    const id = card?.contact.id;
    if (!id) return;
    setTagsBusy(true);
    try {
      const updated = await api<{ tags?: { id: string; name: string; color: string }[] }>(scope.contactTagsPath(id), { method: "PUT", body: JSON.stringify({ tag_ids: ids }) });
      if (updated?.tags) setLoaded((prev) => prev && prev.card && { ...prev, card: { ...prev.card, contact: { ...prev.card.contact, tags: updated.tags ?? prev.card.contact.tags } } });
      await refresh();
      loadCatalog();
    } catch (err) { toast.error(messageFrom(err)); } finally { setTagsBusy(false); }
  }
  function toggleTag(tag: { id: string }) {
    if (!card) return;
    const current = card.contact.tags.map((item) => item.id);
    void applyTags(current.includes(tag.id) ? current.filter((id) => id !== tag.id) : [...current, tag.id]);
  }
  async function createTag(name: string) {
    if (!card) return;
    try {
      const created = await api<ContactTag>(scope.tagCatalogPath, { method: "POST", body: JSON.stringify({ name }) });
      await applyTags([...card.contact.tags.map((item) => item.id), created.id]);
    } catch (err) { toast.error(messageFrom(err)); }
  }

  function copy(text: string, done: string) {
    setMenuOpen(false);
    navigator.clipboard?.writeText(text).then(() => toast.success(done)).catch((err) => toast.error(messageFrom(err)));
  }

  const shownNumber = card?.number ?? number;
  const fields = card?.fields ?? [];

  return <div className="lead-panel" role="complementary" aria-label={t("lead.panelLabel")}>
    <header className="lead-head">
      <strong>{t("lead.title", { number: shownNumber })}</strong>
      <span className="lead-head-actions">
        <span className="start-line-wrap">
          <button type="button" className="icon-button" onClick={() => setMenuOpen((v) => !v)} aria-haspopup="menu" aria-expanded={menuOpen} aria-label={t("lead.menu")} title={t("lead.menu")}><MoreHorizontal size={16} /></button>
          {menuOpen && <>
            <div className="menu-backdrop" onClick={() => setMenuOpen(false)} />
            <div className="start-line-menu lead-menu" role="menu">
              {scope.leadLink && <button type="button" role="menuitem" onClick={() => copy(scope.leadLink!(shownNumber), t("lead.linkCopied"))}><Link2 size={15} /><span><strong>{t("lead.copyLink")}</strong></span></button>}
              <button type="button" role="menuitem" onClick={() => copy(String(shownNumber), t("lead.numberCopied"))}><Copy size={15} /><span><strong>{t("lead.copyNumber")}</strong></span></button>
              {scope.canEditContact && card && <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); setMerging(true); }}><GitMerge size={15} /><span><strong>{t("lead.mergeWith")}</strong></span></button>}
            </div>
          </>}
        </span>
        <button type="button" ref={closeRef} className="icon-button" onClick={onClose} aria-label={t("lead.close")} title={t("lead.close")}><X size={16} /></button>
      </span>
    </header>

    <div className="lead-scroll">
      {!card ? (error
        ? <div className="lead-error" role="alert"><p>{t("lead.loadFailed")}</p><small>{error}</small><button type="button" className="button secondary small" onClick={() => setAttempt((n) => n + 1)}>{t("lead.retry")}</button></div>
        : <div className="lead-skeleton"><ListRowsSkeleton rows={5} /></div>)
        : <>
          <section className="lead-section">
            <TagEditor tags={catalog} value={card.contact.tags} onToggle={toggleTag} onCreate={createTag} canCreate={scope.canCreateTags} busy={tagsBusy} readOnly={!card.contact.id || !scope.canEditContact} />
          </section>

          {stages && <StageSelect stages={stages} current={card.stage} onPick={(stage) => savePipeline(stage, card.deal_value)} />}

          <div className="lead-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={tab === "main"} className={tab === "main" ? "active" : ""} onClick={() => setTab("main")}>{t("lead.tabMain")}</button>
            <button type="button" role="tab" aria-selected={tab === "files"} className={tab === "files" ? "active" : ""} onClick={() => setTab("files")}>{t("lead.tabFiles")}</button>
            {scope.canManageFields && <button type="button" className="lead-configure" onClick={() => setManaging(true)} aria-label={t("lead.configure")} title={t("lead.configure")}><Settings2 size={15} /><span>{t("lead.configure")}</span></button>}
          </div>

          {tab === "files"
            ? <div className="lead-files"><SharedContentList messages={messages} urlFor={urlFor} /></div>
            : <div className="lead-main" key={rev}>
              <ResponsibleRow card={card} members={members} onPick={(id) => patchLead({ responsible_id: id })} />
              {stages && <label className="lead-row">
                <span className="lead-row-label">{t("lead.budget")}</span>
                <InlineInput
                  value={card.deal_value === null ? "" : String(card.deal_value)}
                  inputMode="decimal"
                  ariaLabel={t("lead.budget")}
                  placeholder={t("lead.budgetPlaceholder")}
                  display={(raw) => { const amount = Number(raw); return raw.trim() !== "" && Number.isFinite(amount) ? formatMoney(amount, card.currency, lang) : raw; }}
                  onCommit={(text) => {
                    const amount = parseAmount(text);
                    if (amount === undefined) { toast.error(t("lead.invalidAmount")); setRev((r) => r + 1); return; }
                    return savePipeline(card.stage?.id ?? null, amount);
                  }}
                />
              </label>}

              <h4 className="lead-subhead">{t("lead.customFields")}</h4>
              {fields.length === 0
                ? <p className="lead-empty">{t("lead.noCustomFields")}{scope.canManageFields && <small>{t("lead.noCustomFieldsHint")}</small>}</p>
                : fields.map((field) => <CustomFieldRow key={field.id} field={field} value={card.custom_values[field.key]} onSave={(value) => patchLead({ custom_values: { [field.key]: value } })} />)}

              <ContactBlock card={card} editable={scope.canEditContact && Boolean(card.contact.id)} onSave={saveContact} onCopyPhone={(phone) => copy(phone, t("lead.phoneCopied"))} />

              <AppointmentsSection
                conversationId={conversationId}
                contactId={card.contact.id}
                base={scope.leadsBase}
                syncKey={syncKey}
                onChanged={refresh}
              />
            </div>}
        </>}
    </div>

    {merging && card && <MergeDialog card={card} onClose={() => setMerging(false)} onMerged={(primary) => { setMerging(false); onMerged?.(primary); }} />}
    {scope.canManageFields && <FieldManager open={managing} fields={fields} onClose={() => setManaging(false)} onChanged={() => { void refresh(); onChanged?.(); }} />}
  </div>;
}

/** The current stage as a rounded dropdown, and under it a thin bar filled by the stage's place among all the stages. */
function StageSelect({ stages, current, onPick }: { stages: LeadStage[]; current: LeadStage | null; onPick: (stageId: string | null) => void }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  useEscape(open, () => setOpen(false));
  const index = current ? stages.findIndex((stage) => stage.id === current.id) : -1;
  const position = index + 1;
  const percent = stages.length && position > 0 ? Math.round((position / stages.length) * 100) : 0;
  const color = current ? tagStyle(current.color) : undefined;
  return <section className="lead-section lead-stage">
    <div className="start-line-wrap">
      <button type="button" className="lead-stage-trigger" style={color} onClick={() => setOpen((v) => !v)} aria-haspopup="listbox" aria-expanded={open} aria-label={t("lead.stage")} disabled={stages.length === 0}>
        <i className="tag-dot" style={color} />
        <span>{current ? current.name : t("lead.noStage")}</span>
        <ChevronDown size={15} />
      </button>
      {open && <>
        <div className="menu-backdrop" onClick={() => setOpen(false)} />
        <ul className="start-line-menu lead-stage-menu" role="listbox" aria-label={t("lead.stage")}>
          {stages.map((stage) => <li key={stage.id}><button type="button" role="option" aria-selected={stage.id === current?.id} onClick={() => { setOpen(false); if (stage.id !== current?.id) onPick(stage.id); }}><i className="tag-dot" style={tagStyle(stage.color)} /><span><strong>{stage.name}</strong></span>{stage.id === current?.id && <span className="lead-stage-check" aria-hidden="true">✓</span>}</button></li>)}
        </ul>
      </>}
    </div>
    <div className="lead-progress" role="progressbar" aria-valuemin={0} aria-valuemax={stages.length} aria-valuenow={Math.max(position, 0)} aria-label={position > 0 ? t("lead.stageProgress", { position, total: stages.length }) : t("lead.noStage")}>
      <i style={{ width: `${percent}%`, ...(color ?? {}) }} />
    </div>
  </section>;
}

/** "Responsible user": a person of the team, or the client's default owner (null). */
function ResponsibleRow({ card, members, onPick }: { card: LeadCardData; members: Member[]; onPick: (id: string | null) => void }) {
  const t = useT();
  const { responsible, owner_name } = card;
  const defaultName = owner_name || responsible.name;
  const known = members.some((member) => member.id === responsible.id);
  return <label className="lead-row">
    <span className="lead-row-label">{t("lead.responsible")}</span>
    <select className="lead-input" value={responsible.id ?? ""} onChange={(event) => onPick(event.target.value || null)} aria-label={t("lead.responsible")}>
      <option value="">{defaultName ? t("lead.responsibleDefault", { name: defaultName }) : t("lead.responsibleDefaultUnset")}</option>
      {responsible.id && !known && <option value={responsible.id}>{responsible.name ?? responsible.id}</option>}
      {members.map((member) => <option key={member.id} value={member.id}>{member.name}</option>)}
    </select>
  </label>;
}

/** One custom field, edited in place by its type; every change is saved at once (text and numbers when the box loses focus). */
function CustomFieldRow({ field, value, onSave }: { field: LeadField; value: LeadValue | undefined; onSave: (value: LeadValue) => void }) {
  const t = useT();
  const text = value === null || value === undefined ? "" : String(value);
  if (field.type === "checkbox") {
    return <label className="lead-row lead-row-check">
      <span className="lead-row-label">{field.label}</span>
      <input type="checkbox" checked={value === true} onChange={(event) => onSave(event.target.checked)} aria-label={field.label} />
    </label>;
  }
  if (field.type === "select") {
    return <label className="lead-row">
      <span className="lead-row-label">{field.label}</span>
      <select className="lead-input" value={text} onChange={(event) => onSave(event.target.value || null)} aria-label={field.label}>
        <option value="">{t("lead.selectNone")}</option>
        {text && !field.options.includes(text) && <option value={text}>{text}</option>}
        {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
      </select>
    </label>;
  }
  if (field.type === "date") {
    return <label className="lead-row">
      <span className="lead-row-label">{field.label}</span>
      <input className="lead-input" type="date" value={text} onChange={(event) => onSave(event.target.value || null)} aria-label={field.label} />
    </label>;
  }
  return <label className="lead-row">
    <span className="lead-row-label">{field.label}</span>
    <InlineInput
      value={text}
      type={field.type === "number" ? "number" : "text"}
      ariaLabel={field.label}
      maxLength={field.type === "text" ? 500 : undefined}
      onCommit={(next) => {
        if (field.type === "number") { const parsed = Number(next); onSave(next === "" || !Number.isFinite(parsed) ? null : parsed); return; }
        onSave(next === "" ? null : next);
      }}
    />
  </label>;
}

/** Who the lead is: avatar, names, channel and the contact's own fields, editable in place where allowed. */
function ContactBlock({ card, editable, onSave, onCopyPhone }: {
  card: LeadCardData;
  editable: boolean;
  onSave: (body: Record<string, string | null>) => void;
  onCopyPhone: (phone: string) => void;
}) {
  const t = useT();
  const { contact } = card;
  const linked = card.linked_channels ?? [];
  return <section className="lead-contact">
    <div className="lead-contact-head">
      <span className="inbox-avatar">
        <span className="entity-avatar"><UserRound size={17} /></span>
        <span className={`channel-badge ${card.channel}`}><ChannelIcon channel={card.channel} /></span>
      </span>
      <div className="lead-contact-names">
        <InlineInput
          value={contact.name ?? ""}
          ariaLabel={t("lead.contactName")}
          placeholder={t("lead.addName")}
          readOnly={!editable}
          maxLength={180}
          onCommit={(next) => { if (next) onSave({ name: next }); }}
        />
        {contact.whatsapp_name && <small className="lead-wa-name">{t("lead.whatsappName")}: {contact.whatsapp_name}</small>}
        <span className="lead-contact-tags">
          <span className={`lead-channel-pill ${card.channel}`}><ChannelIcon channel={card.channel} size={11} />{channelLabel(card.channel, t)}{card.account_label ? ` · ${card.account_label}` : ""}</span>
          {contact.blocked && <span className="lead-blocked">{t("lead.blocked")}</span>}
        </span>
      </div>
    </div>
    {linked.length > 1 && <div className="lead-linked">
      <span className="lead-row-label">{t("lead.linkedChannels")}</span>
      <ul>{linked.map((item) => <li key={item.conversation_id}>
        <span className={`lead-channel-pill ${item.channel}`}><ChannelIcon channel={item.channel} size={11} />{channelLabel(item.channel, t)}</span>
        <span className="lead-linked-name">{item.label || item.account_label || ""}</span>
        {item.is_primary && <em className="lead-linked-primary">{t("lead.primaryMark")}</em>}
      </li>)}</ul>
    </div>}
    {!contact.id && <p className="lead-empty"><small>{t("lead.noContact")}</small></p>}
    <div className="lead-row">
      <span className="lead-row-label">{t("lead.phone")}</span>
      <span className="lead-row-value">
        <InlineInput value={contact.phone ?? ""} ariaLabel={t("lead.phone")} placeholder={t("lead.addPhone")} readOnly={!editable} maxLength={40} inputMode="tel" onCommit={(next) => { if (next) onSave({ phone: next }); }} />
        {contact.phone && <button type="button" className="lead-copy" onClick={() => onCopyPhone(contact.phone!)} aria-label={t("lead.copyPhone")} title={t("lead.copyPhone")}><Copy size={14} /></button>}
      </span>
    </div>
    <div className="lead-row">
      <span className="lead-row-label">{t("lead.email")}</span>
      <InlineInput value={contact.email ?? ""} ariaLabel={t("lead.email")} placeholder={t("lead.addEmail")} readOnly={!editable} maxLength={254} inputMode="email" onCommit={(next) => onSave({ email: next || null })} />
    </div>
    <div className="lead-row">
      <span className="lead-row-label">{t("lead.company")}</span>
      <InlineInput value={contact.company ?? ""} ariaLabel={t("lead.company")} placeholder={t("lead.addCompany")} readOnly={!editable} maxLength={180} onCommit={(next) => onSave({ company: next })} />
    </div>
  </section>;
}
