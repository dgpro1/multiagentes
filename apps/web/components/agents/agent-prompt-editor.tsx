"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { AlertCircle, Brackets, ChevronRight, Layers, Wrench } from "lucide-react";
import { Alert } from "@/components/ui";
import { useLanguage } from "@/lib/i18n";
import type { PipelineStage } from "@/types";

export type PromptItem = {
  key: string;
  token: string;
  label: string;
  category: "tools" | "blocks" | "stages";
  description: string;
};

interface AgentPromptEditorProps {
  defaultValue?: string;
  placeholder?: string;
  pipelineStages: PipelineStage[];
  clientTimezone?: string;
  onChange?: (val: string) => void;
}

function fold(s: string): string {
  return s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim();
}

export function AgentPromptEditor({
  defaultValue = "",
  placeholder = "",
  pipelineStages,
  clientTimezone,
  onChange,
}: AgentPromptEditorProps) {
  const { t } = useLanguage();
  const [content, setContent] = useState(defaultValue);
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Sync internal state if defaultValue changes from external loads
  useEffect(() => {
    setContent(defaultValue);
  }, [defaultValue]);

  // Catalog of items
  const items: PromptItem[] = useMemo(() => {
    const list: PromptItem[] = [
      // Commercial Tools
      {
        key: "tool-avail",
        token: "[Herramienta: check_calendar_availability]",
        label: "[Herramienta: check_calendar_availability]",
        category: "tools",
        description: "Consulta disponibilidad y cupos libres de agenda",
      },
      {
        key: "tool-book",
        token: "[Herramienta: book_calendar_appointment]",
        label: "[Herramienta: book_calendar_appointment]",
        category: "tools",
        description: "Agenda cita formal (requiere nombre real verificado)",
      },
      {
        key: "tool-resched",
        token: "[Herramienta: reschedule_appointment]",
        label: "[Herramienta: reschedule_appointment]",
        category: "tools",
        description: "Reprograma cita usando el número de la lista (ej: 1)",
      },
      {
        key: "tool-update",
        token: "[Herramienta: update_contact_info]",
        label: "[Herramienta: update_contact_info]",
        category: "tools",
        description: "Actualiza nombre, teléfono o email del contacto",
      },
      {
        key: "tool-stage",
        token: '[Herramienta: move_lead_stage] a [Etapa: ...]',
        label: "[Herramienta: move_lead_stage]",
        category: "tools",
        description: "Avanza etapa del embudo comercial (con protección anti-regresión)",
      },
      {
        key: "tool-tag",
        token: '[Herramienta: add_lead_tag] con "..."',
        label: "[Herramienta: add_lead_tag]",
        category: "tools",
        description: 'Asigna una etiqueta comercial (ej: [Herramienta: add_lead_tag] con "Ortodoncia")',
      },
      {
        key: "tool-note",
        token: "[Herramienta: add_internal_note]",
        label: "[Herramienta: add_internal_note]",
        category: "tools",
        description: "Registra una nota interna visible solo para el equipo",
      },
      {
        key: "tool-escalate",
        token: "[Herramienta: escalate_to_human]",
        label: "[Herramienta: escalate_to_human]",
        category: "tools",
        description: "Deriva la conversación al equipo humano con motivo",
      },
      {
        key: "tool-silent",
        token: "[Herramienta: stay_silent]",
        label: "[Herramienta: stay_silent]",
        category: "tools",
        description: "Permanece en silencio cuando no amerita responder",
      },

      // Dynamic Context Blocks
      {
        key: "block-datetime",
        token: "[FECHA Y HORA ACTUAL DEL NEGOCIO]",
        label: "[FECHA Y HORA ACTUAL DEL NEGOCIO]",
        category: "blocks",
        description: "Inyecta hora local y tabla de referencia de los próximos 14 días",
      },
      {
        key: "block-catalog",
        token: "[CATÁLOGO OFICIAL DE SERVICIOS Y TARIFAS]",
        label: "[CATÁLOGO OFICIAL DE SERVICIOS Y TARIFAS]",
        category: "blocks",
        description: "Inyecta catálogo de servicios y tarifas con formato de moneda",
      },
      {
        key: "block-location",
        token: "[UBICACIÓN Y DATOS DEL NEGOCIO]",
        label: "[UBICACIÓN Y DATOS DEL NEGOCIO]",
        category: "blocks",
        description: "Inyecta dirección física, mapa y horarios de atención comercial",
      },
      {
        key: "block-lead",
        token: "[FICHA COMERCIAL DEL PROSPECTO / CLIENTE]",
        label: "[FICHA COMERCIAL DEL PROSPECTO / CLIENTE]",
        category: "blocks",
        description: "Inyecta ficha del contacto (distingue nombre real de perfil), etapa y tags",
      },
      {
        key: "block-appointments",
        token: "[CITAS ACTIVAS PROGRAMADAS PARA ESTE CLIENTE]",
        label: "[CITAS ACTIVAS PROGRAMADAS PARA ESTE CLIENTE]",
        category: "blocks",
        description: "Inyecta citas futuras numeradas (1, 2) en formato amigable",
      },
      {
        key: "block-notes",
        token: "[NOTAS E INTERVENCIONES PREVIAS DEL EQUIPO HUMANO]",
        label: "[NOTAS E INTERVENCIONES PREVIAS DEL EQUIPO HUMANO]",
        category: "blocks",
        description: "Inyecta historial cronológico de notas internas del equipo",
      },
    ];

    // Pipeline stages from client
    if (pipelineStages && pipelineStages.length > 0) {
      for (const stage of pipelineStages) {
        list.push({
          key: `stage-${stage.id}`,
          token: `[Etapa: ${stage.name}]`,
          label: `[Etapa: ${stage.name}]`,
          category: "stages",
          description: `Etapa oficial del embudo comercial (Posición #${stage.position + 1})`,
        });
      }
    } else {
      list.push(
        {
          key: "stage-descubrimiento",
          token: "[Etapa: Descubrimiento]",
          label: "[Etapa: Descubrimiento]",
          category: "stages",
          description: "Etapa de calificación inicial",
        },
        {
          key: "stage-cita",
          token: "[Etapa: Cita Agendada]",
          label: "[Etapa: Cita Agendada]",
          category: "stages",
          description: "Etapa cuando el prospecto agenda una cita",
        }
      );
    }

    return list;
  }, [pipelineStages]);

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

    const nextVal = newBefore + tokenToInsert + after;
    el.value = nextVal;
    setContent(nextVal);
    onChange?.(nextVal);

    setIsOpen(false);
    setSearchQuery("");

    setTimeout(() => {
      el.focus();
      const pos = newBefore.length + tokenToInsert.length;
      el.setSelectionRange(pos, pos);
    }, 0);
  }, [onChange]);

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

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
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
        insertToken(filteredItems[selectedIndex].token);
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

  // Validation Warnings
  const warnings = useMemo(() => {
    const list: string[] = [];

    // Check timezone
    if (content.includes("[FECHA Y HORA ACTUAL DEL NEGOCIO]")) {
      const tz = (clientTimezone || "").trim().toUpperCase();
      if (!tz || tz === "UTC") {
        list.push(t("agents.detail.timezoneUtcWarning"));
      }
    }

    // Check pipeline stages
    if (pipelineStages && pipelineStages.length > 0) {
      const stageRegex = /\[Etapa:\s*([^\]]+)\]/gi;
      let match;
      const knownStages = new Set(pipelineStages.map((s) => fold(s.name)));
      while ((match = stageRegex.exec(content)) !== null) {
        const rawName = match[1].trim();
        if (rawName && !knownStages.has(fold(rawName))) {
          list.push(t("agents.detail.stageNotFoundWarning", { stage: rawName }));
        }
      }
    }

    return list;
  }, [content, pipelineStages, clientTimezone, t]);

  const categoryTitle = (cat: string) => {
    if (cat === "tools") return t("agents.detail.toolsCategory");
    if (cat === "blocks") return t("agents.detail.blocksCategory");
    return t("agents.detail.stagesCategory");
  };

  return (
    <div className="agent-prompt-editor-wrap" style={{ position: "relative" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <span style={{ fontSize: 13, fontWeight: 500 }}>{t("agents.detail.promptLabel")}</span>
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

      <div style={{ position: "relative" }}>
        <textarea
          ref={textareaRef}
          name="instructions"
          rows={18}
          value={content}
          onChange={handleTextareaInput}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          style={{ width: "100%", fontFamily: "inherit" }}
        />

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
                  No se encontraron herramientas o variables que coincidan con &quot;{searchQuery}&quot;
                </div>
              ) : (
                filteredItems.map((item, idx) => {
                  const isSelected = idx === selectedIndex;
                  return (
                    <button
                      key={item.key}
                      type="button"
                      className={`variable-item${isSelected ? " selected" : ""}`}
                      // eslint-disable-next-line react-hooks/refs
                      onClick={() => insertToken(item.token)}
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
                        {item.category === "tools" ? <Wrench size={15} /> : item.category === "blocks" ? <Layers size={15} /> : <ChevronRight size={15} />}
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
                              background: item.category === "tools" ? "rgba(16, 185, 129, 0.2)" : item.category === "blocks" ? "rgba(139, 92, 246, 0.2)" : "rgba(245, 158, 11, 0.2)",
                              color: item.category === "tools" ? "#10b981" : item.category === "blocks" ? "#a78bfa" : "#f59e0b",
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
    </div>
  );
}
