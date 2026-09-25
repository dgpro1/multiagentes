"use client";

import { useEffect, useRef, useState } from "react";
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight } from "lucide-react";
import { useLanguage } from "@/lib/i18n";

interface CalendarPickerProps {
  value: string; // "YYYY-MM-DD"
  onChange: (value: string) => void;
  min?: string; // "YYYY-MM-DD"
  max?: string;
  disabled?: boolean;
}

const WEEKDAYS_ES = ["Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"];
const WEEKDAYS_EN = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

const MONTHS_ES = [
  "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
  "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
];
const MONTHS_EN = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"
];

export function CalendarPicker({ value, onChange, min, max, disabled }: CalendarPickerProps) {
  const { lang } = useLanguage();
  const isEs = lang === "es";
  const weekdays = isEs ? WEEKDAYS_ES : WEEKDAYS_EN;
  const months = isEs ? MONTHS_ES : MONTHS_EN;

  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Parse current selected date
  const [selectedYear, selectedMonth] = value
    ? value.split("-").map(Number)
    : [new Date().getFullYear(), new Date().getMonth() + 1];

  // View year and month (for pagination)
  const [viewYear, setViewYear] = useState<number>(selectedYear);
  const [viewMonth, setViewMonth] = useState<number>(selectedMonth - 1); // 0-indexed

  // Keep view aligned with value when value changes externally
  useEffect(() => {
    if (value) {
      const [y, m] = value.split("-").map(Number);
      setViewYear(y);
      setViewMonth(m - 1);
    }
  }, [value]);

  // Close popup when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [open]);

  // Navigate months
  function prevMonth() {
    if (viewMonth === 0) {
      setViewYear((y) => y - 1);
      setViewMonth(11);
    } else {
      setViewMonth((m) => m - 1);
    }
  }

  function nextMonth() {
    if (viewMonth === 11) {
      setViewYear((y) => y + 1);
      setViewMonth(0);
    } else {
      setViewMonth((m) => m + 1);
    }
  }

  // Format date helper: "YYYY-MM-DD"
  function toYMD(year: number, month: number, day: number): string {
    const mm = String(month + 1).padStart(2, "0");
    const dd = String(day).padStart(2, "0");
    return `${year}-${mm}-${dd}`;
  }

  // Today string
  const now = new Date();
  const todayYMD = toYMD(now.getFullYear(), now.getMonth(), now.getDate());

  // Format display label
  function formatDisplayDate(val: string): string {
    if (!val) return isEs ? "Seleccionar fecha" : "Select date";
    const [y, m, d] = val.split("-").map(Number);
    const dateObj = new Date(y, m - 1, d);
    return dateObj.toLocaleDateString(isEs ? "es-ES" : "en-US", {
      weekday: "short",
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  // Generate calendar grid days
  // First day of current view month: Monday = 0, Sunday = 6
  const firstDayOfMonth = new Date(viewYear, viewMonth, 1);
  const startDayOfWeek = (firstDayOfMonth.getDay() + 6) % 7; // Convert Sun=0 to Mon=0

  const daysInCurrentMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
  const daysInPrevMonth = new Date(viewYear, viewMonth, 0).getDate();

  const calendarDays: Array<{
    day: number;
    month: number;
    year: number;
    isCurrentMonth: boolean;
    ymd: string;
    isDisabled: boolean;
    isSelected: boolean;
    isToday: boolean;
  }> = [];

  // Previous month trailing days
  for (let i = startDayOfWeek - 1; i >= 0; i--) {
    const d = daysInPrevMonth - i;
    const m = viewMonth === 0 ? 11 : viewMonth - 1;
    const y = viewMonth === 0 ? viewYear - 1 : viewYear;
    const ymd = toYMD(y, m, d);
    calendarDays.push({
      day: d,
      month: m,
      year: y,
      isCurrentMonth: false,
      ymd,
      isDisabled: Boolean(min && ymd < min) || Boolean(max && ymd > max),
      isSelected: ymd === value,
      isToday: ymd === todayYMD,
    });
  }

  // Current month days
  for (let d = 1; d <= daysInCurrentMonth; d++) {
    const ymd = toYMD(viewYear, viewMonth, d);
    calendarDays.push({
      day: d,
      month: viewMonth,
      year: viewYear,
      isCurrentMonth: true,
      ymd,
      isDisabled: Boolean(min && ymd < min) || Boolean(max && ymd > max),
      isSelected: ymd === value,
      isToday: ymd === todayYMD,
    });
  }

  // Next month leading days (fill up to 35 or 42 cells)
  const remaining = (7 - (calendarDays.length % 7)) % 7;
  for (let d = 1; d <= remaining; d++) {
    const m = viewMonth === 11 ? 0 : viewMonth + 1;
    const y = viewMonth === 11 ? viewYear + 1 : viewYear;
    const ymd = toYMD(y, m, d);
    calendarDays.push({
      day: d,
      month: m,
      year: y,
      isCurrentMonth: false,
      ymd,
      isDisabled: Boolean(min && ymd < min) || Boolean(max && ymd > max),
      isSelected: ymd === value,
      isToday: ymd === todayYMD,
    });
  }

  function handleSelect(ymd: string, isDisabled: boolean) {
    if (isDisabled || disabled) return;
    onChange(ymd);
    setOpen(false);
  }

  return (
    <div ref={containerRef} style={{ position: "relative", width: "100%" }}>
      {/* Trigger Button */}
      <button
        type="button"
        className="input"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          cursor: disabled ? "not-allowed" : "pointer",
          width: "100%",
          padding: "8px 12px",
          borderRadius: "8px",
          border: open ? "1px solid var(--accent)" : "1px solid var(--line)",
          background: "var(--surface)",
          color: value ? "var(--ink)" : "var(--muted)",
          fontSize: "14px",
          textAlign: "left",
          outline: "none",
          transition: "all 0.15s ease",
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <CalendarIcon size={16} style={{ color: "var(--accent)" }} />
          <span style={{ fontWeight: 500 }}>{formatDisplayDate(value)}</span>
        </span>
        <span style={{ fontSize: "12px", color: "var(--muted)", fontWeight: 500 }}>
          {value || todayYMD}
        </span>
      </button>

      {/* Popover Calendar Grid */}
      {open && (
        <div
          className="calendar-picker-popover"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            zIndex: 1000,
            width: "300px",
            padding: "14px",
            background: "var(--surface-2)",
            border: "1px solid var(--line)",
            borderRadius: "12px",
            boxShadow: "0 10px 30px rgba(0, 0, 0, 0.5)",
            animation: "fadeIn 0.15s ease",
          }}
        >
          {/* Header Navigation */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: "12px",
            }}
          >
            <span style={{ fontWeight: 600, fontSize: "14px", color: "var(--ink)" }}>
              {months[viewMonth]} {viewYear}
            </span>
            <div style={{ display: "flex", gap: "4px" }}>
              <button
                type="button"
                className="icon-button"
                onClick={prevMonth}
                style={{ padding: "4px", borderRadius: "6px", color: "var(--ink)" }}
                aria-label="Previous month"
              >
                <ChevronLeft size={16} />
              </button>
              <button
                type="button"
                className="icon-button"
                onClick={nextMonth}
                style={{ padding: "4px", borderRadius: "6px", color: "var(--ink)" }}
                aria-label="Next month"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>

          {/* Weekday headers */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(7, 1fr)",
              textAlign: "center",
              fontSize: "11px",
              fontWeight: 600,
              color: "var(--muted)",
              marginBottom: "6px",
            }}
          >
            {weekdays.map((w, idx) => (
              <div key={idx} style={{ padding: "4px 0" }}>
                {w}
              </div>
            ))}
          </div>

          {/* Days Grid */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(7, 1fr)",
              gap: "2px",
              rowGap: "4px",
            }}
          >
            {calendarDays.map((cd, index) => {
              const bg = cd.isSelected
                ? "var(--accent)"
                : cd.isToday
                ? "var(--surface-4)"
                : "transparent";
              const color = cd.isSelected
                ? "#ffffff"
                : cd.isDisabled
                ? "var(--faint)"
                : !cd.isCurrentMonth
                ? "var(--subtle)"
                : "var(--ink)";
              const fontWeight = cd.isSelected || cd.isToday ? 600 : 400;

              return (
                <button
                  key={index}
                  type="button"
                  disabled={cd.isDisabled}
                  onClick={() => handleSelect(cd.ymd, cd.isDisabled)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    height: "32px",
                    borderRadius: "8px",
                    border: cd.isToday && !cd.isSelected ? "1px solid var(--accent)" : "1px solid transparent",
                    background: bg,
                    color: color,
                    fontWeight: fontWeight,
                    fontSize: "13px",
                    cursor: cd.isDisabled ? "not-allowed" : "pointer",
                    opacity: cd.isDisabled ? 0.35 : !cd.isCurrentMonth ? 0.5 : 1,
                    transition: "all 0.12s ease",
                  }}
                  onMouseEnter={(e) => {
                    if (!cd.isDisabled && !cd.isSelected) {
                      e.currentTarget.style.background = "var(--surface-3)";
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!cd.isDisabled && !cd.isSelected) {
                      e.currentTarget.style.background = cd.isToday ? "var(--surface-4)" : "transparent";
                    }
                  }}
                >
                  {cd.day}
                </button>
              );
            })}
          </div>

          {/* Quick Actions Footer */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginTop: "12px",
              paddingTop: "10px",
              borderTop: "1px solid var(--line)",
              fontSize: "12px",
            }}
          >
            <button
              type="button"
              className="button small"
              onClick={() => {
                onChange(todayYMD);
                const [y, m] = todayYMD.split("-").map(Number);
                setViewYear(y);
                setViewMonth(m - 1);
                setOpen(false);
              }}
              style={{ padding: "3px 8px", fontSize: "12px" }}
            >
              {isEs ? "Hoy" : "Today"}
            </button>
            <button
              type="button"
              className="button secondary small"
              onClick={() => setOpen(false)}
              style={{ padding: "3px 8px", fontSize: "12px" }}
            >
              {isEs ? "Cerrar" : "Close"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
