"use client";

import { useEffect, useState } from "react";
import { Clock, Send, Calendar } from "lucide-react";
import { useT } from "@/lib/i18n";
import { Modal, Alert } from "@/components/ui";
import { CalendarPicker } from "@/components/calendar-picker";

interface ScheduleMessageModalProps {
  open: boolean;
  onClose: () => void;
  onSchedule: (content: string, scheduledFor: string) => Promise<void>;
  initialContent?: string;
}

export function ScheduleMessageModal({
  open,
  onClose,
  onSchedule,
  initialContent = "",
}: ScheduleMessageModalProps) {
  const t = useT();

  const [content, setContent] = useState(initialContent);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("09:00");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Initialize date & time when modal opens
  useEffect(() => {
    if (open) {
      setContent(initialContent);
      setError(null);
      setSubmitting(false);

      // Default to 1 hour from now, rounded to next 15 min
      const now = new Date();
      now.setHours(now.getHours() + 1);
      const minutes = Math.ceil(now.getMinutes() / 15) * 15;
      if (minutes >= 60) {
        now.setHours(now.getHours() + 1);
        now.setMinutes(0);
      } else {
        now.setMinutes(minutes);
      }

      const yyyy = now.getFullYear();
      const mm = String(now.getMonth() + 1).padStart(2, "0");
      const dd = String(now.getDate()).padStart(2, "0");
      setDate(`${yyyy}-${mm}-${dd}`);

      const hh = String(now.getHours()).padStart(2, "0");
      const minStr = String(now.getMinutes()).padStart(2, "0");
      setTime(`${hh}:${minStr}`);
    }
  }, [open, initialContent]);

  const todayStr = (() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  })();

  function setQuickPreset(hoursOffset: number, specificHour?: number, specificMin: number = 0) {
    const target = new Date();
    if (specificHour !== undefined) {
      target.setDate(target.getDate() + (hoursOffset > 0 ? 1 : 0));
      target.setHours(specificHour, specificMin, 0, 0);
    } else {
      target.setTime(target.getTime() + hoursOffset * 3600 * 1000);
    }
    const yyyy = target.getFullYear();
    const mm = String(target.getMonth() + 1).padStart(2, "0");
    const dd = String(target.getDate()).padStart(2, "0");
    setDate(`${yyyy}-${mm}-${dd}`);
    const hh = String(target.getHours()).padStart(2, "0");
    const minStr = String(target.getMinutes()).padStart(2, "0");
    setTime(`${hh}:${minStr}`);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!content.trim()) {
      setError(t("inbox.scheduleEmptyError") || "El contenido del mensaje no puede estar vacío");
      return;
    }
    if (!date || !time) {
      setError(t("inbox.scheduleSelectDateTimeError") || "Selecciona una fecha y hora válidas");
      return;
    }

    const scheduledDate = new Date(`${date}T${time}:00`);
    if (isNaN(scheduledDate.getTime())) {
      setError(t("inbox.scheduleInvalidDateTimeError") || "Fecha u hora inválida");
      return;
    }

    if (scheduledDate.getTime() <= Date.now()) {
      setError(t("inbox.schedulePastError") || "La fecha y hora deben ser futuras");
      return;
    }

    try {
      setSubmitting(true);
      setError(null);
      await onSchedule(content.trim(), scheduledDate.toISOString());
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Error al programar el mensaje");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => !submitting && onClose()}
      title={t("inbox.scheduleMessageTitle") || "Programar mensaje"}
    >
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {error && <Alert type="error">{error}</Alert>}

        {/* Message Content */}
        <div>
          <label
            style={{
              display: "block",
              fontSize: 13,
              fontWeight: 500,
              color: "var(--ink)",
              marginBottom: 6,
            }}
          >
            {t("inbox.scheduleMessageContent") || "Mensaje a enviar"}
          </label>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={
              t("inbox.scheduleMessagePlaceholder") ||
              "Escribe el mensaje que deseas programar para este contacto..."
            }
            rows={4}
            disabled={submitting}
            style={{
              width: "100%",
              padding: "10px 12px",
              borderRadius: 8,
              border: "1px solid var(--line)",
              background: "var(--surface)",
              color: "var(--ink)",
              fontSize: 14,
              resize: "vertical",
              outline: "none",
              fontFamily: "inherit",
            }}
          />
        </div>

        {/* Quick Presets */}
        <div>
          <label
            style={{
              display: "block",
              fontSize: 12,
              fontWeight: 500,
              color: "var(--muted)",
              marginBottom: 6,
            }}
          >
            {t("inbox.schedulePresets") || "Accesos rápidos"}
          </label>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={() => setQuickPreset(1)}
              style={{
                fontSize: 12,
                padding: "4px 10px",
                borderRadius: 16,
                border: "1px solid var(--line)",
                background: "var(--surface-2)",
                color: "var(--ink)",
                cursor: "pointer",
              }}
            >
              +1 hora
            </button>
            <button
              type="button"
              onClick={() => setQuickPreset(3)}
              style={{
                fontSize: 12,
                padding: "4px 10px",
                borderRadius: 16,
                border: "1px solid var(--line)",
                background: "var(--surface-2)",
                color: "var(--ink)",
                cursor: "pointer",
              }}
            >
              +3 horas
            </button>
            <button
              type="button"
              onClick={() => setQuickPreset(24, 9, 0)}
              style={{
                fontSize: 12,
                padding: "4px 10px",
                borderRadius: 16,
                border: "1px solid var(--line)",
                background: "var(--surface-2)",
                color: "var(--ink)",
                cursor: "pointer",
              }}
            >
              Mañana 09:00
            </button>
            <button
              type="button"
              onClick={() => setQuickPreset(24, 15, 0)}
              style={{
                fontSize: 12,
                padding: "4px 10px",
                borderRadius: 16,
                border: "1px solid var(--line)",
                background: "var(--surface-2)",
                color: "var(--ink)",
                cursor: "pointer",
              }}
            >
              Mañana 15:00
            </button>
          </div>
        </div>

        {/* Date & Time Picker */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 140px", gap: 12, alignItems: "start" }}>
          <div>
            <label
              style={{
                display: "block",
                fontSize: 13,
                fontWeight: 500,
                color: "var(--ink)",
                marginBottom: 6,
              }}
            >
              {t("inbox.scheduleDate") || "Fecha de envío"}
            </label>
            <CalendarPicker
              value={date}
              onChange={(val) => setDate(val)}
              min={todayStr}
              disabled={submitting}
            />
          </div>

          <div>
            <label
              style={{
                display: "block",
                fontSize: 13,
                fontWeight: 500,
                color: "var(--ink)",
                marginBottom: 6,
              }}
            >
              {t("inbox.scheduleTime") || "Hora"}
            </label>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                border: "1px solid var(--line)",
                borderRadius: 8,
                padding: "8px 10px",
                background: "var(--surface)",
              }}
            >
              <Clock size={16} color="var(--muted)" />
              <input
                type="time"
                value={time}
                onChange={(e) => setTime(e.target.value)}
                disabled={submitting}
                style={{
                  border: "none",
                  background: "transparent",
                  color: "var(--ink)",
                  fontSize: 14,
                  outline: "none",
                  width: "100%",
                  fontFamily: "inherit",
                }}
              />
            </div>
          </div>
        </div>

        {/* Schedule Summary Banner */}
        {date && time && (
          <div
            style={{
              padding: "10px 14px",
              borderRadius: 8,
              background: "var(--surface-2)",
              border: "1px solid var(--line)",
              fontSize: 13,
              color: "var(--ink)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <Calendar size={16} color="var(--accent)" />
            <span>
              {t("inbox.scheduleWillSendOn") || "Se enviará el"}:{" "}
              <strong>
                {date} a las {time}
              </strong>
            </span>
          </div>
        )}

        {/* Actions */}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 8 }}>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            style={{
              padding: "8px 16px",
              borderRadius: 8,
              border: "1px solid var(--line)",
              background: "transparent",
              color: "var(--ink)",
              fontSize: 14,
              cursor: submitting ? "not-allowed" : "pointer",
            }}
          >
            {t("common.cancel") || "Cancelar"}
          </button>
          <button
            type="submit"
            disabled={submitting || !content.trim()}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 18px",
              borderRadius: 8,
              border: "none",
              background: "var(--accent)",
              color: "var(--accent-text)",
              fontSize: 14,
              fontWeight: 500,
              cursor: submitting || !content.trim() ? "not-allowed" : "pointer",
              opacity: submitting || !content.trim() ? 0.6 : 1,
            }}
          >
            <Send size={15} />
            <span>
              {submitting
                ? t("inbox.saving") || "Guardando..."
                : t("inbox.scheduleConfirmButton") || "Programar mensaje"}
            </span>
          </button>
        </div>
      </form>
    </Modal>
  );
}
