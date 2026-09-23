"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, GripVertical, LoaderCircle, Plus, Search, Trash2, Wand2, X } from "lucide-react";
import { Alert, EmptyState, Modal } from "@/components/ui";
import { ListRowsSkeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { ChannelIcon } from "@/lib/channels";
import { formatWhen } from "@/lib/datetime";
import { tagStyle } from "@/lib/tags";
import { api, messageFrom } from "@/lib/api";
import { useLanguage, useT, type Lang } from "@/lib/i18n";
import type { PipelineBoard as PipelineBoardData, PipelineCard, PipelineStage } from "@/types";

const UNASSIGNED = "__unassigned__";
// Distinct on both themes, in the order new stages take them.
const PALETTE = ["#2f6df0", "#00a67d", "#7c5cff", "#d4932f", "#c83b82", "#0891b2", "#c43d4b", "#65a30d"];

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "";
  return value.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

/** The sales pipeline: a client's own stages as kanban columns, with every
 * open conversation as a draggable card — including those not yet in any
 * stage, which sit in a virtual first column so dragging one in needs no
 * separate picker anywhere else. Shared by the client page and the portal.
 * Every edit to the stages themselves (add, rename, recolor, reorder,
 * delete) lives behind the single "Automatiza" button, Kommo-style. */
export function PipelineBoard({ base, canManage }: { base: string; canManage: boolean }) {
  const t = useT();
  const { lang } = useLanguage();
  const toast = useToast();
  const [board, setBoard] = useState<PipelineBoardData | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [quickLeadStage, setQuickLeadStage] = useState<PipelineStage | null>(null);
  const load = useCallback(async () => {
    try { setBoard(await api<PipelineBoardData>(`${base}/pipeline/board`)); }
    catch (err) { setError(messageFrom(err)); }
  }, [base]);
  useEffect(() => { load(); }, [load]);

  const [managing, setManaging] = useState(false);
  const [dragCardId, setDragCardId] = useState<string | null>(null);
  const [dragOverStage, setDragOverStage] = useState<string | null>(null);

  const matches = useCallback((card: PipelineCard) => {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return [card.contact_name, card.title, card.preview].some((field) => (field || "").toLowerCase().includes(needle));
  }, [query]);

  const cardsByStage = useMemo(() => {
    const map = new Map<string, PipelineCard[]>();
    for (const card of board?.cards ?? []) {
      if (!matches(card)) continue;
      const key = card.pipeline_stage_id ?? UNASSIGNED;
      map.set(key, [...(map.get(key) ?? []), card]);
    }
    return map;
  }, [board, matches]);

  const threadUrl = useCallback((id: string) => (
    base.startsWith("/portal") ? `${base}?conversation=${id}` : `/inbox?conversation=${id}`
  ), [base]);

  async function moveCard(card: PipelineCard, stageId: string | null) {
    if (card.pipeline_stage_id === stageId) return;
    // Optimistic: the board feels instant, and a failure just reloads it.
    setBoard((current) => current && {
      ...current,
      cards: current.cards.map((c) => (c.id === card.id ? { ...c, pipeline_stage_id: stageId } : c)),
    });
    try {
      await api(`/conversations/${card.id}/pipeline`, { method: "PATCH", body: JSON.stringify({ pipeline_stage_id: stageId, deal_value: card.deal_value }) });
    } catch { toast.error(t("pipeline.moveFailed")); load(); }
  }

  async function editValue(card: PipelineCard, value: number | null) {
    setBoard((current) => current && { ...current, cards: current.cards.map((c) => (c.id === card.id ? { ...c, deal_value: value } : c)) });
    try {
      await api(`/conversations/${card.id}/pipeline`, { method: "PATCH", body: JSON.stringify({ pipeline_stage_id: card.pipeline_stage_id, deal_value: value }) });
    } catch (err) { toast.error(messageFrom(err)); load(); }
  }

  if (error) return <Alert>{error}</Alert>;
  if (!board) return <ListRowsSkeleton rows={3} />;

  const columns: { id: string | null; name: string; color: string; count: number; total: number | null }[] = [
    { id: null, name: t("pipeline.unassignedColumn"), color: "#8996a3", count: board.unassigned_count, total: null },
    ...board.stages.map((s) => ({ id: s.id, name: s.name, color: s.color, count: s.conversation_count, total: s.deal_value_total })),
  ];
  const totalDeals = board.cards.length;
  const totalValue = board.cards.reduce((sum, card) => sum + (card.deal_value ?? 0), 0);

  return <div className="pipeline-view">
    {board.stages.length === 0 && !canManage
      ? <section className="form-section"><div className="section-copy"><h2>{t("pipeline.title")}</h2><p>{t("pipeline.description")}</p></div><EmptyState icon={<GripVertical />} title={t("pipeline.stagesEmptyTitle")} description={t("pipeline.stagesEmptyDescription")} /></section>
      : <div className="pipeline-topbar">
          <button type="button" className="pipeline-profile-btn" title="Settings"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"></circle><path d="M12 1v6m0 6v6"></path><path d="M4.22 4.22l4.24 4.24m5.08 0l4.24-4.24"></path><path d="M1 12h6m6 0h6"></path><path d="M4.22 19.78l4.24-4.24m5.08 0l4.24 4.24"></path></svg></button>
          <span className="pipeline-name">{t("pipeline.title")}</span>
          <div className="pipeline-view-toggle">
            <button type="button" className="pipeline-view-btn active" title="Kanban"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg></button>
            <button type="button" className="pipeline-view-btn" title="List"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg></button>
          </div>
          <label className="pipeline-search-compact"><Search size={15} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("pipeline.searchPlaceholder")} aria-label={t("pipeline.searchPlaceholder")} /></label>
          <span className="pipeline-totals-compact">{money(totalValue)}</span>
          {canManage && <button type="button" className="pipeline-automate-btn" onClick={() => setManaging(true)}><Wand2 size={15} /> {t("pipeline.automate")}</button>}
        </div>}

    {board.stages.length > 0 && <div className="pipeline-board">
      {columns.map((column) => {
        const cards = cardsByStage.get(column.id ?? UNASSIGNED) ?? [];
        const isOver = dragOverStage === (column.id ?? UNASSIGNED);
        const stage = column.id ? board.stages.find((s) => s.id === column.id) ?? null : null;
        return <div key={column.id ?? UNASSIGNED} className={`pipeline-column${isOver ? " pipeline-column-over" : ""}`}
          style={{ borderTop: `3px solid ${column.color}` }}
          onDragOver={(e) => { e.preventDefault(); setDragOverStage(column.id ?? UNASSIGNED); }}
          onDragLeave={() => setDragOverStage((current) => (current === (column.id ?? UNASSIGNED) ? null : current))}
          onDrop={(e) => {
            e.preventDefault(); setDragOverStage(null);
            const card = board.cards.find((c) => c.id === dragCardId);
            if (card) moveCard(card, column.id);
            setDragCardId(null);
          }}>
          <header className="pipeline-column-head" style={{ backgroundColor: column.color }}>
            <strong>{column.name}</strong>
            <span className="pipeline-column-count">{t("pipeline.columnDeals", { count: column.count })}{column.total != null && column.total > 0 ? ` · ${money(column.total)}` : ""}</span>
          </header>
          <div className="pipeline-cards">
            {stage && <button type="button" className="pipeline-quick-add" onClick={() => setQuickLeadStage(stage)}><Plus size={14} /> {t("pipeline.quickLead")}</button>}
            {cards.length === 0 ? <div className="pipeline-empty-column">{t("pipeline.emptyColumn")}</div>
              : cards.map((card) => <PipelineCardView key={card.id} card={card} t={t} lang={lang}
                threadUrl={threadUrl(card.id)} onDragStart={() => setDragCardId(card.id)} onValueChange={(value) => editValue(card, value)} />)}
          </div>
        </div>;
      })}
    </div>}

    {quickLeadStage && <QuickLeadModal base={base} stage={quickLeadStage}
      onClose={() => setQuickLeadStage(null)} onCreated={() => load()} />}
    <AutomationModal open={managing} base={base} stages={board.stages} onClose={() => setManaging(false)}
      onChange={(stages) => setBoard((current) => current && { ...current, stages })} />
  </div>;
}

function PipelineCardView({ card, t, lang, threadUrl, onDragStart, onValueChange }: {
  card: PipelineCard; t: ReturnType<typeof useT>; lang: Lang; threadUrl: string;
  onDragStart: () => void; onValueChange: (value: number | null) => void;
}) {
  const [editingValue, setEditingValue] = useState(false);
  const [draft, setDraft] = useState(card.deal_value != null ? String(card.deal_value) : "");
  const name = card.contact_name || card.title;
  function commit() {
    setEditingValue(false);
    const trimmed = draft.trim();
    onValueChange(trimmed === "" ? null : Number(trimmed));
  }
  return <article className="pipeline-card" draggable onDragStart={onDragStart}>
    <div className="pipeline-card-top">
      <span className="pipeline-avatar" aria-hidden="true">{name.slice(0, 1).toUpperCase()}</span>
      <div className="pipeline-card-identity">
        <a className="pipeline-card-name" href={threadUrl} target="_blank" rel="noreferrer" draggable={false} title={t("pipeline.openThread")}><strong>{name}</strong></a>
        <small className="pipeline-card-date">{formatWhen(card.updated_at, lang)}</small>
      </div>
      <ChannelIcon channel={card.channel} size={13} />
    </div>
    {card.account_label && <small className="pipeline-card-account">{card.account_label}</small>}
    {card.mode === "human" && <span className="mini-badge human">{t("portal.inbox.list.humanSupport")}</span>}
    {card.tags.length > 0 && <span className="pipeline-card-tags">{card.tags.map((tag) => <span key={tag.name} className="tag-chip" style={tagStyle(tag.color)}>{tag.name}</span>)}</span>}
    {card.preview && <p className="pipeline-card-preview">{card.preview}</p>}
    {editingValue
      ? <input className="pipeline-value-input" type="number" min={0} step={0.01} autoFocus value={draft}
          onChange={(e) => setDraft(e.target.value)} onBlur={commit}
          onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") { setDraft(card.deal_value != null ? String(card.deal_value) : ""); setEditingValue(false); } }} />
      : <button type="button" className="pipeline-value" onClick={() => setEditingValue(true)} title={t("pipeline.editValue")}>
          {card.deal_value != null ? money(card.deal_value) : <span className="pipeline-value-empty">{t("pipeline.noValue")}</span>}
        </button>}
  </article>;
}

/** A manually created deal ("quick lead"): name, optional phone and value,
 * landing straight in the stage whose column opened it. */
function QuickLeadModal({ base, stage, onClose, onCreated }: {
  base: string; stage: PipelineStage; onClose: () => void; onCreated: () => void;
}) {
  const t = useT();
  const toast = useToast();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  async function create(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      const payload: Record<string, string | number> = { contact_name: trimmed, pipeline_stage_id: stage.id };
      if (phone.trim()) payload.contact_phone = phone.trim();
      if (value.trim()) payload.deal_value = Number(value);
      await api(`${base}/pipeline/leads`, { method: "POST", body: JSON.stringify(payload) });
      toast.success(t("pipeline.quickLeadCreated"));
      onCreated();
      onClose();
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }
  return <Modal open title={t("pipeline.quickLeadTitle", { stage: stage.name })} onClose={onClose}>
    <form className="modal-form" onSubmit={create}>
      <div className="wa-cloud-form">
        <label>{t("pipeline.quickLeadName")}<input value={name} maxLength={180} onChange={(e) => setName(e.target.value)} disabled={busy} autoFocus /></label>
        <label>{t("pipeline.quickLeadPhone")}<input value={phone} maxLength={40} onChange={(e) => setPhone(e.target.value)} disabled={busy} autoComplete="off" /></label>
        <label>{t("pipeline.quickLeadValue")}<input type="number" min={0} step={0.01} value={value} onChange={(e) => setValue(e.target.value)} disabled={busy} /></label>
      </div>
      <div className="modal-actions">
        <button type="button" className="button" onClick={onClose}>{t("common.cancel")}</button>
        <button type="submit" className="button primary" disabled={busy || !name.trim()}>{busy ? <LoaderCircle className="spin" size={16} /> : <Plus size={15} />} {t("pipeline.quickLeadCreate")}</button>
      </div>
    </form>
  </Modal>;
}

/** The "Automatiza" screen: every edit to the stage list in one place — add,
 * rename, recolor, reorder (up/down; this is a short list, so arrows beat a
 * drag target that's easy to miss inside a modal), delete. Each row saves
 * itself as soon as it changes, so there is no separate save step. */
function AutomationModal({ open, base, stages, onClose, onChange }: {
  open: boolean; base: string; stages: PipelineStage[]; onClose: () => void; onChange: (stages: PipelineStage[]) => void;
}) {
  const t = useT();
  const toast = useToast();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [addBusy, setAddBusy] = useState(false);

  async function rename(stage: PipelineStage, name: string) {
    const trimmed = name.trim();
    if (!trimmed || trimmed === stage.name) return;
    setBusyId(stage.id);
    try {
      const updated = await api<PipelineStage>(`${base}/pipeline/stages/${stage.id}`, { method: "PATCH", body: JSON.stringify({ name: trimmed }) });
      onChange(stages.map((s) => (s.id === updated.id ? { ...s, ...updated } : s)));
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusyId(null); }
  }

  async function recolor(stage: PipelineStage, color: string) {
    onChange(stages.map((s) => (s.id === stage.id ? { ...s, color } : s)));
    try {
      await api<PipelineStage>(`${base}/pipeline/stages/${stage.id}`, { method: "PATCH", body: JSON.stringify({ color }) });
    } catch (err) { toast.error(messageFrom(err)); }
  }

  async function move(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= stages.length) return;
    const reordered = [...stages];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    onChange(reordered);
    try {
      const saved = await api<PipelineStage[]>(`${base}/pipeline/stages/reorder`, { method: "POST", body: JSON.stringify({ stage_ids: reordered.map((s) => s.id) }) });
      onChange(saved);
    } catch (err) { toast.error(messageFrom(err)); }
  }

  async function remove(stage: PipelineStage) {
    setBusyId(stage.id);
    try {
      await api(`${base}/pipeline/stages/${stage.id}`, { method: "DELETE" });
      onChange(stages.filter((s) => s.id !== stage.id));
      setConfirmingDelete(null);
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusyId(null); }
  }

  async function addStage(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = newName.trim();
    if (!trimmed) return;
    setAddBusy(true);
    try {
      const color = PALETTE[stages.length % PALETTE.length];
      const created = await api<PipelineStage>(`${base}/pipeline/stages`, { method: "POST", body: JSON.stringify({ name: trimmed, color }) });
      onChange([...stages, created]);
      setNewName("");
      toast.success(t("pipeline.stageAdded"));
    } catch (err) { toast.error(messageFrom(err)); } finally { setAddBusy(false); }
  }

  return <Modal open={open} title={t("pipeline.automate")} description={t("pipeline.automateCopy")} onClose={onClose} wide>
    <div className="modal-form">
      <div className="pipeline-automation-list">
        {stages.length === 0 && <p className="field-help">{t("pipeline.stagesEmptyDescription")}</p>}
        {stages.map((stage, index) => <div key={stage.id} className="pipeline-automation-row">
          <div className="pipeline-automation-order">
            <button type="button" className="icon-button small" disabled={index === 0} onClick={() => move(index, -1)} aria-label={t("pipeline.moveUp")} title={t("pipeline.moveUp")}><ArrowUp size={13} /></button>
            <button type="button" className="icon-button small" disabled={index === stages.length - 1} onClick={() => move(index, 1)} aria-label={t("pipeline.moveDown")} title={t("pipeline.moveDown")}><ArrowDown size={13} /></button>
          </div>
          <input type="color" className="pipeline-automation-color" value={stage.color} onChange={(e) => recolor(stage, e.target.value)} title={t("pipeline.stageColor")} />
          <input className="pipeline-automation-name" defaultValue={stage.name} maxLength={80}
            onBlur={(e) => rename(stage, e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
          {busyId === stage.id && <LoaderCircle size={14} className="spin" />}
          {confirmingDelete === stage.id
            ? <span className="pipeline-automation-confirm">
                <button type="button" className="button danger small" onClick={() => remove(stage)}>{t("pipeline.deleteStageConfirm")}</button>
                <button type="button" className="icon-button small" onClick={() => setConfirmingDelete(null)} aria-label={t("common.cancel")}><X size={13} /></button>
              </span>
            : <button type="button" className="icon-button small danger-icon" onClick={() => setConfirmingDelete(stage.id)} aria-label={t("pipeline.deleteStage")} title={t("pipeline.deleteStage")}><Trash2 size={14} /></button>}
        </div>)}
      </div>
      <form className="pipeline-automation-add" onSubmit={addStage}>
        <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder={t("pipeline.stageNamePlaceholder")} maxLength={80} />
        <button type="submit" className="button secondary" disabled={addBusy || !newName.trim()}>{addBusy ? <LoaderCircle size={15} className="spin" /> : <Plus size={15} />} {t("pipeline.addStage")}</button>
      </form>
      <div className="modal-actions"><button type="button" className="button primary" onClick={onClose}>{t("pipeline.done")}</button></div>
    </div>
  </Modal>;
}
