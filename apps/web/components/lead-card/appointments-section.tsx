"use client";

import { useCallback, useEffect, useState } from "react";
import { Calendar, Clock, Plus, RotateCw, XCircle, CheckCircle2, User, Scissors } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { useLanguage, useT } from "@/lib/i18n";
import { useToast } from "@/components/toast";
import { AppointmentModal } from "@/components/appointment-modal";
import type { Appointment } from "@/types";

interface AppointmentsSectionProps {
  conversationId: string;
  contactId?: string | null;
  base: string;
  syncKey?: string | number | null;
  onChanged?: () => void;
}

export function AppointmentsSection({
  conversationId,
  contactId,
  base,
  syncKey,
  onChanged,
}: AppointmentsSectionProps) {
  const t = useT();
  const { lang } = useLanguage();
  const toast = useToast();

  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingAppointment, setEditingAppointment] = useState<Appointment | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);

  const loadAppointments = useCallback(async () => {
    try {
      setLoading(true);
      const rows = await api<Appointment[]>(
        `${base}/appointments?conversation_id=${conversationId}`
      );
      setAppointments(rows);
    } catch {
      setAppointments([]);
    } finally {
      setLoading(false);
    }
  }, [base, conversationId]);

  useEffect(() => {
    loadAppointments();
  }, [loadAppointments, syncKey]);

  async function handleCancel(apt: Appointment) {
    if (!window.confirm("¿Seguro que deseas cancelar esta cita?")) return;
    setCancellingId(apt.id);
    try {
      await api<Appointment>(`${base}/appointments/${apt.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "cancelled" }),
      });
      toast.success(t("inbox.appointmentCancelledSuccess") || "Cita cancelada.");
      await loadAppointments();
      onChanged?.();
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setCancellingId(null);
    }
  }

  function formatDateTime(iso: string): string {
    const d = new Date(iso);
    return d.toLocaleString(lang === "es" ? "es-ES" : "en-US", {
      weekday: "short",
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }

  return (
    <section className="lead-section" style={{ borderTop: "1px solid var(--border)", paddingTop: "1rem", marginTop: "1rem" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "0.5rem" }}>
        <h4 className="lead-subhead" style={{ margin: 0, display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <Calendar size={14} />
          <span>{t("inbox.appointmentActiveTitle") || "Citas agendadas"}</span>
          {appointments.length > 0 && (
            <span style={{ fontSize: "0.75rem", background: "var(--bg-subtle, #f4f4f5)", padding: "0.1rem 0.4rem", borderRadius: "10px", color: "var(--muted)" }}>
              {appointments.length}
            </span>
          )}
        </h4>
        <button
          type="button"
          className="button secondary small"
          style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem", display: "flex", alignItems: "center", gap: "0.25rem" }}
          onClick={() => {
            setEditingAppointment(null);
            setModalOpen(true);
          }}
        >
          <Plus size={13} />
          <span>Agendar</span>
        </button>
      </div>

      {loading && appointments.length === 0 ? (
        <p style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Cargando citas...</p>
      ) : appointments.length === 0 ? (
        <p className="lead-empty" style={{ margin: 0 }}>
          <small>{t("inbox.appointmentNone") || "Sin citas agendadas"}</small>
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {appointments.map((apt) => {
            const isConfirmed = apt.status === "confirmed";
            const isCancelled = apt.status === "cancelled";
            const isCompleted = apt.status === "completed";

            return (
              <div
                key={apt.id}
                style={{
                  border: "1px solid var(--border)",
                  borderRadius: "6px",
                  padding: "0.6rem 0.75rem",
                  background: isCancelled ? "var(--bg-subtle, #fafafa)" : "var(--card-bg, #fff)",
                  opacity: isCancelled ? 0.65 : 1,
                  display: "flex",
                  flexDirection: "column",
                  gap: "0.3rem",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <strong style={{ fontSize: "0.85rem", textDecoration: isCancelled ? "line-through" : "none" }}>
                    {apt.title}
                  </strong>
                  <span
                    style={{
                      fontSize: "0.7rem",
                      fontWeight: 500,
                      padding: "0.1rem 0.4rem",
                      borderRadius: "4px",
                      textTransform: "capitalize",
                      background: isConfirmed ? "rgba(16, 185, 129, 0.1)" : isCancelled ? "rgba(239, 68, 68, 0.1)" : "rgba(59, 130, 246, 0.1)",
                      color: isConfirmed ? "#059669" : isCancelled ? "#dc2626" : "#2563eb",
                    }}
                  >
                    {isConfirmed
                      ? t("inbox.appointmentStatusConfirmed") || "Confirmada"
                      : isCancelled
                      ? t("inbox.appointmentStatusCancelled") || "Cancelada"
                      : isCompleted
                      ? t("inbox.appointmentStatusCompleted") || "Completada"
                      : apt.status}
                  </span>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.775rem", color: "var(--muted)" }}>
                  <Clock size={12} />
                  <span>{formatDateTime(apt.start_time)}</span>
                  <span>({apt.duration_minutes} min)</span>
                </div>

                {(apt.professional_name || apt.service_name) && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", fontSize: "0.75rem", color: "var(--muted)", marginTop: "0.1rem" }}>
                    {apt.service_name && (
                      <span style={{ display: "flex", alignItems: "center", gap: "0.2rem" }}>
                        <Scissors size={11} /> {apt.service_name}
                      </span>
                    )}
                    {apt.professional_name && (
                      <span style={{ display: "flex", alignItems: "center", gap: "0.2rem" }}>
                        <User size={11} /> {apt.professional_name}
                      </span>
                    )}
                  </div>
                )}

                {apt.notes && (
                  <p style={{ margin: 0, fontSize: "0.75rem", color: "var(--muted)", fontStyle: "italic" }}>
                    {apt.notes}
                  </p>
                )}

                {/* Actions for confirmed appointments */}
                {isConfirmed && (
                  <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem", marginTop: "0.4rem", borderTop: "1px dashed var(--border)", paddingTop: "0.4rem" }}>
                    <button
                      type="button"
                      style={{ fontSize: "0.75rem", color: "var(--accent)", background: "transparent", border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: "0.2rem" }}
                      onClick={() => {
                        setEditingAppointment(apt);
                        setModalOpen(true);
                      }}
                    >
                      <RotateCw size={11} />
                      <span>{t("inbox.appointmentReschedule") || "Reagendar"}</span>
                    </button>
                    <button
                      type="button"
                      disabled={cancellingId === apt.id}
                      style={{ fontSize: "0.75rem", color: "#dc2626", background: "transparent", border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: "0.2rem" }}
                      onClick={() => handleCancel(apt)}
                    >
                      <XCircle size={11} />
                      <span>{t("inbox.appointmentCancelAction") || "Cancelar"}</span>
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {modalOpen && (
        <AppointmentModal
          open={modalOpen}
          onClose={() => {
            setModalOpen(false);
            setEditingAppointment(null);
          }}
          base={base}
          conversationId={conversationId}
          contactId={contactId}
          initialAppointment={editingAppointment}
          onSuccess={() => {
            loadAppointments();
            onChanged?.();
          }}
        />
      )}
    </section>
  );
}
