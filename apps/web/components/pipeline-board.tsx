"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, GripVertical, LoaderCircle, Plus, Trash2, Wand2, X } from "lucide-react";
import { Alert, EmptyState, Modal } from "@/components/ui";
import { ListRowsSkeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { ChannelIcon } from "@/lib/channels";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
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
  const toast = useToast();
  const [board, setBoard] = useState<PipelineBoardData | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setBoard(await api<PipelineBoardData>(`${base}/pipeline/board`)); }
    catch (err) { setError(messageFrom(err)); }
  }, [base]);
  useEffect(() => { load(); }, [load]);

  const [managing, setManaging] = useState(false);
  const [dragCardId, setDragCardId] = useState<string | null>(null);
  const [dragOverStage, setDragOverStage] = useState<string | null>(null);

  const cardsByStage = useMemo(() => {
    const map = new Map<string, PipelineCard[]>();
    for (const card of board?.cards ?? []) {
      const key = card.pipeline_stage_id ?? UNASSIGNED;
      map.set(key, [...(map.get(key) ?? []), card]);
    }
    return map;
  }, [board]);

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

  const columns: { id: string | null; name: string; color: string; count: number; total: number }[] = [
    { id: null, name: t("pipeline.unassignedColumn"), color: "#8996a3", count: board.unassigned_count, total: 0 },
    ...board.stages.map((s) => ({ id: s.id, name: s.name, color: s.color, count: s.conversation_count, total: s.deal_value_total })),
  ];

  return <div className="pipeline-view">
    <section className="form-section">
      <div className="section-copy"><h2>{t("pipeline.title")}</h2><p>{t("pipeline.description")}</p></div>
      {board.stages.length === 0 && !canManage
        ? <EmptyState icon={<GripVertical />} title={t("pipeline.stagesEmptyTitle")} description={t("pipeline.stagesEmptyDescription")} />
        : canManage && <div className="form-fields">
          <button type="button" className="button secondary align-start" onClick={() => setManaging(true)}><Wand2 size={15} /> {t("pipeline.automate")}</button>
        </div>}
    </section>

    {board.stages.length > 0 && <div className="pipeline-board">
      {columns.map((column) => {
        const cards = cardsByStage.get(column.id ?? UNASSIGNED) ?? [];
        const isOver = dragOverStage === (column.id ?? UNASSIGNED);
        return <div key={column.id ?? UNASSIGNED} className={`pipeline-column${isOver ? " pipeline-column-over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOverStage(column.id ?? UNASSIGNED); }}
          onDragLeave={() => setDragOverStage((current) => (current === (column.id ?? UNASSIGNED) ? null : current))}
          onDrop={(e) => {
            e.preventDefault(); setDragOverStage(null);
            const card = board.cards.find((c) => c.id === dragCardId);
            if (card) moveCard(card, column.id);
            setDragCardId(null);
          }}>
          <header className="pipeline-column-head">
            <span className="pipeline-dot" style={{ background: column.color }} />
            <strong>{column.name}</strong>
            <span className="pipeline-column-count">{column.count}</span>
          </header>
          {column.id && column.total > 0 && <div className="pipeline-column-total">{t("pipeline.totalLabel")}: {money(column.total)}</div>}
          <div className="pipeline-cards">
            {cards.length === 0 ? <div className="pipeline-empty-column">{t("pipeline.emptyColumn")}</div>
              : cards.map((card) => <PipelineCardView key={card.id} card={card} t={t}
                onDragStart={() => setDragCardId(card.id)} onValueChange={(value) => editValue(card, value)} />)}
          </div>
        </div>;
      })}
    </div>}

    <AutomationModal open={managing} base={base} stages={board.stages} onClose={() => setManaging(false)}
      onChange={(stages) => setBoard((current) => current && { ...current, stages })} />
  </div>;
}

function PipelineCardView({ card, t, onDragStart, onValueChange }: {
  card: PipelineCard; t: ReturnType<typeof useT>; onDragStart: () => void; onValueChange: (value: number | null) => void;
}) {
  const [editingValue, setEditingValue] = useState(false);
  const [draft, setDraft] = useState(card.deal_value != null ? String(card.deal_value) : "");
  function commit() {
    setEditingValue(false);
    const trimmed = draft.trim();
    onValueChange(trimmed === "" ? null : Number(trimmed));
  }
  return <article className="pipeline-card" draggable onDragStart={onDragStart}>
    <div className="pipeline-card-head">
      <ChannelIcon channel={card.channel} size={13} />
      <strong>{card.contact_name || card.title}</strong>
      {card.mode === "human" && <span className="mini-badge human">{t("portal.inbox.list.humanSupport")}</span>}
    </div>
    {card.account_label && <small className="pipeline-card-account">{card.account_label}</small>}
    {card.preview && <p className="pipeline-card-preview">{card.preview}</p>}
    {editingValue
      ? <input className="pipeline-value-input" type="number" min={0} step="0.01" autoFocus value={draft}
          onChange={(e) => setDraft(e.target.value)} onBlur={commit}
          onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") { setDraft(card.deal_value != null ? String(card.deal_value) : ""); setEditingValue(false); } }} />
      : <button type="button" className="pipeline-value" onClick={() => setEditingValue(true)} title={t("pipeline.editValue")}>
          {card.deal_value != null ? money(card.deal_value) : <span className="pipeline-value-empty">{t("pipeline.noValue")}</span>}
        </button>}
  </article>;
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
