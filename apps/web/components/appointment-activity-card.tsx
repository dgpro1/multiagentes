"use client";

import { Calendar, CalendarCheck, CalendarX } from "lucide-react";
import { formatTime } from "@/lib/datetime";
import { useLanguage, useT } from "@/lib/i18n";
import type { Message } from "@/types";

/** True for activity feed entries recorded when an appointment is scheduled, rescheduled, or cancelled. */
export function isAppointmentActivity(message: Message): boolean {
  return (
    message.kind === "activity" &&
    Boolean(
      message.activity?.event &&
      ["appointment_created", "appointment_rescheduled", "appointment_cancelled"].includes(message.activity.event)
    )
  );
}

export function AppointmentActivityCard({ message }: { message: Message }) {
  const t = useT();
  const { lang } = useLanguage();
  const event = message.activity?.event;
  const title = (message.activity?.title as string | undefined) || "";
  const date = (message.activity?.date as string | undefined) || "";
  const stamp = formatTime(message.created_at, lang);

  const isCancelled = event === "appointment_cancelled";
  const isRescheduled = event === "appointment_rescheduled";

  const Icon = isCancelled ? CalendarX : isRescheduled ? Calendar : CalendarCheck;
  const badgeColor = isCancelled
    ? "var(--danger, #ef4444)"
    : isRescheduled
    ? "var(--warning, #f59e0b)"
    : "var(--accent, #6366f1)";

  let eventLabel = t("inbox.appointmentActivityCreated") || "Cita agendada";
  if (isCancelled) eventLabel = t("inbox.appointmentActivityCancelled") || "Cita cancelada";
  if (isRescheduled) eventLabel = t("inbox.appointmentActivityRescheduled") || "Cita reprogramada";

  return (
    <div
      className="appointment-activity-card"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "10px",
        margin: "6px auto",
        padding: "8px 14px",
        borderRadius: "8px",
        border: "1px solid var(--border)",
        background: "var(--surface-2)",
        fontSize: "13px",
        maxWidth: "92%",
        boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
      }}
    >
      <div
        style={{
          width: "28px",
          height: "28px",
          borderRadius: "6px",
          background: "var(--surface-3)",
          color: badgeColor,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Icon size={16} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
          <strong style={{ fontWeight: 600 }}>{eventLabel}</strong>
          {title && <span style={{ color: "var(--subtle)" }}>· {title}</span>}
          {message.sender_name && (
            <span style={{ fontSize: "11px", color: "var(--subtle)" }}>
              ({message.sender_name})
            </span>
          )}
        </div>
        {date && !isCancelled && (
          <div style={{ fontSize: "12px", color: "var(--subtle)", marginTop: "2px" }}>
            {date}
          </div>
        )}
      </div>
      <time style={{ fontSize: "11px", color: "var(--subtle)", marginLeft: "auto", flexShrink: 0 }}>
        {stamp}
      </time>
    </div>
  );
}
