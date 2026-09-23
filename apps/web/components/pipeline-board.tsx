"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { GripVertical, LoaderCircle, Pencil, Plus, Trash2 } from "lucide-react";
import { Alert, EmptyState, Modal } from "@/components/ui";
import { ListRowsSkeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { ChannelIcon } from "@/lib/channels";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { PipelineBoard as PipelineBoardData, PipelineCard, PipelineStage } from "@/types";

const UNASSIGNED = "__unassigned__";

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "";
  return value.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

/** The sales pipeline: a client's own stages as kanban columns, with every
 * open conversation as a draggable card — including those not yet in any
 * stage, which sit in a virtual first column so dragging one in needs no
 * separate picker anywhere else. Shared by the client page and the portal. */
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

  const [editing, setEditing] = useState<PipelineStage | "new" | null>(null);
  const [deleting, setDeleting] = useState<PipelineStage | null>(null);
  const [busy, setBusy] = useState(false);
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

  async function saveStage(name: string, color: string) {
    setBusy(true);
    try {
      if (editing && editing !== "new") {
        const updated = await api<PipelineStage>(`${base}/pipeline/stages/${editing.id}`, { method: "PATCH", body: JSON.stringify({ name, color }) });
        setBoard((current) => current && { ...current, stages: current.stages.map((s) => (s.id === updated.id ? updated : s)) });
        toast.success(t("pipeline.stageSaved"));
      } else {
        const created = await api<PipelineStage>(`${base}/pipeline/stages`, { method: "POST", body: JSON.stringify({ name, color }) });
        setBoard((current) => current && { ...current, stages: [...current.stages, created] });
        toast.success(t("pipeline.stageAdded"));
      }
      setEditing(null);
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }

  async function removeStage() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api(`${base}/pipeline/stages/${deleting.id}`, { method: "DELETE" });
      setBoard((current) => current && {
        ...current,
        stages: current.stages.filter((s) => s.id !== deleting.id),
        cards: current.cards.map((c) => (c.pipeline_stage_id === deleting.id ? { ...c, pipeline_stage_id: null } : c)),
        unassigned_count: current.unassigned_count + (cardsByStage.get(deleting.id)?.length ?? 0),
      });
      setDeleting(null);
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }

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
        : <div className="form-fields">
          {canManage && <button type="button" className="button secondary align-start" onClick={() => setEditing("new")}><Plus size={15} /> {t("pipeline.addStage")}</button>}
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
            {column.id && canManage && <span className="pipeline-column-actions">
              <button type="button" className="icon-button small" onClick={() => setEditing(board.stages.find((s) => s.id === column.id) || null)} aria-label={t("common.edit")} title={t("common.edit")}><Pencil size={13} /></button>
              <button type="button" className="icon-button small danger-icon" onClick={() => setDeleting(board.stages.find((s) => s.id === column.id) || null)} aria-label={t("pipeline.deleteStage")} title={t("pipeline.deleteStage")}><Trash2 size={13} /></button>
            </span>}
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

    <Modal open={editing !== null} title={editing === "new" ? t("pipeline.addStageTitle") : t("pipeline.editStageTitle")} onClose={() => setEditing(null)}>
      <StageForm stage={editing !== "new" ? editing : null} busy={busy} isNew={editing === "new"} onCancel={() => setEditing(null)} onSave={saveStage} />
    </Modal>

    <Modal open={deleting !== null} title={t("pipeline.deleteStageConfirmTitle", { name: deleting?.name || "" })} onClose={() => setDeleting(null)}>
      <div className="modal-form"><p className="modal-copy">{t("pipeline.deleteStageConfirmCopy")}</p>
        <div className="modal-actions"><button type="button" className="button" onClick={() => setDeleting(null)}>{t("common.cancel")}</button>
          <button type="button" className="button danger" disabled={busy} onClick={removeStage}>{busy ? <LoaderCircle className="spin" size={16} /> : <><Trash2 size={15} /> {t("pipeline.deleteStageConfirm")}</>}</button></div>
      </div>
    </Modal>
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

function StageForm({ stage, isNew, busy, onCancel, onSave }: {
  stage: PipelineStage | null; isNew: boolean; busy: boolean; onCancel: () => void; onSave: (name: string, color: string) => void;
}) {
  const t = useT();
  const [name, setName] = useState(stage?.name || "");
  const [color, setColor] = useState(stage?.color || "#2f6df0");
  return <form className="modal-form" onSubmit={(e) => { e.preventDefault(); if (name.trim()) onSave(name.trim(), color); }}>
    <label>{t("pipeline.stageName")}<input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("pipeline.stageNamePlaceholder")} required autoFocus maxLength={80} /></label>
    <label>{t("pipeline.stageColor")}<div className="color-input"><input type="color" value={color} onChange={(e) => setColor(e.target.value)} /><input value={color} readOnly /></div></label>
    <div className="modal-actions"><button type="button" className="button" onClick={onCancel}>{t("common.cancel")}</button>
      <button className="button primary" disabled={busy || !name.trim()}>{busy ? <LoaderCircle className="spin" size={16} /> : isNew ? t("pipeline.add") : t("pipeline.save")}</button></div>
  </form>;
}
