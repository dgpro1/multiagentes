"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Clock, Copy, LoaderCircle, Pencil, Plus, Stethoscope, Trash2, X } from "lucide-react";
import { Alert, EmptyState, Modal } from "@/components/ui";
import { useToast } from "@/components/toast";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Professional, Service, TimeRange, WeekDay, WeeklyHours } from "@/types";

import { DAYS, WEEKDAYS, MAX_RANGES, DEFAULT_RANGE, emptyHours, cloneRanges, cloneHours, scheduleError, summarizeHours } from "@/lib/schedule";

const SLOT_OPTIONS = [10, 15, 20, 30, 45, 60, 90, 120] as const;
const DEFAULT_SLOT = 30;
const PALETTE = ["#2563eb", "#7c3aed", "#db2777", "#dc2626", "#ea580c", "#ca8a04", "#16a34a", "#0d9488", "#0891b2", "#475569"] as const;

type Draft = { name: string; role: string; color: string; isActive: boolean; slotMinutes: number; hours: WeeklyHours; serviceIds: string[] };

/** The palette color not yet taken by another professional, or the next one in turn. */
function nextColor(existing: Professional[]): string {
  const used = new Set(existing.map((item) => item.color.toLowerCase()));
  return PALETTE.find((color) => !used.has(color)) ?? PALETTE[existing.length % PALETTE.length];
}

/** A client's professionals with their weekly hours. `apiBase` is the API prefix the rows live under: the portal's
 * own (`/portal/{slug}`) or the agency's client page (`/clients/{id}`). Without `canManage` the list is read-only.
 * `timezone` is only shown, as the zone the hours are read in. */
export function ProfessionalsView({ apiBase, canManage, timezone }: { apiBase: string; canManage: boolean; timezone?: string | null }) {
  const t = useT();
  const toast = useToast();
  const [items, setItems] = useState<Professional[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Professional | "new" | null>(null);
  const [deleting, setDeleting] = useState<Professional | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);

  const load = useCallback(async () => {
    const [pros, srvs] = await Promise.all([
      api<Professional[]>(`${apiBase}/professionals`),
      api<Service[]>(`${apiBase}/services`).catch(() => [] as Service[]),
    ]);
    setItems(pros);
    setServices(srvs);
  }, [apiBase]);
  useEffect(() => {
    setLoading(true);
    load().catch((err) => setError(messageFrom(err))).finally(() => setLoading(false));
  }, [load]);

  function openEditor(target: Professional | "new") {
    setError("");
    setEditing(target);
    setDraft(target === "new"
      ? { name: "", role: "", color: nextColor(items), isActive: true, slotMinutes: DEFAULT_SLOT, hours: emptyHours(), serviceIds: [] }
      : { name: target.name, role: target.role ?? "", color: target.color, isActive: target.is_active, slotMinutes: target.slot_minutes, hours: cloneHours(target.weekly_hours), serviceIds: target.service_ids ?? [] });
  }
  const closeEditor = () => { setEditing(null); setDraft(null); };
  const patch = (changes: Partial<Draft>) => setDraft((current) => (current ? { ...current, ...changes } : current));
  const setDay = (day: WeekDay, ranges: TimeRange[]) => setDraft((current) => (current ? { ...current, hours: { ...current.hours, [day]: ranges } } : current));

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft || !editing) return;
    const name = draft.name.trim();
    if (!name) { setError(t("professionals.errors.nameRequired")); return; }
    const problem = scheduleError(draft.hours, t);
    if (problem) { setError(problem); return; }
    const weekly_hours = emptyHours();
    for (const day of DAYS) weekly_hours[day] = [...draft.hours[day]].sort((a, b) => a[0].localeCompare(b[0]));
    const body = { name, role: draft.role.trim() || null, color: draft.color, is_active: draft.isActive, slot_minutes: draft.slotMinutes, weekly_hours, service_ids: draft.serviceIds };
    setBusy(true); setError("");
    try {
      if (editing === "new") await api<Professional>(`${apiBase}/professionals`, { method: "POST", body: JSON.stringify(body) });
      else await api<Professional>(`${apiBase}/professionals/${editing.id}`, { method: "PATCH", body: JSON.stringify(body) });
      toast.success(t("professionals.saved"));
      closeEditor();
      await load();
    } catch (err) {
      setError(messageFrom(err));
    } finally { setBusy(false); }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true); setError("");
    try {
      await api(`${apiBase}/professionals/${deleting.id}`, { method: "DELETE" });
      toast.success(t("professionals.deleted"));
      setDeleting(null);
      await load();
    } catch (err) { setError(messageFrom(err)); } finally { setBusy(false); }
  }

  const firstOnDay = draft ? DAYS.find((day) => draft.hours[day].length > 0) : undefined;

  return <>
    <div className="professionals-view">
      <div className="stitch-section-header">
        <div>
          <h2>{t("professionals.title")}</h2>
          <p>{t("professionals.count", { count: items.length })}{timezone ? ` · ${t("professionals.timezoneNote", { timezone })}` : ""}</p>
        </div>
        {canManage && (
          <button type="button" className="stitch-action-pill" onClick={() => openEditor("new")}>
            <Plus size={16} /> <span>{t("professionals.add")}</span>
          </button>
        )}
      </div>
      {error && !editing && !deleting && <Alert>{error}</Alert>}
      {loading ? (
        <div className="page-loading"><LoaderCircle className="spin" size={24} /></div>
      ) : items.length ? (
        <ul className="stitch-pros-grid">
          {items.map((pro) => (
            <li key={pro.id} className={`stitch-pro-card${pro.is_active ? "" : " inactive"}`}>
              <div className="stitch-pro-card-head">
                <div className="stitch-pro-card-lead">
                  <div className="stitch-pro-avatar" style={{ borderColor: pro.color, color: pro.color }}>
                    <Stethoscope size={20} />
                  </div>
                  <div className="stitch-pro-title-col">
                    <div className="stitch-pro-name-row">
                      <h3 className="stitch-pro-name">{pro.name}</h3>
                      <span className={pro.is_active ? "stitch-badge-active" : "stitch-badge-inactive"}>
                        {pro.is_active && <span className="stitch-badge-active-dot" />}
                        {pro.is_active ? t("professionals.active") : t("professionals.inactive")}
                      </span>
                    </div>
                    {pro.role && <p className="stitch-pro-role">{pro.role}</p>}
                  </div>
                </div>

                {canManage && (
                  <div className="stitch-card-actions">
                    <button
                      type="button"
                      className="stitch-icon-btn"
                      onClick={() => openEditor(pro)}
                      title={t("professionals.edit")}
                      aria-label={t("professionals.edit")}
                    >
                      <Pencil size={15} />
                    </button>
                    <button
                      type="button"
                      className="stitch-icon-btn danger"
                      onClick={() => {
                        setError("");
                        setDeleting(pro);
                      }}
                      title={t("professionals.delete")}
                      aria-label={t("professionals.delete")}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                )}
              </div>

              <div className="stitch-pro-schedule-box">
                <div className="stitch-pro-hours-row">
                  <Clock size={13} />
                  <span>{summarizeHours(pro.weekly_hours, t)}</span>
                </div>
                <div className="stitch-pro-slot-badge">
                  {t("professionals.slotSummary", { minutes: pro.slot_minutes })}
                </div>
              </div>

              {services.length > 0 && (
                <div className="stitch-pro-services-row">
                  {pro.service_ids && pro.service_ids.length > 0 ? (
                    services
                      .filter((s) => pro.service_ids?.includes(s.id))
                      .map((s) => (
                        <span key={s.id} className="stitch-service-pill">
                          {s.name}
                        </span>
                      ))
                  ) : (
                    <span className="stitch-service-pill muted">
                      {t("professionals.allServices")}
                    </span>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          icon={<Stethoscope />}
          title={t("professionals.emptyTitle")}
          description={canManage ? t("professionals.emptyDescription") : t("professionals.emptyReadOnly")}
          action={
            canManage ? (
              <button type="button" className="stitch-action-pill" onClick={() => openEditor("new")}>
                <Plus size={16} /> <span>{t("professionals.add")}</span>
              </button>
            ) : undefined
          }
        />
      )}
    </div>

    <Modal open={editing !== null} title={editing === "new" ? t("professionals.form.newTitle") : t("professionals.form.editTitle")} onClose={closeEditor}>
      {draft && <form className="modal-form" onSubmit={save}>
        <div className="form-grid">
          <label>{t("professionals.form.name")}<input value={draft.name} required maxLength={160} onChange={(e) => patch({ name: e.target.value })} placeholder={t("professionals.form.namePlaceholder")} autoFocus /></label>
          <label>{t("professionals.form.role")}<input value={draft.role} maxLength={160} onChange={(e) => patch({ role: e.target.value })} placeholder={t("professionals.form.rolePlaceholder")} /></label>
        </div>
        <div className="professionals-field">
          <span className="professionals-label">{t("professionals.form.color")}</span>
          <div className="professionals-swatches">
            {PALETTE.map((color) => <button type="button" key={color} className={`professionals-swatch${draft.color.toLowerCase() === color ? " active" : ""}`} style={{ background: color }} aria-pressed={draft.color.toLowerCase() === color} aria-label={t("professionals.form.colorOption", { color })} title={color} onClick={() => patch({ color })} />)}
          </div>
        </div>
        <div className="form-grid">
          <label>{t("professionals.form.slot")}
            <select value={draft.slotMinutes} onChange={(e) => patch({ slotMinutes: Number(e.target.value) })}>
              {SLOT_OPTIONS.map((minutes) => <option key={minutes} value={minutes}>{t("professionals.form.slotOption", { minutes })}</option>)}
            </select>
          </label>
          <label className="switch-row"><span><strong>{t("professionals.form.active")}</strong><small>{t("professionals.form.activeHint")}</small></span><input type="checkbox" checked={draft.isActive} onChange={(e) => patch({ isActive: e.target.checked })} /></label>
        </div>
        {services.length > 0 && (
          <div className="professionals-field">
            <span className="professionals-label">{t("professionals.servicesAssigned")}</span>
            <small className="field-help">{t("professionals.servicesAssignedHint")}</small>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "6px" }}>
              {services.map((srv) => {
                const checked = draft.serviceIds.includes(srv.id);
                return (
                  <button
                    type="button"
                    key={srv.id}
                    className={`button small ${checked ? "primary" : "secondary"}`}
                    onClick={() => {
                      patch({
                        serviceIds: checked
                          ? draft.serviceIds.filter((id) => id !== srv.id)
                          : [...draft.serviceIds, srv.id],
                      });
                    }}
                    style={{ borderRadius: "20px", fontSize: "12px", padding: "4px 10px" }}
                  >
                    {srv.name} {srv.duration_minutes ? `(${srv.duration_minutes}m)` : ""}
                  </button>
                );
              })}
            </div>
          </div>
        )}
        <div className="professionals-field">
          <span className="professionals-label">{t("professionals.form.schedule")}</span>
          <small className="field-help">{t("professionals.form.scheduleHint")}{timezone ? ` ${t("professionals.timezoneNote", { timezone })}` : ""}</small>
          <div className="professionals-schedule">
            {DAYS.map((day) => {
              const ranges = draft.hours[day];
              const dayName = t(`professionals.daysLong.${day}`);
              return <div key={day} className="professionals-day">
                <label className="professionals-day-name"><input type="checkbox" checked={ranges.length > 0} aria-label={t("professionals.form.dayToggle", { day: dayName })} onChange={(e) => setDay(day, e.target.checked ? [[...DEFAULT_RANGE]] : [])} />{t(`professionals.daysShort.${day}`)}</label>
                <div className="professionals-ranges">
                  {ranges.length === 0 && <span className="muted">{t("professionals.form.dayOff")}</span>}
                  {ranges.map((range, index) => <div key={index} className="professionals-range">
                    <input type="time" required value={range[0]} aria-label={t("professionals.form.rangeStart", { day: dayName })} onChange={(e) => setDay(day, ranges.map((item, at) => (at === index ? [e.target.value, item[1]] : item)))} />
                    <span aria-hidden="true">–</span>
                    <input type="time" required value={range[1]} aria-label={t("professionals.form.rangeEnd", { day: dayName })} onChange={(e) => setDay(day, ranges.map((item, at) => (at === index ? [item[0], e.target.value] : item)))} />
                    <button type="button" className="icon-button" onClick={() => setDay(day, ranges.filter((_, at) => at !== index))} title={t("professionals.form.removeRange")} aria-label={t("professionals.form.removeRange")}><X size={14} /></button>
                  </div>)}
                </div>
                <div className="professionals-day-actions">
                  {ranges.length > 0 && ranges.length < MAX_RANGES && <button type="button" className="icon-button" onClick={() => setDay(day, [...ranges, ["14:00", "18:00"]])} title={t("professionals.form.addRange")} aria-label={t("professionals.form.addRange")}><Plus size={14} /></button>}
                  {day === firstOnDay && <button type="button" className="icon-button" onClick={() => setDraft((current) => { if (!current) return current; const hours = cloneHours(current.hours); for (const weekday of WEEKDAYS) hours[weekday] = cloneRanges(current.hours[day]); return { ...current, hours }; })} title={t("professionals.form.copyToWeekdaysTitle")} aria-label={t("professionals.form.copyToWeekdays")}><Copy size={14} /></button>}
                </div>
              </div>;
            })}
          </div>
        </div>
        {error && <Alert>{error}</Alert>}
        <div className="modal-actions"><button type="button" className="button" onClick={closeEditor}>{t("common.cancel")}</button><button className="button primary" disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : t("professionals.form.save")}</button></div>
      </form>}
    </Modal>

    <Modal open={deleting !== null} title={t("professionals.deleteTitle", { name: deleting?.name ?? "" })} description={t("professionals.deleteDescription")} onClose={() => setDeleting(null)}>
      <div className="modal-form">
        {error && <Alert>{error}</Alert>}
        <div className="modal-actions"><button type="button" className="button" onClick={() => setDeleting(null)}>{t("common.cancel")}</button><button type="button" className="button danger" disabled={busy} onClick={remove}>{busy ? <LoaderCircle className="spin" size={16} /> : <><Trash2 size={15} /> {t("professionals.deleteConfirm")}</>}</button></div>
      </div>
    </Modal>
  </>;
}
