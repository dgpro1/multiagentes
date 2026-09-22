"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Calendar as CalendarIcon, ChevronLeft, ChevronRight, Copy, Link2, LoaderCircle, Pencil, Plug, Save, Trash2, UserPlus, UserRound } from "lucide-react";
import { Alert, EmptyState, Modal } from "@/components/ui";
import { ListRowsSkeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { CalendarEvent, CalendarEventsResult, CalendarMember, CalendarOverview } from "@/types";

/** Monday..Sunday of the week containing `date`, in the browser's own timezone. */
function weekStart(date: Date): Date {
  const start = new Date(date);
  const day = (start.getDay() + 6) % 7; // Monday = 0
  start.setDate(start.getDate() - day);
  start.setHours(0, 0, 0, 0);
  return start;
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

/** The client's calendar tab: who is on it, and a week of their combined
 * events. Shared by the agency's client page and the client portal — `base`
 * is whichever REST prefix owns the client (`/clients/{id}` or
 * `/portal/{slug}`), and `canManage` gates adding, editing and removing
 * people (viewing the calendar itself is always free). */
export function CalendarView({ base, canManage }: { base: string; canManage: boolean }) {
  const t = useT();
  const toast = useToast();
  const [overview, setOverview] = useState<CalendarOverview | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setOverview(await api<CalendarOverview>(`${base}/calendar`)); }
    catch (err) { setError(messageFrom(err)); }
  }, [base]);
  useEffect(() => { load(); }, [load]);

  const [editing, setEditing] = useState<CalendarMember | "new" | null>(null);
  const [renewing, setRenewing] = useState<CalendarMember | null>(null);
  const [disconnecting, setDisconnecting] = useState<CalendarMember | null>(null);
  const [removing, setRemoving] = useState<CalendarMember | null>(null);
  const [busy, setBusy] = useState(false);

  function upsertMember(member: CalendarMember) {
    setOverview((current) => current && {
      ...current,
      members: current.members.some((m) => m.id === member.id)
        ? current.members.map((m) => (m.id === member.id ? member : m))
        : [...current.members, member],
    });
  }

  async function saveMember(name: string, role: string, color: string) {
    setBusy(true);
    try {
      const member = editing && editing !== "new"
        ? await api<CalendarMember>(`${base}/calendar/members/${editing.id}`, { method: "PATCH", body: JSON.stringify({ name, role }) })
        : await api<CalendarMember>(`${base}/calendar/members`, { method: "POST", body: JSON.stringify({ name, role, color }) });
      upsertMember(member);
      setEditing(null);
      toast.success(editing === "new" ? t("calendar.memberAdded") : t("calendar.memberSaved"));
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }

  async function copyLink(member: CalendarMember) {
    try { await navigator.clipboard.writeText(member.connect_url); toast.success(t("calendar.linkCopied")); }
    catch { toast.error(member.connect_url); }
  }

  async function renewLink() {
    if (!renewing) return;
    setBusy(true);
    try { upsertMember(await api<CalendarMember>(`${base}/calendar/members/${renewing.id}/renew-link`, { method: "POST" })); setRenewing(null); }
    catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }

  async function disconnect() {
    if (!disconnecting) return;
    setBusy(true);
    try { upsertMember(await api<CalendarMember>(`${base}/calendar/members/${disconnecting.id}/disconnect`, { method: "POST" })); setDisconnecting(null); }
    catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      await api(`${base}/calendar/members/${removing.id}`, { method: "DELETE" });
      setOverview((current) => current && { ...current, members: current.members.filter((m) => m.id !== removing.id) });
      setRemoving(null);
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }

  if (error) return <Alert>{error}</Alert>;
  if (!overview) return <ListRowsSkeleton rows={3} />;

  return <div className="calendar-view">
    <section className="form-section">
      <div className="section-copy"><h2>{t("calendar.title")}</h2><p>{t("calendar.description")}</p></div>
      <div className="form-fields">
        {!overview.oauth_ready && <Alert type="info">{t("calendar.notConfigured")}</Alert>}
        {overview.members.length === 0
          ? <EmptyState icon={<CalendarIcon />} title={t("calendar.membersEmptyTitle")} description={t("calendar.membersEmptyDescription")}
              action={canManage && <button type="button" className="button primary" onClick={() => setEditing("new")}><UserPlus size={16} /> {t("calendar.addMember")}</button>} />
          : <>
            <div className="calendar-member-grid">
              {overview.members.map((member) => <CalendarMemberCard key={member.id} member={member} canManage={canManage}
                onEdit={() => setEditing(member)} onCopy={() => copyLink(member)} onRenew={() => setRenewing(member)}
                onDisconnect={() => setDisconnecting(member)} onRemove={() => setRemoving(member)} />)}
            </div>
            {canManage && <button type="button" className="button secondary align-start" onClick={() => setEditing("new")}><UserPlus size={15} /> {t("calendar.addMember")}</button>}
          </>}
      </div>
    </section>

    {overview.members.length > 0 && <CalendarWeek base={base} members={overview.members} />}

    <Modal open={editing !== null} title={editing === "new" ? t("calendar.addMemberTitle") : t("calendar.editMemberTitle", { name: editing?.name || "" })}
      description={editing === "new" ? t("calendar.addMemberCopy") : undefined} onClose={() => setEditing(null)}>
      <MemberForm key={editing === "new" ? "new" : editing?.id} member={editing !== "new" ? editing : null} busy={busy}
        onCancel={() => setEditing(null)} onSave={saveMember} isNew={editing === "new"} />
    </Modal>

    <Modal open={renewing !== null} title={t("calendar.renewLinkConfirmTitle", { name: renewing?.name || "" })} onClose={() => setRenewing(null)}>
      <div className="modal-form"><p className="modal-copy">{t("calendar.renewLinkConfirmCopy")}</p>
        <div className="modal-actions"><button type="button" className="button" onClick={() => setRenewing(null)}>{t("common.cancel")}</button>
          <button type="button" className="button primary" disabled={busy} onClick={renewLink}>{busy ? <LoaderCircle className="spin" size={16} /> : t("calendar.renewLinkConfirm")}</button></div>
      </div>
    </Modal>

    <Modal open={disconnecting !== null} title={t("calendar.disconnectConfirmTitle", { name: disconnecting?.name || "" })} onClose={() => setDisconnecting(null)}>
      <div className="modal-form"><p className="modal-copy">{t("calendar.disconnectConfirmCopy")}</p>
        <div className="modal-actions"><button type="button" className="button" onClick={() => setDisconnecting(null)}>{t("common.cancel")}</button>
          <button type="button" className="button danger" disabled={busy} onClick={disconnect}>{busy ? <LoaderCircle className="spin" size={16} /> : t("calendar.disconnectConfirm")}</button></div>
      </div>
    </Modal>

    <Modal open={removing !== null} title={t("calendar.removeConfirmTitle", { name: removing?.name || "" })} onClose={() => setRemoving(null)}>
      <div className="modal-form"><p className="modal-copy">{t("calendar.removeConfirmCopy")}</p>
        <div className="modal-actions"><button type="button" className="button" onClick={() => setRemoving(null)}>{t("common.cancel")}</button>
          <button type="button" className="button danger" disabled={busy} onClick={remove}>{busy ? <LoaderCircle className="spin" size={16} /> : <><Trash2 size={15} /> {t("calendar.removeConfirm")}</>}</button></div>
      </div>
    </Modal>
  </div>;
}

function CalendarMemberCard({ member, canManage, onEdit, onCopy, onRenew, onDisconnect, onRemove }: {
  member: CalendarMember; canManage: boolean;
  onEdit: () => void; onCopy: () => void; onRenew: () => void; onDisconnect: () => void; onRemove: () => void;
}) {
  const t = useT();
  const statusLabel = member.status === "connected" ? t("calendar.statusConnected") : member.status === "error" ? t("calendar.statusError") : t("calendar.statusPending");
  return <article className="calendar-member-card">
    <span className="calendar-avatar" style={{ background: member.color }}><UserRound size={18} /></span>
    <div className="calendar-member-info">
      <strong>{member.name}{member.role && <small className="calendar-member-role"> · {member.role}</small>}</strong>
      <small className={`calendar-status calendar-status-${member.status}`}><i style={{ background: member.status === "connected" ? "var(--green)" : member.status === "error" ? "var(--red)" : "var(--faint)" }} />{statusLabel}
        {member.status === "connected" && member.google_email && <span> · {member.google_email}</span>}</small>
      {member.status === "error" && member.last_error && <small className="field-help">{member.last_error}</small>}
      {member.status !== "connected" && member.link_expired && <small className="calendar-link-expired">{t("calendar.linkExpired")}</small>}
    </div>
    <div className="calendar-member-actions">
      {member.status !== "connected" && <button type="button" className="button secondary small" onClick={onCopy}><Copy size={14} /> {t("calendar.copyLink")}</button>}
      {canManage && <>
        <button type="button" className="icon-button" title={t("common.edit")} aria-label={t("common.edit")} onClick={onEdit}><Pencil size={15} /></button>
        <button type="button" className="icon-button" title={t("calendar.renewLink")} aria-label={t("calendar.renewLink")} onClick={onRenew}><Link2 size={15} /></button>
        {member.status === "connected" && <button type="button" className="icon-button" title={t("calendar.disconnect")} aria-label={t("calendar.disconnect")} onClick={onDisconnect}><Plug size={15} /></button>}
        <button type="button" className="icon-button danger-icon" title={t("calendar.remove")} aria-label={t("calendar.remove")} onClick={onRemove}><Trash2 size={15} /></button>
      </>}
    </div>
  </article>;
}

function MemberForm({ member, isNew, busy, onCancel, onSave }: {
  member: CalendarMember | null; isNew: boolean; busy: boolean;
  onCancel: () => void; onSave: (name: string, role: string, color: string) => void;
}) {
  const t = useT();
  const [name, setName] = useState(member?.name || "");
  const [role, setRole] = useState(member?.role || "");
  const [color, setColor] = useState(member?.color || "#2f6df0");
  return <form className="modal-form" onSubmit={(e) => { e.preventDefault(); if (name.trim()) onSave(name.trim(), role.trim(), color); }}>
    <label>{t("calendar.name")}<input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("calendar.namePlaceholder")} required autoFocus maxLength={120} /></label>
    <label>{t("calendar.role")}<input value={role} onChange={(e) => setRole(e.target.value)} placeholder={t("calendar.rolePlaceholder")} maxLength={120} /></label>
    {isNew && <label>{t("calendar.color")}<div className="color-input"><input type="color" value={color} onChange={(e) => setColor(e.target.value)} /><input value={color} readOnly /></div></label>}
    <div className="modal-actions"><button type="button" className="button" onClick={onCancel}>{t("common.cancel")}</button>
      <button className="button primary" disabled={busy || !name.trim()}>{busy ? <LoaderCircle className="spin" size={16} /> : <><Save size={15} /> {isNew ? t("calendar.add") : t("calendar.save")}</>}</button></div>
  </form>;
}

function CalendarWeek({ base, members }: { base: string; members: CalendarMember[] }) {
  const t = useT();
  const [anchor, setAnchor] = useState(() => new Date());
  const [result, setResult] = useState<CalendarEventsResult | null>(null);
  const [loading, setLoading] = useState(true);
  const start = useMemo(() => weekStart(anchor), [anchor]);
  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(start, i)), [start]);
  const membersById = useMemo(() => new Map(members.map((m) => [m.id, m])), [members]);
  const hasConnected = members.some((m) => m.status === "connected");

  useEffect(() => {
    if (!hasConnected) { setResult({ events: [], errors: [] }); setLoading(false); return; }
    let cancelled = false;
    setLoading(true);
    const end = addDays(start, 7);
    api<CalendarEventsResult>(`${base}/calendar/events?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`)
      .then((data) => { if (!cancelled) setResult(data); })
      .catch(() => { if (!cancelled) setResult({ events: [], errors: [] }); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [base, start, hasConnected]);

  const eventsByDay = useMemo(() => {
    const buckets = new Map<string, CalendarEvent[]>();
    for (const event of result?.events ?? []) {
      const key = new Date(event.start).toDateString();
      buckets.set(key, [...(buckets.get(key) ?? []), event]);
    }
    for (const list of buckets.values()) list.sort((a, b) => a.start.localeCompare(b.start));
    return buckets;
  }, [result]);

  const monthLabel = start.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const today = new Date().toDateString();

  return <section className="form-section calendar-week-section">
    <div className="calendar-week-head">
      <h2 style={{ textTransform: "capitalize" }}>{monthLabel}</h2>
      <div className="calendar-week-nav">
        <button type="button" className="icon-button" aria-label={t("calendar.previousWeek")} title={t("calendar.previousWeek")} onClick={() => setAnchor(addDays(start, -7))}><ChevronLeft size={17} /></button>
        <button type="button" className="button secondary small" onClick={() => setAnchor(new Date())}>{t("calendar.today")}</button>
        <button type="button" className="icon-button" aria-label={t("calendar.nextWeek")} title={t("calendar.nextWeek")} onClick={() => setAnchor(addDays(start, 7))}><ChevronRight size={17} /></button>
      </div>
    </div>
    {result?.errors && result.errors.length > 0 && <Alert type="info"><AlertTriangle size={14} style={{ verticalAlign: "-2px", marginRight: 6 }} />
      {t("calendar.eventsErrorFor", { names: result.errors.map((err) => membersById.get(err.member_id)?.name).filter(Boolean).join(", ") })}</Alert>}
    {loading ? <ListRowsSkeleton rows={2} /> : <div className="calendar-grid">
      {days.map((day) => {
        const items = eventsByDay.get(day.toDateString()) ?? [];
        return <div key={day.toISOString()} className={`calendar-day${day.toDateString() === today ? " calendar-day-today" : ""}`}>
          <header><span className="calendar-day-name">{day.toLocaleDateString(undefined, { weekday: "short" })}</span><span className="calendar-day-num">{day.getDate()}</span></header>
          <div className="calendar-day-events">
            {items.length === 0 ? <small className="calendar-day-empty">{t("calendar.noEvents")}</small> : items.map((event) => {
              const member = membersById.get(event.member_id);
              return <a key={event.id} className="calendar-event" style={{ borderColor: member?.color, background: `${member?.color}1a` }}
                href={event.url || undefined} target={event.url ? "_blank" : undefined} rel="noreferrer" title={event.title}>
                <span className="calendar-event-dot" style={{ background: member?.color }} />
                <span className="calendar-event-time">{event.all_day ? t("calendar.allDay") : new Date(event.start).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</span>
                <span className="calendar-event-title">{event.title || t("calendar.title")}</span>
              </a>;
            })}
          </div>
        </div>;
      })}
    </div>}
  </section>;
}
