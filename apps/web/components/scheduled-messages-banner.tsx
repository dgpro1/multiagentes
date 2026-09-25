"use client";

import { useEffect, useState } from "react";
import { Clock, X, ChevronDown, ChevronUp, LoaderCircle } from "lucide-react";
import { formatWhen } from "@/lib/datetime";
import { useLanguage, useT } from "@/lib/i18n";
import type { ScheduledMessage } from "@/types";

interface ScheduledMessagesBannerProps {
  messages: ScheduledMessage[];
  onCancel: (scheduledId: string) => Promise<void>;
}

export function ScheduledMessagesBanner({
  messages,
  onCancel,
}: ScheduledMessagesBannerProps) {
  const t = useT();
  const { lang } = useLanguage();
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(0);

  useEffect(() => {
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const pending = messages.filter((m) => m.status === "pending");
  if (pending.length === 0) return null;

  async function handleCancel(id: string) {
    try {
      setCancellingId(id);
      await onCancel(id);
    } finally {
      setCancellingId(null);
    }
  }

  const first = pending[0];
  const count = pending.length;
  const isDue = now > 0 && new Date(first.scheduled_for).getTime() <= now;

  return (
    <div
      style={{
        margin: "0 0 8px 0",
        padding: "8px 12px",
        borderRadius: 8,
        background: "var(--surface-2)",
        border: "1px solid var(--line)",
        fontSize: 13,
        color: "var(--ink)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flex: 1 }}>
          {isDue ? (
            <LoaderCircle size={16} className="spin" color="#8b5cf6" style={{ flexShrink: 0 }} />
          ) : (
            <Clock size={16} color="#8b5cf6" style={{ flexShrink: 0 }} />
          )}
          <div style={{ display: "flex", alignItems: "baseline", gap: 6, minWidth: 0, overflow: "hidden" }}>
            <span style={{ fontWeight: 600, color: "var(--ink)", flexShrink: 0 }}>
              {count === 1
                ? t("inbox.scheduledSingle") || "Mensaje programado"
                : `${count} ${t("inbox.scheduledMultiple") || "mensajes programados"}`}
            </span>
            <span style={{ color: isDue ? "var(--accent)" : "var(--muted)", fontSize: 12, flexShrink: 0, display: "inline-flex", alignItems: "center", gap: 4 }}>
              ({isDue ? t("inbox.sendingScheduled") || "Enviando..." : formatWhen(first.scheduled_for, lang)}):
            </span>
            <span
              style={{
                color: "var(--ink-2)",
                fontSize: 12,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={first.content}
            >
              &ldquo;{first.content}&rdquo;
            </span>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          {count > 1 && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--muted)",
                cursor: "pointer",
                padding: "2px 6px",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                fontSize: 12,
              }}
            >
              {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              <span>{expanded ? t("inbox.hide") || "Ocultar" : t("inbox.viewAll") || "Ver todos"}</span>
            </button>
          )}

          <button
            type="button"
            onClick={() => handleCancel(first.id)}
            disabled={cancellingId === first.id}
            title={t("inbox.cancelScheduled") || "Cancelar envío programado"}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "3px 8px",
              borderRadius: 6,
              border: "1px solid var(--line)",
              background: "transparent",
              color: "var(--ink-2)",
              fontSize: 12,
              cursor: cancellingId === first.id ? "not-allowed" : "pointer",
            }}
          >
            {cancellingId === first.id ? (
              <LoaderCircle size={13} className="spin" />
            ) : (
              <X size={13} />
            )}
            <span>{t("common.cancel") || "Cancelar"}</span>
          </button>
        </div>
      </div>

      {/* Expanded list when multiple scheduled messages exist */}
      {expanded && count > 1 && (
        <div
          style={{
            marginTop: 8,
            paddingTop: 8,
            borderTop: "1px solid var(--line-soft)",
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          {pending.slice(1).map((msg) => (
            <div
              key={msg.id}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
                fontSize: 12,
                padding: "4px 0",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, overflow: "hidden" }}>
                {(() => {
                  const msgDue = now > 0 && new Date(msg.scheduled_for).getTime() <= now;
                  return (
                    <span style={{ color: msgDue ? "var(--accent)" : "var(--muted)", flexShrink: 0, display: "inline-flex", alignItems: "center", gap: 3 }}>
                      {msgDue && <LoaderCircle size={10} className="spin" />}
                      ({msgDue ? t("inbox.sendingScheduled") || "Enviando..." : formatWhen(msg.scheduled_for, lang)}):
                    </span>
                  );
                })()}
                <span
                  style={{
                    color: "var(--ink-2)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={msg.content}
                >
                  &ldquo;{msg.content}&rdquo;
                </span>
              </div>
              <button
                type="button"
                onClick={() => handleCancel(msg.id)}
                disabled={cancellingId === msg.id}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 3,
                  padding: "2px 6px",
                  borderRadius: 4,
                  border: "1px solid var(--line)",
                  background: "transparent",
                  color: "var(--ink-2)",
                  fontSize: 11,
                  cursor: cancellingId === msg.id ? "not-allowed" : "pointer",
                  flexShrink: 0,
                }}
              >
                {cancellingId === msg.id ? (
                  <LoaderCircle size={11} className="spin" />
                ) : (
                  <X size={11} />
                )}
                <span>{t("common.cancel") || "Cancelar"}</span>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
