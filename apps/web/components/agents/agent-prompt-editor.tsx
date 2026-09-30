"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { AlertCircle, Brackets, Check, ChevronRight, FileText, Film, ImageIcon, Layers, Link2, Music, Undo2, Wrench } from "lucide-react";
import { Alert, Modal } from "@/components/ui";
import { useLanguage, type I18nKey, type TranslateFn } from "@/lib/i18n";
import {
  atomicSpans,
  expandToWhole,
  fold,
  nearestEdge,
  parseVariables,
  promptVariables,
  segments,
  spanInside,
  type PromptVariable,
} from "@/lib/agent-variables";
import type { ClientResource, PipelineStage } from "@/types";

export type PromptItem = {
  key: string;
  token: string;
  label: string;
  category: "tools" | "blocks" | "stages" | "resources" | "control";
  description: string;
  /** Set on the item that opens a chooser instead of inserting a bare marker. */
  picker?: string;
};

interface AgentPromptEditorProps {
  defaultValue?: string;
  placeholder?: string;
  pipelineStages: PipelineStage[];
  clientTimezone?: string;
  /** The client's library; undefined while loading or where it cannot be read. */
  resources?: ClientResource[];
  /** Builds a file's preview address for the picker; omitted, files show an icon. */
  resourceFileUrl?: (resource: ClientResource) => string;
  onChange?: (val: string) => void;
}

const RESOURCE_TOKEN_RE = /\[Recurso:\s*([^\]\n]+?)\s*\]/gi;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Names cited via [Recurso: name], in order. */
function citedResources(text: string): string[] {
  return Array.from(text.matchAll(RESOURCE_TOKEN_RE), (m) => m[1].trim()).filter(Boolean);
}

/**
 * What each variable is for, by the machine name the server gave it. A variable
 * with no entry here still appears in the list, described by its kind, so a
 * server-side addition is never a blank row.
 */
const VAR_DESCRIPTION: Record<string, I18nKey> = {
  check_calendar_availability: "agents.detail.varDesc.check_calendar_availability",
  book_calendar_appointment: "agents.detail.varDesc.book_calendar_appointment",
  reschedule_appointment: "agents.detail.varDesc.reschedule_appointment",
  update_contact_info: "agents.detail.varDesc.update_contact_info",
  move_lead_stage: "agents.detail.varDesc.move_lead_stage",
  add_lead_tag: "agents.detail.varDesc.add_lead_tag",
  add_internal_note: "agents.detail.varDesc.add_internal_note",
  escalate_to_human: "agents.detail.varDesc.escalate_to_human",
  stay_silent: "agents.detail.varDesc.stay_silent",
  enviar_recurso: "agents.detail.varDesc.enviar_recurso",
  temporal: "agents.detail.varDesc.temporal",
  business_info: "agents.detail.varDesc.business_info",
  catalog: "agents.detail.varDesc.catalog",
  contact_card: "agents.detail.varDesc.contact_card",
  appointments: "agents.detail.varDesc.appointments",
  team_notes: "agents.detail.varDesc.team_notes",
};

const CATEGORY_COLOR: Record<PromptItem["category"], { line: string; fill: string; text: string }> = {
  tools: { line: "#10b981", fill: "rgba(16, 185, 129, 0.2)", text: "#10b981" },
  blocks: { line: "#a78bfa", fill: "rgba(139, 92, 246, 0.2)", text: "#a78bfa" },
  stages: { line: "#f59e0b", fill: "rgba(245, 158, 11, 0.2)", text: "#f59e0b" },
  resources: { line: "#38bdf8", fill: "rgba(56, 189, 248, 0.2)", text: "#38bdf8" },
  control: { line: "#94a3b8", fill: "rgba(148, 163, 184, 0.2)", text: "#94a3b8" },
};

function describeVariable(row: PromptVariable, t: TranslateFn): string {
  const byName = VAR_DESCRIPTION[row.value];
  if (byName) return t(byName);
  if (row.kind === "stage") return t("agents.detail.varDesc.stage");
  if (row.kind === "resource") return t("agents.detail.varDesc.resource");
  if (row.kind === "control") return t("agents.detail.varDesc.control");
  return t("agents.detail.varDescUnknown");
}

export function AgentPromptEditor({
  defaultValue = "",
  placeholder = "",
  pipelineStages,
  clientTimezone,
  resources,
  resourceFileUrl,
  onChange,
}: AgentPromptEditorProps) {
  const { t } = useLanguage();
  const [content, setContent] = useState(defaultValue);
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [catalog, setCatalog] = useState<PromptVariable[] | null>(null);
  const [catalogFailed, setCatalogFailed] = useState(false);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const undoStack = useRef<{ value: string; caret: number }[]>([]);
  const [undoDepth, setUndoDepth] = useState(0);

  // The guards below read the latest text without re-subscribing on every
  // keystroke, which is what keeps a listener on the document affordable. An
  // effect, not the render pass: a ref is written once the text it mirrors is
  // on screen, and every reader runs from an event.
  const contentRef = useRef(content);
  useEffect(() => { contentRef.current = content; }, [content]);

  // Sync internal state if defaultValue changes from external loads
  useEffect(() => {
    setContent(defaultValue);
  }, [defaultValue]);

  useEffect(() => {
    let alive = true;
    promptVariables()
      .then((rows) => { if (alive) setCatalog(rows); })
      .catch(() => { if (alive) setCatalogFailed(true); });
    return () => { alive = false; };
  }, []);

  // Catalog of items. Every marker the editor can insert comes from the server's
  // table, so what is offered and what the engine honours cannot drift apart.
  const items: PromptItem[] = useMemo(() => {
    const list: PromptItem[] = [];
    const stageShape = catalog?.find((row) => row.kind === "stage")?.template;
    const resourceShape = catalog?.find((row) => row.kind === "resource")?.template;

    for (const row of catalog ?? []) {
      if (!row.token) continue;
      const category: PromptItem["category"] =
        row.kind === "tool" ? "tools" : row.kind === "block" ? "blocks" : "control";
      list.push({
        key: `${row.kind}-${row.value}`,
        token: row.token,
        label: row.token,
        category,
        description: describeVariable(row, t),
        picker: row.picker ?? undefined,
      });
    }

    // The stages and the library are the client's own, so they arrive with the
    // client rather than in the server's table. Until they do, two usual names
    // keep the list useful; the client always wins over them.
    const stages = pipelineStages && pipelineStages.length > 0
      ? pipelineStages.map((stage) => stage.name)
      : ["Descubrimiento", "Cita Agendada"];
    if (stageShape) {
      for (const [index, name] of stages.entries()) {
        const token = stageShape.replace("{name}", name);
        list.push({
          key: `stage-${pipelineStages?.[index]?.id ?? `fallback-${name}`}`,
          token,
          label: token,
          category: "stages",
          description: t("agents.detail.varDesc.stage"),
        });
      }
    }

    for (const resource of resources ?? []) {
      if (!resource.is_active || !resourceShape) continue;
      const token = resourceShape.replace("{name}", resource.name);
      const kind = resource.kind === "link" ? t("resources.kindLink") : t(`resources.kind.${resource.media_kind ?? "file"}`);
      list.push({
        key: `resource-${resource.id}`,
        token,
        label: token,
        category: "resources",
        description: resource.description || t("resources.picker.itemDescription", { kind }),
      });
    }

    return list;
  }, [catalog, pipelineStages, resources, t]);

  const resourceTool = useMemo(() => items.find((item) => item.picker === "resource"), [items]);
  const resourceToolRe = useMemo(
    () => (resourceTool ? new RegExp(escapeRegExp(resourceTool.token), "i") : null),
    [resourceTool],
  );

  const filteredItems = useMemo(() => {
    if (!searchQuery.trim()) return items;
    const q = fold(searchQuery);
    return items.filter((item) => {
      const matchToken = fold(item.token).includes(q);
      const matchLabel = fold(item.label).includes(q);
      const matchDesc = fold(item.description).includes(q);
      return matchToken || matchLabel || matchDesc;
    });
  }, [items, searchQuery]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [searchQuery, isOpen]);

  // The variables in the text, and the two sets that matter: the ones that
  // behave as a single piece, and the ones this release has no variable for.
  const spans = useMemo(
    () => parseVariables(content, { catalog, stages: pipelineStages, resources }),
    [content, catalog, pipelineStages, resources],
  );
  const atomic = useMemo(() => atomicSpans(spans), [spans]);
  const unknown = useMemo(() => spans.filter((span) => !span.known && !span.draft), [spans]);
  const atomicRef = useRef(atomic);
  useEffect(() => { atomicRef.current = atomic; }, [atomic]);
  const painted = useMemo(() => segments(content, spans), [content, spans]);

  const syncScroll = useCallback(() => {
    const input = textareaRef.current;
    const mirror = mirrorRef.current;
    if (!input || !mirror) return;
    mirror.scrollTop = input.scrollTop;
    mirror.scrollLeft = input.scrollLeft;
  }, []);

  /**
   * Write the whole value at once, remembering what was there. Writing
   * ``element.value`` is the only way to place a marker exactly where the caret
   * is, and it costs the browser's own undo history, so the editor keeps its
   * own instead of losing the insertion for good.
   */
  const applyValue = useCallback((next: string, caret: number) => {
    const el = textareaRef.current;
    if (!el) return;
    undoStack.current.push({ value: contentRef.current, caret: el.selectionStart });
    if (undoStack.current.length > 60) undoStack.current.shift();
    setUndoDepth(undoStack.current.length);
    el.value = next;
    setContent(next);
    onChange?.(next);
    setTimeout(() => {
      el.focus();
      el.setSelectionRange(caret, caret);
      syncScroll();
    }, 0);
  }, [onChange, syncScroll]);

  const restore = useCallback((previous: { value: string; caret: number }) => {
    const el = textareaRef.current;
    if (!el) return;
    el.value = previous.value;
    setContent(previous.value);
    onChange?.(previous.value);
    setTimeout(() => {
      el.focus();
      el.setSelectionRange(previous.caret, previous.caret);
      syncScroll();
    }, 0);
  }, [onChange, syncScroll]);

  const undo = useCallback(() => {
    const previous = undoStack.current.pop();
    if (!previous) return false;
    setUndoDepth(undoStack.current.length);
    restore(previous);
    return true;
  }, [restore]);

  // Insert token at current cursor position
  const insertToken = useCallback((tokenToInsert: string) => {
    const el = textareaRef.current;
    if (!el) return;

    const start = el.selectionStart;
    const end = el.selectionEnd;
    const current = el.value;

    const before = current.slice(0, start);
    const after = current.slice(end);

    let newBefore = before;
    const lastBracket = before.lastIndexOf("[");
    if (lastBracket !== -1) {
      const textBetween = before.slice(lastBracket + 1);
      if (!textBetween.includes("]") && !textBetween.includes("\n")) {
        newBefore = before.slice(0, lastBracket);
      }
    }

    setIsOpen(false);
    setSearchQuery("");
    applyValue(newBefore + tokenToInsert + after, newBefore.length + tokenToInsert.length);
  }, [applyValue]);

  // The resource tool opens the picker instead of inserting a bare token.
  const choose = (item: PromptItem) => {
    if (item.picker) {
      setIsOpen(false);
      setSearchQuery("");
      setPickerOpen(true);
      return;
    }
    insertToken(item.token);
  };

  /** Makes the prompt cite exactly `names`: unchecked [Recurso: ...] lines go away,
   * new ones follow the tool token (inserted at the cursor when missing). */
  const applyPicker = (names: string[]) => {
    const el = textareaRef.current;
    if (!el || !resourceTool || !resourceToolRe) return;
    const wanted = new Set(names.map(fold));
    let next = el.value;
    for (const cited of citedResources(next)) {
      if (wanted.has(fold(cited))) continue;
      const token = `\\[Recurso:\\s*${escapeRegExp(cited)}\\s*\\]`;
      next = next.replace(new RegExp(`^[ \\t]*${token}[ \\t]*\\r?\\n?`, "gim"), "").replace(new RegExp(token, "gi"), "");
    }
    const present = new Set(citedResources(next).map(fold));
    const lines = names.filter((name) => !present.has(fold(name))).map((name) => {
      const shape = catalog?.find((row) => row.kind === "resource")?.template;
      return shape ? shape.replace("{name}", name) : `[Recurso: ${name}]`;
    });
    const toolMatch = resourceToolRe.exec(next);
    if (toolMatch) {
      if (lines.length) {
        // After the last resource already cited below the tool, or right after the tool.
        let at = toolMatch.index + toolMatch[0].length;
        for (const m of next.matchAll(RESOURCE_TOKEN_RE)) if ((m.index ?? 0) > at) at = (m.index ?? 0) + m[0].length;
        next = `${next.slice(0, at)}\n${lines.join("\n")}${next.slice(at)}`;
      }
      const caret = el.selectionStart;
      setPickerOpen(false);
      applyValue(next, caret);
      return;
    }
    setPickerOpen(false);
    if (names.length) insertToken([resourceTool.token, ...lines].join("\n"));
  };

  // Inspect typing for bracket trigger
  const handleTextareaInput = () => {
    const el = textareaRef.current;
    if (!el) return;

    const val = el.value;
    setContent(val);
    onChange?.(val);

    const start = el.selectionStart;
    const before = val.slice(0, start);
    const lastBracket = before.lastIndexOf("[");

    if (lastBracket !== -1) {
      const queryText = before.slice(lastBracket + 1);
      if (!queryText.includes("]") && !queryText.includes("\n") && queryText.length <= 40) {
        setSearchQuery(queryText);
        setIsOpen(true);
        return;
      }
    }

    setIsOpen(false);
    setSearchQuery("");
  };

  /**
   * The caret treats a variable as one character. Where the browser is about to
   * put it inside one, it goes to the far edge instead, so it can never come to
   * rest in the middle and never sticks on an edge it cannot move past.
   */
  const stepOverToken = useCallback((el: HTMLTextAreaElement, direction: 1 | -1): boolean => {
    const tokens = atomicRef.current;
    if (!tokens.length) return false;
    const start = el.selectionStart;
    if (start !== el.selectionEnd) return false;
    const next = start + direction;
    const token = direction > 0
      ? tokens.find((span) => span.start <= next && next < span.end)
      : tokens.find((span) => span.start < next && next <= span.end);
    if (!token) return false;
    const to = direction > 0 ? token.end : token.start;
    el.setSelectionRange(to, to);
    return true;
  }, []);

  /**
   * The last line of defence, and the only one that sees every route an edit can
   * take: a keystroke, a paste, a cut, a drop. A caret that has landed inside a
   * variable is moved to its edge, and an edit that would cut one in half is
   * refused rather than applied.
   */
  const handleBeforeInput = (event: FormEvent<HTMLTextAreaElement>) => {
    const el = event.currentTarget;
    const tokens = atomicRef.current;
    if (!tokens.length) return;
    const target = (event.nativeEvent as InputEvent).getTargetRanges?.()[0];
    const start = target?.startOffset ?? el.selectionStart;
    const end = target?.endOffset ?? el.selectionEnd;
    if (start === end) {
      const inside = spanInside(tokens, start);
      if (!inside) return;
      event.preventDefault();
      const edge = nearestEdge(inside, start);
      el.setSelectionRange(edge, edge);
      return;
    }
    const whole = expandToWhole(tokens, start, end);
    if (whole.start === start && whole.end === end) return;
    event.preventDefault();
    el.setSelectionRange(whole.start, whole.end);
  };

  // Clicks, drags and anything else that moves the caret without a keystroke.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const settle = () => {
      if (document.activeElement !== el) return;
      const tokens = atomicRef.current;
      if (!tokens.length) return;
      const start = el.selectionStart;
      const end = el.selectionEnd;
      if (start === end) {
        const inside = spanInside(tokens, start);
        if (!inside) return;
        const edge = nearestEdge(inside, start);
        el.setSelectionRange(edge, edge);
        return;
      }
      const whole = expandToWhole(tokens, start, end);
      if (whole.start !== start || whole.end !== end) el.setSelectionRange(whole.start, whole.end);
    };
    document.addEventListener("selectionchange", settle);
    return () => document.removeEventListener("selectionchange", settle);
  }, []);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget;

    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z") {
      if (undo()) e.preventDefault();
      return;
    }

    if (e.key === "ArrowRight" && stepOverToken(el, 1)) { e.preventDefault(); return; }
    if (e.key === "ArrowLeft" && stepOverToken(el, -1)) { e.preventDefault(); return; }

    if (!isOpen) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev + 1) % (filteredItems.length || 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev - 1 + (filteredItems.length || 1)) % (filteredItems.length || 1));
    } else if (e.key === "Enter" || e.key === "Tab") {
      if (filteredItems.length > 0 && filteredItems[selectedIndex]) {
        e.preventDefault();
        choose(filteredItems[selectedIndex]);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      setIsOpen(false);
      setSearchQuery("");
    }
  };

  // Outside click handler
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(event.target as Node) &&
        textareaRef.current &&
        !textareaRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [isOpen]);

  // Validation Warnings. What the scanner already paints red (an unknown stage, a
  // resource that is not in the library) is reported there instead of twice.
  const warnings = useMemo(() => {
    const list: string[] = [];
    if (content.includes("[FECHA Y HORA ACTUAL DEL NEGOCIO]")) {
      const tz = (clientTimezone || "").trim().toUpperCase();
      if (!tz || tz === "UTC") {
        list.push(t("agents.detail.timezoneUtcWarning"));
      }
    }
    if (citedResources(content).length && resourceTool && !resourceToolRe?.test(content)) {
      list.push(t("resources.warnings.missingTool"));
    }
    return list;
  }, [content, clientTimezone, resourceTool, resourceToolRe, t]);

  const categoryTitle = (cat: string) => {
    if (cat === "tools") return t("agents.detail.toolsCategory");
    if (cat === "blocks") return t("agents.detail.blocksCategory");
    if (cat === "resources") return t("resources.title");
    if (cat === "control") return t("agents.detail.controlCategory");
    return t("agents.detail.stagesCategory");
  };

  return (
    <div className="agent-prompt-editor-wrap" style={{ position: "relative" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <span style={{ fontSize: 13, fontWeight: 500 }}>{t("agents.detail.promptLabel")}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {undoDepth > 0 && (
            <button type="button" className="prompt-editor-undo" style={{ marginLeft: 0 }} onClick={undo}>
              <Undo2 size={13} /> {t("agents.detail.undoInsert")}
            </button>
          )}
          <button
            type="button"
            className="button secondary small"
            style={{ fontSize: 12, padding: "4px 8px", display: "inline-flex", alignItems: "center", gap: 5 }}
            onClick={() => {
              setSearchQuery("");
              setIsOpen((prev) => !prev);
              textareaRef.current?.focus();
            }}
            title={t("agents.detail.insertVariableOrTool")}
          >
            <Brackets size={14} />
            <span>{t("agents.detail.insertVariableOrTool")}</span>
          </button>
        </div>
      </div>

      <div style={{ position: "relative" }}>
        <div className="prompt-editor">
          <div ref={mirrorRef} className="prompt-editor-mirror" aria-hidden="true">
            {painted.map((part, index) =>
              part.span ? (
                <span
                  key={index}
                  className={`pv pv-${part.span.draft ? "draft" : part.span.known ? part.span.kind : "unknown"}`}
                >
                  {part.text}
                </span>
              ) : (
                <span key={index}>{part.text}</span>
              ),
            )}
            {"\n"}
          </div>
          <textarea
            ref={textareaRef}
            name="instructions"
            rows={18}
            value={content}
            onChange={handleTextareaInput}
            onKeyDown={handleKeyDown}
            onBeforeInput={handleBeforeInput}
            onScroll={syncScroll}
            placeholder={placeholder}
            className="prompt-editor-input"
          />
        </div>

        {isOpen && (
          <div
            ref={popoverRef}
            className="variables-popover"
            style={{
              position: "absolute",
              bottom: "auto",
              top: 8,
              left: 8,
              right: 8,
              width: "auto",
              maxWidth: 580,
              maxHeight: 380,
              zIndex: 100,
            }}
          >
            <div className="variables-header" style={{ padding: "8px 12px" }}>
              <div className="variables-title">
                <span style={{ fontWeight: 600 }}>{t("agents.detail.insertVariableOrTool")}</span>
                <span style={{ fontSize: 11, opacity: 0.7 }}>
                  {searchQuery ? `Filtrando por: "${searchQuery}"` : 'Escribe "[" para filtrar'}
                </span>
              </div>
              <div className="variables-hint">
                <span>↑↓ NAVEGAR • ↵ INSERTAR • ESC CERRAR</span>
              </div>
            </div>

            <div className="variables-list" style={{ maxHeight: 310, overflowY: "auto", padding: 6 }}>
              {filteredItems.length === 0 ? (
                <div style={{ padding: "16px", textAlign: "center", color: "#888", fontSize: 13 }}>
                  {catalogFailed
                    ? t("agents.detail.variablesUnavailable")
                    : t("agents.detail.variablesNoneFound", { query: searchQuery })}
                </div>
              ) : (
                filteredItems.map((item, idx) => {
                  const isSelected = idx === selectedIndex;
                  const color = CATEGORY_COLOR[item.category];
                  return (
                    <button
                      key={item.key}
                      type="button"
                      className={`variable-item${isSelected ? " selected" : ""}`}
                      // eslint-disable-next-line react-hooks/refs
                      onClick={() => choose(item)}
                      onMouseEnter={() => setSelectedIndex(idx)}
                      style={{
                        padding: "8px 10px",
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 8,
                        borderRadius: 6,
                        border: "none",
                        background: isSelected ? "rgba(59, 130, 246, 0.15)" : "transparent",
                        cursor: "pointer",
                        textAlign: "left",
                        width: "100%",
                      }}
                    >
                      <span style={{ marginTop: 2, opacity: 0.8 }}>
                        {item.category === "tools" ? <Wrench size={15} /> : item.category === "blocks" ? <Layers size={15} /> : item.category === "resources" ? <Link2 size={15} /> : <ChevronRight size={15} />}
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <strong style={{ fontSize: 13, fontFamily: "monospace" }}>{item.label}</strong>
                          <span
                            className="variable-category-pill"
                            style={{
                              fontSize: 10,
                              padding: "2px 6px",
                              borderRadius: 4,
                              background: color.fill,
                              color: color.text,
                            }}
                          >
                            {categoryTitle(item.category)}
                          </span>
                        </div>
                        <p style={{ margin: "2px 0 0", fontSize: 11, opacity: 0.75, lineHeight: 1.3 }}>
                          {item.description}
                        </p>
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>

      <div className="prompt-editor-status">
        <span className="prompt-editor-count">
          <Brackets size={13} />
          {t("agents.detail.variablesCount", { count: atomic.length })}
        </span>
        {unknown.length > 0 && (
          <span className="prompt-editor-count" style={{ color: "var(--red-text)" }}>
            <AlertCircle size={13} />
            {t("agents.detail.variablesUnknown", { count: unknown.length })}
          </span>
        )}
        {unknown.map((span) => (
          <span key={span.start} className="prompt-editor-unknown" title={t("agents.detail.variablesUnknownHint")}>
            {span.raw}
          </span>
        ))}
        {atomic.length > 0 && <span>{t("agents.detail.atomicHint")}</span>}
      </div>

      <span className="field-help" style={{ marginTop: 4, display: "block" }}>
        {t("agents.detail.promptHelp")}
      </span>

      {warnings.length > 0 && (
        <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
          {warnings.map((warn, i) => (
            <Alert key={i} type="info">
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <AlertCircle size={15} />
                <span>{warn}</span>
              </div>
            </Alert>
          ))}
        </div>
      )}

      {pickerOpen && resourceTool && (
        <ResourcePicker
          resources={(resources ?? []).filter((r) => r.is_active)}
          initial={citedResources(content)}
          fileUrl={resourceFileUrl}
          onApply={(names) => applyPicker(names)}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}

/** Lets the operator check which library resources the enviar_recurso tool may send. */
function ResourcePicker({ resources, initial, fileUrl, onApply, onClose }: {
  resources: ClientResource[];
  initial: string[];
  fileUrl?: (resource: ClientResource) => string;
  onApply: (names: string[]) => void;
  onClose: () => void;
}) {
  const { t } = useLanguage();
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<string>>(() => {
    const cited = new Set(initial.map(fold));
    return new Set(resources.filter((r) => cited.has(fold(r.name))).map((r) => r.id));
  });
  const shown = resources.filter((r) => !query.trim() || fold(`${r.name} ${r.description}`).includes(fold(query)));
  const toggle = (id: string) => setPicked((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const icon = (r: ClientResource) => r.kind === "file" ? r.media_kind === "image" ? <ImageIcon size={16} /> : r.media_kind === "video" ? <Film size={16} /> : r.media_kind === "audio" ? <Music size={16} /> : <FileText size={16} /> : <Link2 size={16} />;

  return <Modal open title={t("resources.picker.title")} description={t("resources.picker.copy")} onClose={onClose}>
    <div className="modal-form">
      {resources.length === 0 ? <Alert type="info">{t("resources.picker.empty")}</Alert> : <>
        <input className="resource-picker-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("resources.picker.search")} autoFocus />
        <ul className="resource-picker-list">
          {shown.map((r) => (
            <li key={r.id}>
              <label>
                <input type="checkbox" checked={picked.has(r.id)} onChange={() => toggle(r.id)} />
                {r.kind === "file" && r.media_kind === "image" && fileUrl
                  ? <img className="resource-thumb" src={fileUrl(r)} alt="" loading="lazy" />
                  : <span className="resource-thumb resource-icon">{icon(r)}</span>}
                <span>
                  <strong>{r.name}</strong>
                  <small>{r.description || (r.kind === "link" ? r.url : r.filename)}</small>
                </span>
              </label>
            </li>
          ))}
        </ul>
      </>}
      <div className="modal-actions">
        <span className="field-help" style={{ marginRight: "auto" }}>{t("resources.picker.selected", { count: picked.size })}</span>
        <button type="button" className="button" onClick={onClose}>{t("common.cancel")}</button>
        <button type="button" className="button primary" disabled={resources.length === 0}
          onClick={() => onApply(resources.filter((r) => picked.has(r.id)).map((r) => r.name))}>
          <Check size={15} /> {t("resources.picker.insert")}
        </button>
      </div>
    </div>
  </Modal>;
}
