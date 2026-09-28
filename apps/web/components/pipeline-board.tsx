"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ExternalLink,
  GripVertical,
  LayoutGrid,
  List,
  LoaderCircle,
  Plus,
  Search,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import { Alert, EmptyState, Modal } from "@/components/ui";
import { ListRowsSkeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { channelLabel } from "@/lib/channels";
import { formatWhen } from "@/lib/datetime";
import { formatMoney } from "@/lib/currencies";
import { api, messageFrom } from "@/lib/api";
import { useLanguage, useT, type Lang } from "@/lib/i18n";
import type { PipelineBoard as PipelineBoardData, PipelineCard, PipelineStage } from "@/types";
import { fold } from "@/lib/text";

const UNASSIGNED = "__unassigned__";
const PALETTE = ["#94a3b8", "#3b82f6", "#00876c", "#8b5cf6", "#f59e0b", "#c83b82", "#0891b2", "#65a30d"];

const AVATAR_PASTELS = [
  { bg: "#e6f7f3", text: "#00876c" }, // Teal/Emerald
  { bg: "#fef3c7", text: "#d97706" }, // Amber
  { bg: "#dbeafe", text: "#2563eb" }, // Blue
  { bg: "#f3e8ff", text: "#7c3aed" }, // Purple
  { bg: "#ffedd5", text: "#ea580c" }, // Orange
  { bg: "#fce7f3", text: "#db2777" }, // Pink
  { bg: "#e2e8f0", text: "#334155" }, // Slate
];

function getAvatarPastel(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  const index = Math.abs(hash) % AVATAR_PASTELS.length;
  return AVATAR_PASTELS[index];
}

function money(value: number | null | undefined, currency: string, locale?: string): string {
  if (value === null || value === undefined) return "";
  return formatMoney(value, currency, locale);
}

export function PipelineBoard({
  base,
  canManage,
  clientName,
}: {
  base: string;
  canManage: boolean;
  clientName?: string;
}) {
  const t = useT();
  const { lang } = useLanguage();
  const toast = useToast();
  const [board, setBoard] = useState<PipelineBoardData | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [viewMode, setViewMode] = useState<"kanban" | "list">("kanban");
  const [quickLeadStage, setQuickLeadStage] = useState<PipelineStage | null>(null);
  const [newDealOpen, setNewDealOpen] = useState(false);
  const [managing, setManaging] = useState(false);
  const [dragCardId, setDragCardId] = useState<string | null>(null);
  const [dragOverStage, setDragOverStage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setBoard(await api<PipelineBoardData>(`${base}/pipeline/board`));
    } catch (err) {
      setError(messageFrom(err));
    }
  }, [base]);

  const canvasRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;

    // 1. Shift + Wheel horizontal scroll
    const handleWheel = (e: WheelEvent) => {
      if (e.shiftKey) {
        e.preventDefault();
        el.scrollLeft += e.deltaY !== 0 ? e.deltaY : e.deltaX;
      }
    };

    // 2. Drag-to-scroll (pan) on canvas background or stage headers
    let isDown = false;
    let startX = 0;
    let scrollLeftStart = 0;

    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      const target = e.target as HTMLElement | null;
      if (!target) return;

      // Exclude lead cards and interactive elements
      if (
        target.closest(".pipeline-deal-card") ||
        target.closest("button") ||
        target.closest("a") ||
        target.closest("input") ||
        target.closest("select") ||
        target.closest("textarea")
      ) {
        return;
      }

      isDown = true;
      startX = e.clientX;
      scrollLeftStart = el.scrollLeft;
      el.classList.add("is-grabbing");
      document.body.style.userSelect = "none";
      e.preventDefault();
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!isDown) return;
      e.preventDefault();
      const deltaX = e.clientX - startX;
      el.scrollLeft = scrollLeftStart - deltaX;
    };

    const onMouseUp = () => {
      if (!isDown) return;
      isDown = false;
      el.classList.remove("is-grabbing");
      document.body.style.userSelect = "";
    };

    el.addEventListener("wheel", handleWheel, { passive: false });
    el.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);

    return () => {
      el.removeEventListener("wheel", handleWheel);
      el.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      el.classList.remove("is-grabbing");
      document.body.style.userSelect = "";
    };
  }, [Boolean(board), viewMode]);

  const matches = useCallback(
    (card: PipelineCard) => {
      const needle = fold(query.trim());
      if (!needle) return true;
      return [
        card.contact_name,
        card.title,
        card.preview,
        card.channel,
        card.account_label,
        ...card.tags.map((tg) => tg.name),
      ].some((field) => field && fold(field).includes(needle));
    },
    [query]
  );

  const cardsByStage = useMemo(() => {
    const map = new Map<string, PipelineCard[]>();
    for (const card of board?.cards ?? []) {
      if (!matches(card)) continue;
      const key = card.pipeline_stage_id ?? UNASSIGNED;
      map.set(key, [...(map.get(key) ?? []), card]);
    }
    return map;
  }, [board, matches]);

  const filteredCards = useMemo(() => {
    return (board?.cards ?? []).filter(matches);
  }, [board, matches]);

  const threadUrl = useCallback(
    (id: string, number?: number) =>
      base.startsWith("/portal")
        ? number
          ? `${base}/inbox/${number}`
          : `${base}?conversation=${id}`
        : `/inbox?conversation=${id}`,
    [base]
  );

  const pipelinePath = useCallback(
    (id: string) =>
      base.startsWith("/portal")
        ? `${base}/conversations/${id}/pipeline`
        : `/conversations/${id}/pipeline`,
    [base]
  );

  const currency = board?.currency || "CLP";

  async function moveCard(card: PipelineCard, stageId: string | null) {
    if (card.pipeline_stage_id === stageId) return;
    setBoard((current) =>
      current && {
        ...current,
        cards: current.cards.map((c) =>
          c.id === card.id ? { ...c, pipeline_stage_id: stageId } : c
        ),
      }
    );
    try {
      await api(pipelinePath(card.id), {
        method: "PATCH",
        body: JSON.stringify({ pipeline_stage_id: stageId, deal_value: card.deal_value }),
      });
    } catch {
      toast.error(t("pipeline.moveFailed"));
      load();
    }
  }

  async function editValue(card: PipelineCard, value: number | null) {
    setBoard((current) =>
      current && {
        ...current,
        cards: current.cards.map((c) => (c.id === card.id ? { ...c, deal_value: value } : c)),
      }
    );
    try {
      await api(pipelinePath(card.id), {
        method: "PATCH",
        body: JSON.stringify({ pipeline_stage_id: card.pipeline_stage_id, deal_value: value }),
      });
    } catch (err) {
      toast.error(messageFrom(err));
      load();
    }
  }

  if (error) return <Alert>{error}</Alert>;
  if (!board) return <ListRowsSkeleton rows={3} />;

  const columns: {
    id: string | null;
    name: string;
    color: string;
    count: number;
    total: number | null;
  }[] = [
    {
      id: null,
      name: t("pipeline.unassignedColumn"),
      color: "#94a3b8",
      count: board.unassigned_count,
      total: null,
    },
    ...board.stages.map((s, idx) => ({
      id: s.id,
      name: s.name,
      color: s.color || PALETTE[idx % PALETTE.length],
      count: s.conversation_count,
      total: s.deal_value_total,
    })),
  ];

  const totalValue = board.cards.reduce((sum, card) => sum + (card.deal_value ?? 0), 0);

  return (
    <div className="pipeline-view">
      {/* Top Bar Header */}
      <div className="pipeline-topbar">
        <div className="pipeline-topbar-left">
          {/* Client / Brand Name */}
          <span className="pipeline-brand-name">{clientName || "Dental Marbella"}</span>

          {/* View Switcher */}
          <div className="pipeline-view-switcher">
            <button
              type="button"
              className={`pipeline-view-btn ${viewMode === "kanban" ? "active" : ""}`}
              onClick={() => setViewMode("kanban")}
              title="Tablero"
            >
              <LayoutGrid size={16} />
            </button>
            <button
              type="button"
              className={`pipeline-view-btn ${viewMode === "list" ? "active" : ""}`}
              onClick={() => setViewMode("list")}
              title="Lista"
            >
              <List size={16} />
            </button>
          </div>
        </div>

        <div className="pipeline-topbar-right">
          {/* Search Bar with Metrics */}
          <div className="pipeline-search-bar">
            <Search size={16} className="pipeline-search-icon" />
            <input
              type="text"
              className="pipeline-search-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Buscar negocios, contactos o servicios..."
              aria-label="Buscar negocios, contactos o servicios..."
            />
            <div className="pipeline-search-divider" />
            <div className="pipeline-search-metrics">
              <span className="pipeline-metric-label">Total leads:</span>
              <strong className="pipeline-metric-lead-count">{board.cards.length}</strong>
              <span className="pipeline-metric-dot">•</span>
              <span className="pipeline-metric-label">Total:</span>
              <strong className="pipeline-metric-total-sum">{money(totalValue, currency, lang)}</strong>
            </div>
          </div>

          {/* Action buttons */}
          {canManage && (
            <button
              type="button"
              className="pipeline-btn-automatiza"
              onClick={() => setManaging(true)}
            >
              <Wand2 size={15} />
              <span>Automatiza</span>
            </button>
          )}

          {canManage && (
            <button
              type="button"
              className="pipeline-btn-new-deal"
              onClick={() => setNewDealOpen(true)}
            >
              <Plus size={16} />
              <span>Nuevo lead</span>
            </button>
          )}
        </div>
      </div>

      {board.stages.length === 0 && !canManage ? (
        <section className="form-section p-6">
          <div className="section-copy">
            <h2>{t("pipeline.title")}</h2>
            <p>{t("pipeline.description")}</p>
          </div>
          <EmptyState
            icon={<GripVertical />}
            title={t("pipeline.stagesEmptyTitle")}
            description={t("pipeline.stagesEmptyDescription")}
          />
        </section>
      ) : viewMode === "kanban" ? (
        /* KANBAN CANVAS */
        <main ref={canvasRef} className="pipeline-canvas">
          <div className="pipeline-columns-row">
            {columns.map((column) => {
              const cards = cardsByStage.get(column.id ?? UNASSIGNED) ?? [];
              const isOver = dragOverStage === (column.id ?? UNASSIGNED);
              const stage = column.id ? board.stages.find((s) => s.id === column.id) ?? null : null;

              return (
                <div
                  key={column.id ?? UNASSIGNED}
                  className={`pipeline-col-card ${isOver ? "drag-over" : ""}`}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragOverStage(column.id ?? UNASSIGNED);
                  }}
                  onDragLeave={() =>
                    setDragOverStage((cur) =>
                      cur === (column.id ?? UNASSIGNED) ? null : cur
                    )
                  }
                  onDrop={(e) => {
                    e.preventDefault();
                    setDragOverStage(null);
                    const card = board.cards.find((c) => c.id === dragCardId);
                    if (card) moveCard(card, column.id);
                    setDragCardId(null);
                  }}
                >
                  {/* Top Colored Accent Bar */}
                  <div
                    className="pipeline-col-top-bar"
                    style={{ backgroundColor: column.color }}
                  />

                  {/* Stage Title */}
                  <h3 className="pipeline-col-name">{column.name}</h3>

                  {/* Stage Total Money */}
                  <span className="pipeline-col-sum">
                    {column.total != null && column.total > 0
                      ? money(column.total, currency, lang)
                      : `$0 ${currency}`}
                  </span>

                  {/* Cards Stack */}
                  <div className="pipeline-col-cards-list">
                    {cards.map((card) => (
                      <PipelineCardView
                        key={card.id}
                        card={card}
                        t={t}
                        lang={lang}
                        currency={currency}
                        threadUrl={threadUrl(card.id, card.number)}
                        onDragStart={() => setDragCardId(card.id)}
                        onValueChange={(val) => editValue(card, val)}
                      />
                    ))}

                    {cards.length === 0 && (
                      <div className="pipeline-col-drop-empty">
                        Suelta una tarjeta aquí
                      </div>
                    )}

                    {isOver && cards.length > 0 && (
                      <div className="pipeline-col-drop-empty">
                        Suelta una tarjeta aquí
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </main>
      ) : (
        /* LIST VIEW */
        <main className="pipeline-list-canvas">
          <div className="pipeline-list-card">
            <table className="pipeline-list-table">
              <thead>
                <tr>
                  <th>{t("pipeline.contact")}</th>
                  <th>{t("pipeline.stage")}</th>
                  <th>{t("pipeline.channel")}</th>
                  <th>{t("pipeline.value")}</th>
                  <th>{t("pipeline.mode")}</th>
                  <th>{t("pipeline.updated")}</th>
                  <th style={{ textAlign: "right" }}>{t("pipeline.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {filteredCards.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="pipeline-list-empty">
                      Suelta una tarjeta aquí
                    </td>
                  </tr>
                ) : (
                  filteredCards.map((card) => {
                    const name = card.contact_name || card.title;
                    const stage = board.stages.find((s) => s.id === card.pipeline_stage_id);
                    const avatarColor = getAvatarPastel(name);

                    return (
                      <tr key={card.id}>
                        <td>
                          <div className="pipeline-list-contact">
                            <div
                              className="pipeline-card-avatar"
                              style={{
                                backgroundColor: avatarColor.bg,
                                color: avatarColor.text,
                              }}
                            >
                              {name.slice(0, 1).toUpperCase()}
                            </div>
                            <div className="pipeline-list-name-col">
                              <a
                                href={threadUrl(card.id, card.number)}
                                target="_blank"
                                rel="noreferrer"
                                className="pipeline-list-name"
                              >
                                {name}
                              </a>
                              {card.preview && (
                                <div className="pipeline-list-preview">
                                  {card.preview}
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                        <td>
                          <span
                            className="pipeline-list-stage-pill"
                            style={{
                              backgroundColor: stage ? `${stage.color}15` : "#f1f5f9",
                              color: stage ? stage.color : "#64748b",
                              borderColor: stage ? `${stage.color}40` : "#e2e8f0",
                            }}
                          >
                            {stage?.name || t("pipeline.unassignedColumn")}
                          </span>
                        </td>
                        <td style={{ fontWeight: 500 }}>
                          {channelLabel(card.channel, t)}
                        </td>
                        <td style={{ fontWeight: 700 }}>
                          {card.deal_value != null
                            ? money(card.deal_value, currency, lang)
                            : "Sin valor definido"}
                        </td>
                        <td>
                          {card.mode === "human" ? (
                            <span className="pipeline-card-human-pill" style={{ display: "inline-flex", width: "auto" }}>
                              <span className="pipeline-card-human-dot" />
                              <span>Atención humana requerida</span>
                            </span>
                          ) : (
                            <span className="pipeline-pill-mode-ai">
                              IA Respondiendo
                            </span>
                          )}
                        </td>
                        <td style={{ color: "#94a3b8", fontSize: "11px" }}>
                          {formatWhen(card.updated_at, lang)}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <a
                            href={threadUrl(card.id, card.number)}
                            target="_blank"
                            rel="noreferrer"
                            className="pipeline-list-action-btn"
                            title={t("pipeline.openThread")}
                          >
                            <ExternalLink size={13} />
                          </a>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </main>
      )}

      {/* Quick Lead Modal */}
      {(quickLeadStage || newDealOpen) && (
        <QuickLeadModal
          base={base}
          initialStage={quickLeadStage}
          stages={board.stages}
          onClose={() => {
            setQuickLeadStage(null);
            setNewDealOpen(false);
          }}
          onCreated={() => load()}
        />
      )}

      {/* Automate Modal */}
      <AutomationModal
        open={managing}
        base={base}
        stages={board.stages}
        onClose={() => setManaging(false)}
        onChange={(stages) => setBoard((current) => current && { ...current, stages })}
      />
    </div>
  );
}

function PipelineCardView({
  card,
  t,
  lang,
  currency,
  threadUrl,
  onDragStart,
  onValueChange,
}: {
  card: PipelineCard;
  t: ReturnType<typeof useT>;
  lang: Lang;
  currency: string;
  threadUrl: string;
  onDragStart: () => void;
  onValueChange: (value: number | null) => void;
}) {
  const [editingValue, setEditingValue] = useState(false);
  const [draft, setDraft] = useState(
    card.deal_value != null ? String(card.deal_value) : ""
  );
  const name = card.contact_name || card.title;
  const avatarColor = getAvatarPastel(name);

  function commit() {
    setEditingValue(false);
    const trimmed = draft.trim();
    onValueChange(trimmed === "" ? null : Number(trimmed));
  }

  const channelPillLabel = () => {
    if (card.channel === "whatsapp_cloud" || card.channel === "whatsapp") return "WhatsApp Web";
    if (card.channel === "instagram") return "Instagram Direct";
    if (card.channel === "messenger") return "Messenger";
    return channelLabel(card.channel, t);
  };

  return (
    <article
      className="pipeline-deal-card"
      draggable
      onDragStart={onDragStart}
    >
      <div className="pipeline-card-header-row">
        <div
          className="pipeline-card-avatar"
          style={{ backgroundColor: avatarColor.bg, color: avatarColor.text }}
        >
          {name.slice(0, 1).toUpperCase()}
        </div>
        <a
          href={threadUrl}
          target="_blank"
          rel="noreferrer"
          className="pipeline-card-name-title"
          title={t("pipeline.openThread")}
        >
          {name}
        </a>
        <span className="pipeline-card-date">{formatWhen(card.updated_at, lang)}</span>
      </div>

      {/* Human attention status pill if human */}
      {card.mode === "human" && (
        <div className="pipeline-card-human-pill">
          <span className="pipeline-card-human-dot" />
          <span>Atención humana requerida</span>
        </div>
      )}

      {/* Badges / Value Row */}
      <div className="pipeline-card-meta-row">
        <div className="pipeline-card-meta-left">
          {card.mode !== "human" && (
            <span
              className={`pipeline-pill-channel ${
                card.channel === "instagram" ? "instagram" : "whatsapp"
              }`}
            >
              {channelPillLabel()}
            </span>
          )}
          {card.mode === "ai" && card.tags.length === 0 && (
            <span className="pipeline-pill-mode-ai">
              IA Respondiendo
            </span>
          )}
        </div>

        <div className="pipeline-card-meta-right">
          {editingValue ? (
            <input
              className="pipeline-input-value-inline"
              type="number"
              min={0}
              step={0.01}
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit();
                if (e.key === "Escape") {
                  setDraft(card.deal_value != null ? String(card.deal_value) : "");
                  setEditingValue(false);
                }
              }}
            />
          ) : (
            <button
              type="button"
              className="pipeline-btn-edit-val"
              onClick={() => setEditingValue(true)}
              title={t("pipeline.editValue")}
            >
              {card.deal_value != null ? (
                <span className="pipeline-val-bold">
                  {money(card.deal_value, currency, lang)}
                </span>
              ) : (
                <span className="pipeline-val-undefined">Sin valor definido</span>
              )}
            </button>
          )}
        </div>
      </div>

      {/* Tag Chips */}
      {card.tags.length > 0 && (
        <div className="pipeline-card-tags-row">
          {card.tags.map((tag) => (
            <span key={tag.name} className="pipeline-tag-chip-outline">
              {tag.name}
            </span>
          ))}
        </div>
      )}

      {/* Human attention assignee row */}
      {card.mode === "human" && (
        <div className="pipeline-card-assign-row">
          <div className="pipeline-assignee-badge">
            <span className="pipeline-assignee-initials">NV</span>
            <span>Asignar</span>
          </div>
        </div>
      )}
    </article>
  );
}

function QuickLeadModal({
  base,
  initialStage,
  stages,
  onClose,
  onCreated,
}: {
  base: string;
  initialStage: PipelineStage | null;
  stages: PipelineStage[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const t = useT();
  const toast = useToast();
  const [selectedStageId, setSelectedStageId] = useState(
    initialStage?.id || stages[0]?.id || ""
  );
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  async function create(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy || !selectedStageId) return;
    setBusy(true);
    try {
      const payload: Record<string, string | number> = {
        contact_name: trimmed,
        pipeline_stage_id: selectedStageId,
      };
      if (phone.trim()) payload.contact_phone = phone.trim();
      if (value.trim()) payload.deal_value = Number(value);
      await api(`${base}/pipeline/leads`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      toast.success(t("pipeline.quickLeadCreated"));
      onCreated();
      onClose();
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  const currentStage = stages.find((s) => s.id === selectedStageId);

  return (
    <Modal
      open
      title={
        initialStage
          ? t("pipeline.quickLeadTitle", { stage: currentStage?.name || "" })
          : "Nuevo lead"
      }
      onClose={onClose}
    >
      <form className="modal-form" onSubmit={create}>
        <div className="wa-cloud-form">
          {stages.length > 1 && (
            <label>
              {t("pipeline.stage")}
              <select
                value={selectedStageId}
                onChange={(e) => setSelectedStageId(e.target.value)}
                disabled={busy}
              >
                {stages.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            {t("pipeline.quickLeadName")}
            <input
              value={name}
              maxLength={180}
              onChange={(e) => setName(e.target.value)}
              disabled={busy}
              autoFocus
              placeholder="Ej: Camila Flores"
            />
          </label>
          <label>
            {t("pipeline.quickLeadPhone")}
            <input
              value={phone}
              maxLength={40}
              onChange={(e) => setPhone(e.target.value)}
              disabled={busy}
              autoComplete="off"
              placeholder="+56 9 1234 5678"
            />
          </label>
          <label>
            {t("pipeline.quickLeadValue")}
            <input
              type="number"
              min={0}
              step={0.01}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              disabled={busy}
              placeholder="0"
            />
          </label>
        </div>
        <div className="modal-actions">
          <button type="button" className="button" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            type="submit"
            className="button primary"
            disabled={busy || !name.trim()}
          >
            {busy ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Plus size={15} />
            )}
            {t("pipeline.quickLeadCreate")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function AutomationModal({
  open,
  base,
  stages,
  onClose,
  onChange,
}: {
  open: boolean;
  base: string;
  stages: PipelineStage[];
  onClose: () => void;
  onChange: (stages: PipelineStage[]) => void;
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
      const updated = await api<PipelineStage>(
        `${base}/pipeline/stages/${stage.id}`,
        { method: "PATCH", body: JSON.stringify({ name: trimmed }) }
      );
      onChange(stages.map((s) => (s.id === updated.id ? { ...s, ...updated } : s)));
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusyId(null);
    }
  }

  async function recolor(stage: PipelineStage, color: string) {
    onChange(stages.map((s) => (s.id === stage.id ? { ...s, color } : s)));
    try {
      await api<PipelineStage>(`${base}/pipeline/stages/${stage.id}`, {
        method: "PATCH",
        body: JSON.stringify({ color }),
      });
    } catch (err) {
      toast.error(messageFrom(err));
    }
  }

  async function move(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= stages.length) return;
    const reordered = [...stages];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    onChange(reordered);
    try {
      const saved = await api<PipelineStage[]>(
        `${base}/pipeline/stages/reorder`,
        { method: "POST", body: JSON.stringify({ stage_ids: reordered.map((s) => s.id) }) }
      );
      onChange(saved);
    } catch (err) {
      toast.error(messageFrom(err));
    }
  }

  async function remove(stage: PipelineStage) {
    setBusyId(stage.id);
    try {
      await api(`${base}/pipeline/stages/${stage.id}`, { method: "DELETE" });
      onChange(stages.filter((s) => s.id !== stage.id));
      setConfirmingDelete(null);
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusyId(null);
    }
  }

  async function addStage(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = newName.trim();
    if (!trimmed) return;
    setAddBusy(true);
    try {
      const color = PALETTE[stages.length % PALETTE.length];
      const created = await api<PipelineStage>(`${base}/pipeline/stages`, {
        method: "POST",
        body: JSON.stringify({ name: trimmed, color }),
      });
      onChange([...stages, created]);
      setNewName("");
      toast.success(t("pipeline.stageAdded"));
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setAddBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Automatiza"
      description={t("pipeline.automateCopy")}
      onClose={onClose}
      wide
    >
      <div className="modal-form">
        <div className="pipeline-automation-list">
          {stages.length === 0 && (
            <p className="field-help">{t("pipeline.stagesEmptyDescription")}</p>
          )}
          {stages.map((stage, index) => (
            <div key={stage.id} className="pipeline-automation-row">
              <div className="pipeline-automation-order">
                <button
                  type="button"
                  className="icon-button small"
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                  aria-label={t("pipeline.moveUp")}
                  title={t("pipeline.moveUp")}
                >
                  <ArrowUp size={13} />
                </button>
                <button
                  type="button"
                  className="icon-button small"
                  disabled={index === stages.length - 1}
                  onClick={() => move(index, 1)}
                  aria-label={t("pipeline.moveDown")}
                  title={t("pipeline.moveDown")}
                >
                  <ArrowDown size={13} />
                </button>
              </div>
              <input
                type="color"
                className="pipeline-automation-color"
                value={stage.color}
                onChange={(e) => recolor(stage, e.target.value)}
                title={t("pipeline.stageColor")}
              />
              <input
                className="pipeline-automation-name"
                defaultValue={stage.name}
                maxLength={80}
                onBlur={(e) => rename(stage, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                }}
              />
              {busyId === stage.id && <LoaderCircle size={14} className="spin" />}
              {confirmingDelete === stage.id ? (
                <span className="pipeline-automation-confirm">
                  <button
                    type="button"
                    className="button danger small"
                    onClick={() => remove(stage)}
                  >
                    {t("pipeline.deleteStageConfirm")}
                  </button>
                  <button
                    type="button"
                    className="icon-button small"
                    onClick={() => setConfirmingDelete(null)}
                    aria-label={t("common.cancel")}
                  >
                    <X size={13} />
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  className="icon-button small danger-icon"
                  onClick={() => setConfirmingDelete(stage.id)}
                  aria-label={t("pipeline.deleteStage")}
                  title={t("pipeline.deleteStage")}
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          ))}
        </div>
        <form className="pipeline-automation-add" onSubmit={addStage}>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={t("pipeline.stageNamePlaceholder")}
            maxLength={80}
          />
          <button
            type="submit"
            className="button secondary"
            disabled={addBusy || !newName.trim()}
          >
            {addBusy ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Plus size={15} />
            )}
            {t("pipeline.addStage")}
          </button>
        </form>
        <div className="modal-actions">
          <button type="button" className="button primary" onClick={onClose}>
            {t("pipeline.done")}
          </button>
        </div>
      </div>
    </Modal>
  );
}
