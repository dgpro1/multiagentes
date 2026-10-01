"use client";

import { FormEvent, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Building2,
  Clock,
  Copy,
  Download,
  ExternalLink,
  ImagePlus,
  Info,
  LoaderCircle,
  MapPin,
  Plus,
  Save,
  Trash2,
  User,
  X,
} from "lucide-react";
import { Alert, Modal } from "@/components/ui";
import { IndustryPicker, isBusinessComplete, type IndustryValue } from "@/components/industry-picker";
import { AiHint } from "@/components/ai-hint";
import { Combobox } from "@/components/combobox";
import { useToast } from "@/components/toast";
import { TIMEZONES } from "@/lib/timezones";
import { CURRENCIES } from "@/lib/currencies";
import { api, apiUrl, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import {
  DAYS,
  WEEKDAYS,
  MAX_RANGES,
  DEFAULT_RANGE,
  emptyHours,
  cloneRanges,
  cloneHours,
  scheduleError,
} from "@/lib/schedule";
import type { PortalClientDetails, TimeRange, WeekDay, WeeklyHours } from "@/types";

type DeletionPreview = {
  agents: number;
  channels: number;
  conversations: number;
  contacts: number;
  portal_users: number;
};

type Editable = PortalClientDetails & { is_active?: boolean };

type Props<T extends Editable> = {
  /** The client as last saved. */
  client: T;
  /** Called with the client as the server returned it after every save or logo change. */
  onChange: (client: T) => void;
} & (
  // The agency panel: every field, the active switch and deleting the client, on the agency's own routes.
  | { mode: "agency"; clientId: string }
  // The client's portal: logo, business, name and timezone only, on `/portal/{slug}/client`.
  | { mode: "portal"; slug: string }
);

function parseTimeToMinutes(t: string): number {
  if (!t) return 0;
  const [h, m] = t.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

function calculateRangeHours(range: TimeRange): number {
  const [start, end] = range;
  if (!start || !end) return 0;
  const startMins = parseTimeToMinutes(start);
  const endMins = parseTimeToMinutes(end);
  if (endMins <= startMins) return 0;
  return Math.round(((endMins - startMins) / 60) * 10) / 10;
}

function calculateDayHours(ranges: TimeRange[]): number {
  return ranges.reduce((acc, r) => acc + calculateRangeHours(r), 0);
}

function calculateWeeklyTotal(hours: WeeklyHours): number {
  return DAYS.reduce((acc, day) => acc + calculateDayHours(hours[day] ?? []), 0);
}

/** The client's own details form, styled in Stitch Warm Bento design. */
export function ClientDetails<T extends Editable>(props: Props<T>) {
  const { client, onChange } = props;
  const t = useT();
  const toast = useToast();
  const router = useRouter();
  const agency = props.mode === "agency";
  const apiBase = props.mode === "agency" ? `/clients/${props.clientId}` : `/portal/${props.slug}/client`;
  // A portal cannot read the agency's catalog; it has its own copy of it.
  const catalogPath = props.mode === "portal" ? `/portal/${props.slug}/industries` : undefined;

  const [business, setBusiness] = useState<IndustryValue>({
    industry: client.industry,
    businessType: client.business_type,
    custom: client.business_custom,
  });
  const [timezone, setTimezone] = useState(client.timezone || "UTC");
  const [currency, setCurrency] = useState(client.currency || "USD");
  const [address, setAddress] = useState(client.address ?? "");
  const [googleMapsUrl, setGoogleMapsUrl] = useState(client.google_maps_url ?? "");
  const [businessHours, setBusinessHours] = useState<WeeklyHours>(() => cloneHours(client.business_hours));
  const [busy, setBusy] = useState(false);
  const [logoVersion, setLogoVersion] = useState(0);
  const [isActive, setIsActive] = useState(client.is_active ?? true);
  const logoRef = useRef<HTMLInputElement>(null);
  const name = client.name;

  const setDayHours = (day: WeekDay, ranges: TimeRange[]) => {
    setBusinessHours((current) => ({ ...current, [day]: ranges }));
  };

  const applyStandardHours = () => {
    setBusinessHours({
      mon: [["09:00", "17:00"]],
      tue: [["09:00", "17:00"]],
      wed: [["09:00", "17:00"]],
      thu: [["09:00", "17:00"]],
      fri: [["09:00", "17:00"]],
      sat: [],
      sun: [],
    });
    toast.success("Horario estándar Lun-Vie (9-17h) aplicado.");
  };

  const copyToAllWeekdays = () => {
    const firstActive = DAYS.find((day) => (businessHours[day] ?? []).length > 0) ?? "mon";
    const sourceRanges = cloneRanges(businessHours[firstActive] ?? [["09:00", "17:00"]]);
    setBusinessHours((current) => {
      const hours = cloneHours(current);
      for (const weekday of WEEKDAYS) {
        hours[weekday] = cloneRanges(sourceRanges);
      }
      return hours;
    });
    toast.success("Horario replicado a todos los días laborables.");
  };

  async function uploadLogo(file?: File) {
    if (!file) return;
    setBusy(true);
    const data = new FormData();
    data.append("file", file);
    try {
      onChange(await api<T>(`${apiBase}/logo`, { method: "POST", body: data }));
      setLogoVersion((v) => v + 1);
      toast.success(t("clients.detail.logoUpdated"));
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusy(false);
      if (logoRef.current) logoRef.current.value = "";
    }
  }

  async function deleteLogo() {
    try {
      await api(`${apiBase}/logo`, { method: "DELETE" });
      setLogoVersion((v) => v + 1);
      onChange(await api<T>(apiBase));
    } catch (err) {
      toast.error(messageFrom(err));
    }
  }

  async function saveDetails(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const problem = scheduleError(businessHours, t);
    if (problem) {
      toast.error(problem);
      setBusy(false);
      return;
    }
    const weekly_hours: WeeklyHours = emptyHours();
    let hasAnyHours = false;
    for (const day of DAYS) {
      const sorted = [...(businessHours[day] ?? [])].sort((a, b) => a[0].localeCompare(b[0]));
      weekly_hours[day] = sorted;
      if (sorted.length > 0) hasAnyHours = true;
    }
    const data = new FormData(event.currentTarget);
    const body: Record<string, unknown> = {
      name: data.get("name"),
      industry: business.industry,
      business_type: business.businessType,
      business_custom: business.custom,
      timezone,
      owner_name: String(data.get("owner_name") ?? "").trim() || null,
      currency,
      address: address.trim() || null,
      google_maps_url: googleMapsUrl.trim() || null,
      business_hours: hasAnyHours ? weekly_hours : null,
    };
    if (agency) body.is_active = isActive;
    try {
      onChange(await api<T>(apiBase, { method: "PATCH", body: JSON.stringify(body) }));
      toast.success(t("clients.detail.detailsSaved"));
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  // Deletion preview modal state
  const [deletePreview, setDeletePreview] = useState<DeletionPreview | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteName, setDeleteName] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function openDelete() {
    setDeleteName("");
    setDeleteError(null);
    setDeletePreview(null);
    setDeleteOpen(true);
    try {
      setDeletePreview(await api<DeletionPreview>(`${apiBase}/deletion-preview`));
    } catch (err) {
      setDeleteError(messageFrom(err));
    }
  }

  async function remove() {
    if (deleteName.trim() !== name.trim()) return;
    setBusy(true);
    setDeleteError(null);
    try {
      await api(apiBase, { method: "DELETE" });
      toast.success(t("clients.detail.clientDeleted", { name }));
      router.push("/clients");
    } catch (err) {
      setDeleteError(messageFrom(err));
      setBusy(false);
    }
  }

  const logoSrc = client.logo_url
    ? `${client.logo_url}${client.logo_url.includes("?") ? "&" : "?"}r=${logoVersion}`
    : null;

  const totalWeeklyHours = Math.round(calculateWeeklyTotal(businessHours));
  const mapsDestinationUrl =
    googleMapsUrl ||
    (address.trim() ? `https://maps.google.com/?q=${encodeURIComponent(address)}` : "https://maps.google.com");

  // Address labels for map preview
  const addressParts = address.split(",").map((s) => s.trim()).filter(Boolean);
  const mapMainLabel = addressParts[0] || client.name || "Ubicación del negocio";
  const mapSubLabel =
    addressParts.length > 1
      ? addressParts.slice(1).join(", ")
      : address.trim()
        ? "Dirección configurada"
        : "Sin dirección física registrada";

  return (
    <>
      <form onSubmit={saveDetails} className="stitch-details-wrapper">
        <div className="stitch-details-layout">
          {/* ================= LEFT COLUMN: Perfil & Identidad del Negocio (5 cols) ================= */}
          <div className="stitch-details-col">
            {/* Card 1: Perfil Hero & Datos Principales */}
            <section className="stitch-details-card" aria-labelledby="client-info-title">
              <div className="stitch-details-card-header">
                <div className="stitch-details-card-title-group">
                  <h2 className="stitch-details-card-title" id="client-info-title">
                    {t("clients.detail.clientInfo")}
                  </h2>
                  <p className="stitch-details-card-subtitle">{t("clients.detail.clientInfoCopy")}</p>
                </div>
                <span className="stitch-verified-badge">
                  <span className="stitch-pulse-dot" />
                  Verificado
                </span>
              </div>

              {/* Integrated Logo & Brand Hero Header */}
              <div className="stitch-brand-hero-box">
                <div
                  className="stitch-logo-container"
                  onClick={() => logoRef.current?.click()}
                  title={t("clients.detail.logoChange")}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") logoRef.current?.click();
                  }}
                >
                  {logoSrc ? (
                    <img src={logoSrc} alt={t("clients.detail.logoAlt")} className="stitch-logo-img" />
                  ) : (
                    <ImagePlus size={24} style={{ color: "#00876c" }} />
                  )}
                  <span className="stitch-logo-badge" title={t("clients.detail.logoChange")}>
                    <Plus size={13} strokeWidth={3} />
                  </span>
                </div>

                <div className="stitch-brand-info">
                  <span className="stitch-brand-label">NOMBRE COMERCIAL</span>
                  <input
                    name="name"
                    required
                    defaultValue={client.name}
                    className="stitch-brand-input"
                    placeholder="Nombre del cliente o clínica"
                  />
                  <div className="stitch-brand-actions">
                    <button
                      type="button"
                      className="stitch-brand-action-btn"
                      onClick={() => logoRef.current?.click()}
                    >
                      {t("clients.detail.logoChange")} (PNG/SVG)
                    </button>
                    {client.logo_url && (
                      <button
                        type="button"
                        className="stitch-brand-action-btn danger"
                        onClick={deleteLogo}
                      >
                        <Trash2 size={13} /> {t("clients.detail.logoRemove")}
                      </button>
                    )}
                  </div>
                </div>
                <input
                  ref={logoRef}
                  hidden
                  type="file"
                  accept="image/png,image/jpeg,image/webp,image/svg+xml"
                  onChange={(e) => uploadLogo(e.target.files?.[0])}
                />
              </div>

              {/* Core Fields */}
              <IndustryPicker value={business} onChange={setBusiness} catalogPath={catalogPath} />

              {/* Responsable / Director */}
              <div className="stitch-field-group">
                <label className="stitch-field-label">
                  {t("clients.detail.ownerLabel")}
                </label>
                <div className="stitch-input-wrap">
                  <div className="stitch-input-icon">
                    <User size={15} />
                  </div>
                  <input
                    name="owner_name"
                    maxLength={160}
                    defaultValue={client.owner_name ?? ""}
                    placeholder={t("clients.detail.ownerPlaceholder")}
                    className="stitch-input-with-icon"
                  />
                </div>
                <span className="stitch-field-help">{t("clients.detail.ownerHint")}</span>
              </div>

              {/* Moneda & Zona Horaria */}
              <div className="stitch-form-grid-2">
                <div className="stitch-field-group">
                  <label className="stitch-field-label">{t("clients.detail.currencyLabel")}</label>
                  <select
                    name="currency"
                    value={currency}
                    onChange={(e) => setCurrency(e.target.value)}
                  >
                    {(CURRENCIES as readonly string[]).includes(currency) ? null : (
                      <option value={currency}>{currency}</option>
                    )}
                    {CURRENCIES.map((code) => (
                      <option key={code} value={code}>
                        {code}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="stitch-field-group">
                  <label className="stitch-field-label">{t("clients.detail.timezoneLabel")}</label>
                  <Combobox
                    value={timezone}
                    onChange={setTimezone}
                    options={TIMEZONES}
                    placeholder={t("clients.detail.timezoneLabel")}
                  />
                </div>
              </div>

              {/* Ciclo de vida / Cliente activo (Agency mode only) */}
              {agency && (
                <div className={`stitch-service-status-card ${isActive ? "" : "inactive"}`}>
                  <div className="stitch-service-status-info">
                    <div className="stitch-service-status-title-row">
                      <span className="stitch-service-status-title">Estado del servicio</span>
                      <span className="stitch-service-status-pill">
                        {isActive ? "Operativo" : "Inactivo"}
                      </span>
                    </div>
                    <span className="stitch-service-status-desc">
                      {isActive
                        ? "Agentes respondiendo normalmente."
                        : "Servicio pausado. Los agentes no responderán."}
                    </span>
                  </div>
                  <label className="stitch-switch-toggle" title="Cambiar estado del servicio">
                    <input
                      name="is_active"
                      type="checkbox"
                      checked={isActive}
                      onChange={(e) => setIsActive(e.target.checked)}
                      aria-label="Estado activo del cliente"
                    />
                    <span className="stitch-switch-slider" />
                  </label>
                </div>
              )}
            </section>

            {/* Card 2: Ubicación física */}
            <section className="stitch-details-card" aria-labelledby="location-title">
              <div className="stitch-details-card-header">
                <div className="stitch-details-card-header-left">
                  <div className="stitch-details-icon-badge">
                    <MapPin size={17} />
                  </div>
                  <div className="stitch-details-card-title-group">
                    <h2 className="stitch-details-card-title" id="location-title">
                      {t("clients.detail.locationSection")}
                    </h2>
                    <p className="stitch-details-card-subtitle">
                      {t("clients.detail.locationSectionCopy")}
                    </p>
                  </div>
                </div>

                <a
                  href={mapsDestinationUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="stitch-preset-btn highlight"
                  style={{ textDecoration: "none" }}
                >
                  <span>Abrir Maps</span>
                  <ExternalLink size={12} />
                </a>
              </div>

              {/* Dirección presencial */}
              <div className="stitch-field-group">
                <label className="stitch-field-label">{t("clients.detail.addressLabel")}</label>
                <div className="stitch-input-wrap">
                  <div className="stitch-input-icon top-align">
                    <Building2 size={16} />
                  </div>
                  <textarea
                    name="address"
                    maxLength={255}
                    rows={2}
                    value={address}
                    onChange={(e) => setAddress(e.target.value)}
                    placeholder={t("clients.detail.addressPlaceholder")}
                    className="stitch-input-with-icon"
                  />
                </div>
              </div>

              {/* Enlace directo Google Maps */}
              <div className="stitch-field-group">
                <label className="stitch-field-label">{t("clients.detail.googleMapsLabel")}</label>
                <input
                  name="google_maps_url"
                  type="url"
                  maxLength={500}
                  value={googleMapsUrl}
                  onChange={(e) => setGoogleMapsUrl(e.target.value)}
                  placeholder={t("clients.detail.googleMapsPlaceholder")}
                />
                <span className="stitch-field-help">{t("clients.detail.googleMapsHint")}</span>
              </div>

              {/* Micro Map Preview Mockup */}
              <a
                href={mapsDestinationUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="stitch-map-preview-card"
                title="Abrir en Google Maps"
              >
                <div className="stitch-map-pattern" />
                <div className="stitch-map-content">
                  <div className="stitch-map-pin">
                    <MapPin size={18} />
                  </div>
                  <span className="stitch-map-label-main">{mapMainLabel}</span>
                  <span className="stitch-map-label-sub">{mapSubLabel}</span>
                </div>
              </a>
            </section>
          </div>

          {/* ================= RIGHT COLUMN: Horarios de Atención (7 cols) ================= */}
          <div className="stitch-details-col">
            <section className="stitch-details-card" aria-labelledby="schedule-title">
              <div className="stitch-details-card-header">
                <div>
                  <div className="stitch-details-card-title">
                    <span>{t("clients.detail.businessHoursLabel")}</span>
                    <span className="stitch-hours-badge">{totalWeeklyHours} hrs / sem</span>
                  </div>
                  <p className="stitch-details-card-subtitle">
                    Franjas horarias activas en zona horaria {timezone}
                  </p>
                </div>

                {/* Quick Presets */}
                <div className="stitch-schedule-presets">
                  <button
                    type="button"
                    className="stitch-preset-btn"
                    onClick={applyStandardHours}
                    title="Aplicar horario Lun-Vie 9-17h"
                  >
                    <Clock size={13} />
                    <span>Lun-Vie 9–17h</span>
                  </button>
                  <button
                    type="button"
                    className="stitch-preset-btn highlight"
                    onClick={copyToAllWeekdays}
                    title="Copiar horario a todos los días de semana"
                  >
                    <Copy size={13} />
                    <span>Copiar a todos</span>
                  </button>
                </div>
              </div>

              {/* Weekdays (Lunes a Viernes) */}
              <div className="stitch-schedule-list">
                {WEEKDAYS.map((day) => {
                  const ranges = businessHours[day] ?? [];
                  const dayName = t(`professionals.daysLong.${day}`);
                  const dayShort = t(`professionals.daysShort.${day}`);
                  const dayHours = calculateDayHours(ranges);

                  return (
                    <div key={day} className="stitch-schedule-day-card">
                      <div
                        className="stitch-day-label-group"
                        onClick={() =>
                          setDayHours(day, ranges.length > 0 ? [] : [[...DEFAULT_RANGE]])
                        }
                      >
                        <input
                          type="checkbox"
                          checked={ranges.length > 0}
                          onChange={(e) =>
                            setDayHours(day, e.target.checked ? [[...DEFAULT_RANGE]] : [])
                          }
                          aria-label={t("professionals.form.dayToggle", { day: dayName })}
                          onClick={(e) => e.stopPropagation()}
                        />
                        <span className="stitch-day-name">
                          {dayName}
                          {ranges.length > 0 && <span className="stitch-day-dot" />}
                        </span>
                      </div>

                      <div className="stitch-ranges-group">
                        {ranges.length === 0 ? (
                          <span style={{ fontSize: "12px", color: "var(--muted)" }}>
                            {t("professionals.form.dayOff")}
                          </span>
                        ) : (
                          ranges.map((range, index) => (
                            <div key={index} className="stitch-time-range-row">
                              <div className="stitch-time-input-box">
                                <input
                                  type="time"
                                  required
                                  value={range[0]}
                                  className="stitch-time-input"
                                  aria-label={t("professionals.form.rangeStart", { day: dayName })}
                                  onChange={(e) =>
                                    setDayHours(
                                      day,
                                      ranges.map((item, at) =>
                                        at === index ? [e.target.value, item[1]] : item
                                      )
                                    )
                                  }
                                />
                                <span className="stitch-time-tag">IN</span>
                              </div>

                              <span className="stitch-range-arrow">→</span>

                              <div className="stitch-time-input-box">
                                <input
                                  type="time"
                                  required
                                  value={range[1]}
                                  className="stitch-time-input"
                                  aria-label={t("professionals.form.rangeEnd", { day: dayName })}
                                  onChange={(e) =>
                                    setDayHours(
                                      day,
                                      ranges.map((item, at) =>
                                        at === index ? [item[0], e.target.value] : item
                                      )
                                    )
                                  }
                                />
                                <span className="stitch-time-tag">OUT</span>
                              </div>
                              {ranges.length > 1 && (
                                <button
                                  type="button"
                                  className="stitch-day-btn danger"
                                  onClick={() => setDayHours(day, ranges.filter((_, at) => at !== index))}
                                  title={t("professionals.form.removeRange")}
                                  aria-label={t("professionals.form.removeRange")}
                                >
                                  <X size={14} />
                                </button>
                              )}
                            </div>
                          ))
                        )}
                      </div>

                      <div className="stitch-day-meta-actions">
                        {ranges.length > 0 && (
                          <span className="stitch-hours-badge">{dayHours} hrs</span>
                        )}
                        {ranges.length > 0 && ranges.length < MAX_RANGES && (
                          <button
                            type="button"
                            className="stitch-day-btn"
                            onClick={() => setDayHours(day, [...ranges, ["14:00", "18:00"]])}
                            title={t("professionals.form.addRange")}
                            aria-label={t("professionals.form.addRange")}
                          >
                            <Plus size={14} />
                          </button>
                        )}
                        {ranges.length === 1 && (
                          <button
                            type="button"
                            className="stitch-day-btn danger"
                            onClick={() => setDayHours(day, [])}
                            title={t("professionals.form.removeRange")}
                            aria-label={t("professionals.form.removeRange")}
                          >
                            <X size={14} />
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}

                {/* Weekend (Sábado y Domingo) */}
                <div className="stitch-weekend-grid">
                  {(["sat", "sun"] as const).map((day) => {
                    const ranges = businessHours[day] ?? [];
                    const dayName = t(`professionals.daysLong.${day}`);
                    const dayHours = calculateDayHours(ranges);

                    if (ranges.length === 0) {
                      return (
                        <div
                          key={day}
                          className="stitch-weekend-card"
                          onClick={() => setDayHours(day, [[...DEFAULT_RANGE]])}
                          style={{ cursor: "pointer" }}
                        >
                          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                            <input
                              type="checkbox"
                              checked={false}
                              onChange={(e) =>
                                setDayHours(day, e.target.checked ? [[...DEFAULT_RANGE]] : [])
                              }
                              onClick={(e) => e.stopPropagation()}
                              aria-label={t("professionals.form.dayToggle", { day: dayName })}
                            />
                            <span className="stitch-day-name" style={{ color: "var(--muted)" }}>
                              {dayName}
                            </span>
                          </div>
                          <span className="stitch-day-off-pill">
                            {t("professionals.form.dayOff")}
                          </span>
                        </div>
                      );
                    }

                    return (
                      <div key={day} className="stitch-schedule-day-card" style={{ gridColumn: "span 2" }}>
                        <div
                          className="stitch-day-label-group"
                          onClick={() => setDayHours(day, [])}
                        >
                          <input
                            type="checkbox"
                            checked={true}
                            onChange={(e) =>
                              setDayHours(day, e.target.checked ? [[...DEFAULT_RANGE]] : [])
                            }
                            onClick={(e) => e.stopPropagation()}
                            aria-label={t("professionals.form.dayToggle", { day: dayName })}
                          />
                          <span className="stitch-day-name">
                            {dayName}
                            <span className="stitch-day-dot" />
                          </span>
                        </div>

                        <div className="stitch-ranges-group">
                          {ranges.map((range, index) => (
                            <div key={index} className="stitch-time-range-row">
                              <div className="stitch-time-input-box">
                                <input
                                  type="time"
                                  required
                                  value={range[0]}
                                  className="stitch-time-input"
                                  aria-label={t("professionals.form.rangeStart", { day: dayName })}
                                  onChange={(e) =>
                                    setDayHours(
                                      day,
                                      ranges.map((item, at) =>
                                        at === index ? [e.target.value, item[1]] : item
                                      )
                                    )
                                  }
                                />
                                <span className="stitch-time-tag">IN</span>
                              </div>

                              <span className="stitch-range-arrow">→</span>

                              <div className="stitch-time-input-box">
                                <input
                                  type="time"
                                  required
                                  value={range[1]}
                                  className="stitch-time-input"
                                  aria-label={t("professionals.form.rangeEnd", { day: dayName })}
                                  onChange={(e) =>
                                    setDayHours(
                                      day,
                                      ranges.map((item, at) =>
                                        at === index ? [item[0], e.target.value] : item
                                      )
                                    )
                                  }
                                />
                                <span className="stitch-time-tag">OUT</span>
                              </div>
                              {ranges.length > 1 && (
                                <button
                                  type="button"
                                  className="stitch-day-btn danger"
                                  onClick={() => setDayHours(day, ranges.filter((_, at) => at !== index))}
                                  title={t("professionals.form.removeRange")}
                                  aria-label={t("professionals.form.removeRange")}
                                >
                                  <X size={14} />
                                </button>
                              )}
                            </div>
                          ))}
                        </div>

                        <div className="stitch-day-meta-actions">
                          <span className="stitch-hours-badge">{dayHours} hrs</span>
                          {ranges.length === 1 && (
                            <button
                              type="button"
                              className="stitch-day-btn danger"
                              onClick={() => setDayHours(day, [])}
                              title={t("professionals.form.removeRange")}
                              aria-label={t("professionals.form.removeRange")}
                            >
                              <X size={14} />
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Callout Info Note */}
              <div className="stitch-operational-callout">
                <Info size={17} className="stitch-operational-icon" />
                <p className="stitch-operational-text">
                  Los asistentes IA informarán al paciente si la clínica se encuentra abierta o
                  cerrada al momento de la conversación, sugiriendo citas dentro de estas franjas.
                </p>
              </div>
            </section>
          </div>
        </div>

        {/* Sticky Bottom Actions Bar */}
        <footer className="stitch-details-sticky-footer">
          <div className="stitch-details-sticky-footer-inner">
            {agency ? (
              <button
                type="button"
                className="stitch-btn-danger-outline"
                onClick={openDelete}
              >
                <Trash2 size={15} />
                <span>{t("clients.detail.deleteClient")}</span>
              </button>
            ) : (
              <div />
            )}

            <button
              type="submit"
              className="stitch-btn-save-primary"
              disabled={busy || !isBusinessComplete(business)}
            >
              {busy ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}
              <span>{t("clients.detail.saveChanges")}</span>
            </button>
          </div>
        </footer>
      </form>

      {/* Delete Client Confirmation Modal */}
      {agency && (
        <Modal
          open={deleteOpen}
          title={t("clients.detail.deleteTitle", { name })}
          onClose={() => setDeleteOpen(false)}
        >
          <div className="modal-form">
            <p className="modal-copy">{t("clients.detail.deleteCopy")}</p>
            {deletePreview ? (
              <ul className="deletion-list">
                <li>
                  <strong>{deletePreview.agents}</strong> {t("clients.detail.deleteCountAgents")}
                </li>
                <li>
                  <strong>{deletePreview.channels}</strong> {t("clients.detail.deleteCountChannels")}
                </li>
                <li>
                  <strong>{deletePreview.conversations}</strong>{" "}
                  {t("clients.detail.deleteCountConversations")}
                </li>
                <li>
                  <strong>{deletePreview.contacts}</strong> {t("clients.detail.deleteCountContacts")}
                </li>
                <li>
                  <strong>{deletePreview.portal_users}</strong>{" "}
                  {t("clients.detail.deleteCountPortalUsers")}
                </li>
              </ul>
            ) : (
              !deleteError && (
                <p className="field-help">
                  <LoaderCircle className="spin" size={14} />
                </p>
              )
            )}
            <div className="delete-export">
              <p className="field-help">{t("clients.detail.deleteExportCopy")}</p>
              <a
                className="button secondary small"
                href={apiUrl(`${apiBase}/export`)}
                download
              >
                <Download size={15} /> {t("clients.detail.deleteExport")}
              </a>
            </div>
            <label>
              {t("clients.detail.deleteTypeName", { name })}
              <input
                value={deleteName}
                onChange={(e) => setDeleteName(e.target.value)}
                autoComplete="off"
                placeholder={name}
              />
            </label>
            {deleteError && <Alert>{deleteError}</Alert>}
            <div className="modal-actions">
              <button
                type="button"
                className="button"
                onClick={() => setDeleteOpen(false)}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="button danger"
                disabled={busy || !deletePreview || deleteName.trim() !== name.trim()}
                onClick={remove}
              >
                {busy ? (
                  <LoaderCircle className="spin" size={16} />
                ) : (
                  <>
                    <Trash2 size={15} /> {t("clients.detail.deleteClient")}
                  </>
                )}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

