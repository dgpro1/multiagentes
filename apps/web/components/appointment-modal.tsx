"use client";

import { useEffect, useState } from "react";
import { Calendar as CalendarIcon, Clock, User, Scissors } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useToast } from "@/components/toast";
import { Modal, Alert } from "@/components/ui";
import { CalendarPicker } from "@/components/calendar-picker";
import type { Appointment, AvailabilityResponse, AvailabilitySlot, Professional, Service } from "@/types";

interface AppointmentModalProps {
  open: boolean;
  onClose: () => void;
  base: string;
  conversationId?: string | null;
  contactId?: string | null;
  initialAppointment?: Appointment | null;
  onSuccess: (appointment: Appointment) => void;
}

export function AppointmentModal({
  open,
  onClose,
  base,
  conversationId,
  contactId,
  initialAppointment,
  onSuccess,
}: AppointmentModalProps) {
  const t = useT();
  const toast = useToast();

  const [services, setServices] = useState<Service[]>([]);
  const [professionals, setProfessionals] = useState<Professional[]>([]);

  // Form states
  const todayStr = new Date().toISOString().split("T")[0];
  const [serviceId, setServiceId] = useState<string>(initialAppointment?.service_id || "");
  const [professionalId, setProfessionalId] = useState<string>(initialAppointment?.professional_id || "");
  const [date, setDate] = useState<string>(
    initialAppointment ? initialAppointment.start_time.split("T")[0] : todayStr
  );
  const [slots, setSlots] = useState<AvailabilitySlot[]>([]);
  const [selectedSlot, setSelectedSlot] = useState<AvailabilitySlot | null>(null);
  const [loadingSlots, setLoadingSlots] = useState(false);
  const [availabilityError, setAvailabilityError] = useState<string | null>(null);
  const [title, setTitle] = useState(initialAppointment?.title || "");
  const [notes, setNotes] = useState(initialAppointment?.notes || "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load initial services & professionals
  useEffect(() => {
    if (!open) return;
    let active = true;
    setError(null);

    Promise.allSettled([
      api<Service[]>(`${base}/services`),
      api<Professional[]>(`${base}/professionals`),
    ]).then(([srvRes, profRes]) => {
      if (!active) return;
      if (srvRes.status === "fulfilled") setServices(srvRes.value.filter((s) => s.is_active));
      if (profRes.status === "fulfilled") setProfessionals(profRes.value.filter((p) => p.is_active));
    });

    return () => {
      active = false;
    };
  }, [open, base]);

  // Bidirectional filtered options
  const filteredProfessionals = serviceId
    ? professionals.filter(
        (p) => !p.service_ids || p.service_ids.length === 0 || p.service_ids.includes(serviceId)
      )
    : professionals;

  const selectedProf = professionals.find((p) => p.id === professionalId);
  const filteredServices =
    selectedProf && selectedProf.service_ids && selectedProf.service_ids.length > 0
      ? services.filter((s) => selectedProf.service_ids?.includes(s.id))
      : services;

  // When service changes, update title if empty or matches previous service name
  function handleServiceChange(newServiceId: string) {
    setServiceId(newServiceId);
    const chosen = services.find((s) => s.id === newServiceId);
    if (chosen && (!title || services.some((s) => s.name === title))) {
      setTitle(chosen.name);
    }
    if (newServiceId && professionalId) {
      const prof = professionals.find((p) => p.id === professionalId);
      if (prof && prof.service_ids && prof.service_ids.length > 0 && !prof.service_ids.includes(newServiceId)) {
        setProfessionalId("");
      }
    }
  }

  function handleProfessionalChange(newProfId: string) {
    setProfessionalId(newProfId);
    if (newProfId && serviceId) {
      const prof = professionals.find((p) => p.id === newProfId);
      if (prof && prof.service_ids && prof.service_ids.length > 0 && !prof.service_ids.includes(serviceId)) {
        setServiceId("");
      }
    }
  }

  // Fetch availability when date, service, or professional changes
  useEffect(() => {
    if (!open || !date) return;
    let active = true;
    setLoadingSlots(true);
    setSelectedSlot(null);
    setAvailabilityError(null);

    const params = new URLSearchParams({
      date_from: date,
      date_to: date,
    });
    if (serviceId) params.set("service_id", serviceId);
    if (professionalId) params.set("professional_id", professionalId);

    api<AvailabilityResponse>(`${base}/appointments/availability?${params.toString()}`)
      .then((res) => {
        if (!active) return;
        const daySlots = res.days[0]?.slots || [];
        setSlots(daySlots);
        setAvailabilityError(null);
        setLoadingSlots(false);
      })
      .catch((err) => {
        if (!active) return;
        setSlots([]);
        setAvailabilityError(messageFrom(err));
        setLoadingSlots(false);
      });

    return () => {
      active = false;
    };
  }, [open, base, date, serviceId, professionalId]);

  function formatSlotTime(isoString: string): string {
    const d = new Date(isoString);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!selectedSlot && !initialAppointment) {
      setError(t("inbox.appointmentSlotSelect") || "Selecciona un horario disponible.");
      return;
    }

    const finalTitle = title.trim() || t("inbox.appointmentModalTitle") || "Cita";
    setSubmitting(true);

    try {
      if (initialAppointment) {
        // Reschedule / Update
        const payload: Record<string, unknown> = {
          title: finalTitle,
          notes: notes.trim() || null,
          service_id: serviceId || null,
          professional_id: professionalId || null,
        };
        if (selectedSlot) {
          payload.start_time = selectedSlot.start_time;
          payload.end_time = selectedSlot.end_time;
          if (selectedSlot.professional_id) {
            payload.professional_id = selectedSlot.professional_id;
          }
        }
        const updated = await api<Appointment>(`${base}/appointments/${initialAppointment.id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        toast.success(t("inbox.appointmentUpdatedSuccess") || "Cita actualizada.");
        onSuccess(updated);
        onClose();
      } else {
        // Create new
        if (!selectedSlot) return;
        const payload = {
          conversation_id: conversationId || null,
          contact_id: contactId || null,
          professional_id: selectedSlot.professional_id || (professionalId || null),
          service_id: serviceId || null,
          title: finalTitle,
          start_time: selectedSlot.start_time,
          end_time: selectedSlot.end_time,
          notes: notes.trim() || null,
          created_by_role: "operator",
        };
        const created = await api<Appointment>(`${base}/appointments`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        toast.success(t("inbox.appointmentCreatedSuccess") || "Cita agendada exitosamente.");
        onSuccess(created);
        onClose();
      }
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      title={initialAppointment ? (t("inbox.appointmentReschedule") || "Reagendar cita") : (t("inbox.appointmentModalTitle") || "Agendar cita o tarea")}
      onClose={onClose}
    >
      <form onSubmit={handleSubmit} className="stack-form" style={{ gap: "1rem" }}>
        {error && <Alert type="error">{error}</Alert>}

        {/* Service Picker */}
        {services.length > 0 && (
          <div className="form-row">
            <label className="field-label" htmlFor="appointment-service">
              <Scissors size={14} className="inline mr-1" />
              {t("inbox.appointmentService") || "Servicio"}
            </label>
            <select
              id="appointment-service"
              className="select-input"
              value={serviceId}
              onChange={(e) => handleServiceChange(e.target.value)}
            >
              <option value="">{t("inbox.appointmentServiceSelect") || "Seleccionar un servicio..."}</option>
              {filteredServices.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.duration_minutes} min{s.price > 0 ? ` · $${s.price}` : ""})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Professional Picker */}
        {professionals.length > 0 && (
          <div className="form-row">
            <label className="field-label" htmlFor="appointment-professional">
              <User size={14} className="inline mr-1" />
              {t("inbox.appointmentProfessional") || "Profesional"}
            </label>
            <select
              id="appointment-professional"
              className="select-input"
              value={professionalId}
              onChange={(e) => handleProfessionalChange(e.target.value)}
            >
              <option value="">{t("inbox.appointmentAnyProfessional") || "Cualquier profesional / general"}</option>
              {filteredProfessionals.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} {p.role ? `(${p.role})` : ""}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Date picker */}
        <div className="form-row">
          <label className="field-label" htmlFor="appointment-date">
            <CalendarIcon size={14} className="inline mr-1" />
            {t("inbox.appointmentDate") || "Fecha"}
          </label>
          <CalendarPicker
            value={date}
            onChange={(newDate) => setDate(newDate)}
            min={todayStr}
          />
        </div>

        {/* Slot selector */}
        <div className="form-row">
          <label className="field-label">
            <Clock size={14} className="inline mr-1" />
            {t("inbox.appointmentSlot") || "Horario disponible"}
          </label>
          {loadingSlots ? (
            <div style={{ fontSize: "0.875rem", color: "var(--muted)", padding: "0.5rem 0" }}>
              Cargando disponibilidad...
            </div>
          ) : availabilityError ? (
            <div style={{ fontSize: "0.875rem", color: "var(--red, #ef4444)", padding: "0.5rem 0" }}>
              {availabilityError}
            </div>
          ) : slots.length === 0 ? (
            <div style={{ fontSize: "0.875rem", color: "var(--muted)", padding: "0.5rem 0" }}>
              {t("inbox.appointmentNoSlots") || "No hay horarios disponibles para esta fecha"}
            </div>
          ) : (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(84px, 1fr))",
                gap: "0.5rem",
                maxHeight: "160px",
                overflowY: "auto",
                padding: "0.5rem",
                border: "1px solid var(--line)",
                borderRadius: "8px",
                background: "var(--surface)",
              }}
            >
              {slots.map((s, idx) => {
                const isSelected = selectedSlot?.start_time === s.start_time;
                return (
                  <button
                    key={`${s.start_time}-${idx}`}
                    type="button"
                    onClick={() => setSelectedSlot(s)}
                    style={{
                      padding: "0.45rem 0.5rem",
                      borderRadius: "6px",
                      fontSize: "0.85rem",
                      fontWeight: isSelected ? 600 : 500,
                      backgroundColor: isSelected ? "var(--accent)" : "var(--surface-3)",
                      color: isSelected ? "#ffffff" : "var(--ink)",
                      border: isSelected ? "1px solid var(--accent)" : "1px solid var(--line)",
                      cursor: "pointer",
                      textAlign: "center",
                      transition: "all 0.15s ease",
                    }}
                  >
                    {formatSlotTime(s.start_time)}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Title */}
        <div className="form-row">
          <label className="field-label" htmlFor="appointment-title">
            {t("inbox.appointmentTitle") || "Título / Descripción"}
          </label>
          <input
            id="appointment-title"
            type="text"
            className="input"
            value={title}
            placeholder={t("inbox.appointmentTitlePlaceholder") || "p. ej. Consulta general"}
            onChange={(e) => setTitle(e.target.value)}
            required
          />
        </div>

        {/* Notes */}
        <div className="form-row">
          <label className="field-label" htmlFor="appointment-notes">
            {t("inbox.appointmentNotes") || "Notas (opcional)"}
          </label>
          <textarea
            id="appointment-notes"
            className="input"
            rows={2}
            value={notes}
            placeholder={t("inbox.appointmentNotesPlaceholder") || "Indicaciones adicionales..."}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>

        {/* Action buttons */}
        <div className="modal-actions" style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem", marginTop: "1rem" }}>
          <button type="button" className="button secondary" onClick={onClose} disabled={submitting}>
            {t("inbox.appointmentCancel") || "Cancelar"}
          </button>
          <button
            type="submit"
            className="button primary"
            disabled={submitting || (!selectedSlot && !initialAppointment)}
          >
            {submitting ? "Guardando..." : (t("inbox.appointmentSubmit") || "Agendar cita")}
          </button>
        </div>
      </form>
    </Modal>
  );
}
