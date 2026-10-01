"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { Building2, LoaderCircle, UserRound } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { useLanguage, useT, useApiError } from "@/lib/i18n";
import { PlatformShell } from "@/components/platform-shell";
import { useToast } from "@/components/toast";
import { Alert, EmptyState, PageHead } from "@/components/ui";
import { SectionTabs } from "@/components/section-tabs";
import { PLATFORM_AGENCY_TABS, platformAgencyPath, tabFromSegments } from "@/lib/routes";
import { AGENCY_FEATURES, AGENCY_PRESETS, PRESET_NAMES, isQuotaFeature, type AgencyFeature, type QuotaFeature } from "@/lib/agency-features";
import { QuotaStepper } from "@/components/quota-stepper";
import type { PlatformAgency, PlatformAgencyUser, PlatformClient, PlatformInfrastructureClient, PlatformInvitation, PlatformUsage } from "@/types";

function tokens(value: number): string {
  return value.toLocaleString();
}

function cost(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `$${value.toFixed(4)}`;
}

function StatusPill({ status }: { status: string }) {
  return (
    <span className={`status ${status === "connected" || status === "active" ? "status-active" : status === "error" ? "status-inactive" : ""}`}>
      <i />{status}
    </span>
  );
}

function PlanTab({ agency }: { agency: PlatformAgency }) {
  const t = useT();
  const [features, setFeatures] = useState<Record<string, boolean>>(agency.features);
  // Only the types the platform capped are here; a missing key means unlimited.
  const [quotas, setQuotas] = useState<Record<string, number>>(agency.channel_quotas);
  const [used, setUsed] = useState<Record<string, number>>(agency.channel_used);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  // Clients that keep their data in the agency's own project when the module is about to go off.
  const [offWarning, setOffWarning] = useState<number | null>(null);

  function adopt(body: PlatformAgency) {
    setFeatures(body.features);
    setQuotas(body.channel_quotas);
    setUsed(body.channel_used);
  }

  async function put(next: Record<string, boolean>, plan?: string) {
    setBusy("all");
    setError("");
    try {
      const body = await api<PlatformAgency>(`/platform/agencies/${agency.id}/features`, {
        method: "PUT",
        body: JSON.stringify(plan === undefined ? { features: next } : { features: next, plan }),
      });
      adopt(body);
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(null);
    }
  }

  /** One number changes; the switches ride along unchanged, because the endpoint
   * saves the plan in one transaction. null lifts the cap. */
  async function putQuota(key: QuotaFeature, value: number | null) {
    setBusy(key);
    setError("");
    try {
      const body = await api<PlatformAgency>(`/platform/agencies/${agency.id}/features`, {
        method: "PUT",
        body: JSON.stringify({ features, channel_quotas: { [key]: value } }),
      });
      adopt(body);
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(null);
    }
  }

  async function toggle(key: AgencyFeature, enabled: boolean) {
    if (key === "agency_backend" && !enabled && features[key]) {
      // Turning it off never cuts anyone off, but the platform should know who is inside.
      try {
        const rows = await api<PlatformInfrastructureClient[]>(`/platform/agencies/${agency.id}/infrastructure`);
        const inside = rows.filter((row) => row.data_mode === "agency" || row.storage?.hosted_by === "agency").length;
        if (inside > 0) { setOffWarning(inside); return; }
      } catch (err) { setError(messageFrom(err)); return; }
    }
    void put({ [key]: enabled });
  }

  function applyPreset(name: keyof typeof AGENCY_PRESETS) {
    const keys = AGENCY_PRESETS[name];
    const snapshot = Object.fromEntries(AGENCY_FEATURES.map((entry) => [entry.key, keys.includes(entry.key as never)]));
    void put(snapshot, name);
  }

  return (
    <section style={{ marginTop: 20 }}>
      <div className="section-copy" style={{ marginBottom: 14 }}>
        <h2>{t("platform.detail.planTitle")}</h2>
        <p>{t("platform.detail.planCopy")}</p>
        <p style={{ color: "var(--muted)", fontSize: 13 }}>{t("channels.quota.hint")}</p>
      </div>
      <div className="stitch-feature-grid">
        {AGENCY_FEATURES.map((entry) => {
          const on = features[entry.key] ?? entry.default;
          // Narrowed here so the handlers below keep the quota type.
          const quotaKey = isQuotaFeature(entry.key) ? entry.key : null;
          const quota = quotaKey ? quotas[quotaKey] : undefined;
          const unlimited = quota === undefined;
          const inUse = quotaKey ? used[quotaKey] ?? 0 : 0;
          const over = !unlimited && inUse > (quota ?? 0);
          const stepDisabled = busy !== null || !on;
          return (
            <div key={entry.key} className="stitch-feature-tile">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
                  {t(`platform.detail.modules.${entry.key}` as const)}
                </span>
                <label className="stitch-switch">
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={busy !== null}
                    onChange={(event) => toggle(entry.key, event.target.checked)}
                  />
                  <span className="stitch-slider" />
                </label>
              </div>
              {quotaKey && (
                <div style={{ marginTop: 8 }}>
                  <QuotaStepper
                    value={quota ?? null}
                    disabled={stepDisabled}
                    onChange={(next) => void putQuota(quotaKey, next)}
                  />
                  <small style={{ display: "block", marginTop: 4, color: over ? "#b91c1c" : "var(--muted)" }}>
                    {unlimited
                      ? t("channels.quota.inUseFree", { used: inUse })
                      : over
                        ? t("channels.quota.overLimit", { used: inUse, quota: quota as number })
                        : t("channels.quota.inUseOf", { used: inUse, quota: quota as number })}
                  </small>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 18, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, color: "var(--muted)" }}>{t("platform.detail.presetLabel")}:</span>
        {PRESET_NAMES.map((name) => (
          <button
            key={name}
            type="button"
            className={agency.plan === name ? "button primary" : "button"}
            disabled={busy !== null}
            onClick={() => applyPreset(name)}
          >
            {t("platform.detail.apply")} {name}
          </button>
        ))}
        {agency.plan && <small style={{ color: "var(--muted)" }}>{t("platform.detail.currentPlan", { plan: agency.plan })}</small>}
      </div>
      {offWarning !== null && <Alert type="info">
        <strong>{t("agencyBackend.platform.offWarningTitle", { count: offWarning })}</strong>
        <p style={{ margin: "4px 0 8px" }}>{t("agencyBackend.platform.offWarningCopy")}</p>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="button danger small" disabled={busy !== null} onClick={() => { setOffWarning(null); void put({ agency_backend: false }); }}>{t("agencyBackend.platform.offConfirm")}</button>
          <button type="button" className="button small" onClick={() => setOffWarning(null)}>{t("common.cancel")}</button>
        </div>
      </Alert>}
      {error && <Alert>{error}</Alert>}
    </section>
  );
}

function SummaryTab({ agency }: { agency: PlatformAgency }) {
  const t = useT();
  const [reason, setReason] = useState(agency.access_block_reason);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [invite, setInvite] = useState<PlatformInvitation | null>(null);
  const [inviteName, setInviteName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteBusy, setInviteBusy] = useState(false);

  async function setAccess(status: "active" | "blocked") {
    setBusy(true);
    setError("");
    try {
      const body = await api<PlatformAgency>(`/platform/agencies/${agency.id}/access`, {
        method: "PUT",
        body: JSON.stringify(status === "blocked" ? { status, reason } : { status }),
      });
      setReason(body.access_block_reason);
      setConfirming(false);
      // Keep the screen honest: reload the resolved agency.
      window.location.reload();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  async function sendInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setInviteBusy(true);
    try {
      const body = await api<PlatformInvitation>(`/platform/agencies/${agency.id}/admin-invitations`, {
        method: "POST",
        body: JSON.stringify({ email: inviteEmail, name: inviteName }),
      });
      setInvite(body);
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setInviteBusy(false);
    }
  }

  const blocked = agency.access_status === "blocked";

  return (
    <>
      <section className="form-section" style={{ marginTop: 20 }}>
        <div className="section-copy">
          <h2>{t("platform.detail.accessTitle")}</h2>
          <p>{t("platform.detail.accessCopy")}</p>
        </div>
        <div className="form-fields">
          <p>
            <span className={`status ${blocked ? "status-inactive" : "status-active"}`}><i />{blocked ? t("platform.detail.blocked") : t("platform.detail.active")}</span>
            {blocked && agency.access_block_reason ? <small style={{ marginLeft: 8 }}>{agency.access_block_reason}</small> : null}
          </p>
          {blocked ? (
            <button className="button primary" disabled={busy} onClick={() => void setAccess("active")}>{t("platform.detail.restore")}</button>
          ) : (
            <>
              <label>{t("platform.detail.reason")}<input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
              {confirming ? (
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="button danger" disabled={busy} onClick={() => void setAccess("blocked")}>{busy && <LoaderCircle className="spin" size={16} />}{t("platform.detail.blockConfirm")}</button>
                  <button className="button" onClick={() => setConfirming(false)}>{t("common.cancel")}</button>
                </div>
              ) : (
                <button className="button danger" onClick={() => setConfirming(true)}>{t("platform.detail.block")}</button>
              )}
            </>
          )}
          {error && <Alert>{error}</Alert>}
        </div>
      </section>
      <PeopleSection agencyId={agency.id} hasInvitation={!blocked} />
      <section className="form-section">
        <div className="section-copy">
          <h2>{t("platform.detail.inviteTitle")}</h2>
          <p>{t("platform.detail.inviteCopy")}</p>
        </div>
        <div className="form-fields">
          {invite ? (
            <div style={{ padding: "12px 14px", border: "1px solid var(--line)", borderRadius: 9 }}>
              <p style={{ margin: "0 0 6px", fontSize: 13, color: "var(--muted)" }}>{t("platform.detail.inviteLink")}</p>
              <p style={{ wordBreak: "break-all", margin: 0 }}><strong>{invite.url}</strong></p>
            </div>
          ) : (
            <form onSubmit={sendInvitation} style={{ display: "grid", gap: 12 }}>
              <label>{t("platform.detail.inviteName")}<input value={inviteName} onChange={(event) => setInviteName(event.target.value)} required minLength={2} /></label>
              <label>{t("platform.detail.inviteEmail")}<input value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} required type="email" /></label>
              <button className="button primary align-start" disabled={inviteBusy}>{inviteBusy && <LoaderCircle className="spin" size={16} />}{t("platform.detail.inviteSend")}</button>
            </form>
          )}
        </div>
      </section>
    </>
  );
}

/** Who is in the agency. The rest of the profile is counts and switches, which
 * read the same whether an account is in daily use or was opened once and left;
 * the names and addresses are what answer that. */
function PeopleSection({ agencyId }: { agencyId: string; hasInvitation: boolean }) {
  const t = useT();
  const { lang } = useLanguage();
  const [people, setPeople] = useState<PlatformAgencyUser[] | null>(null);

  useEffect(() => {
    let active = true;
    api<PlatformAgencyUser[]>(`/platform/agencies/${agencyId}/users`)
      .then((rows) => { if (active) setPeople(rows); })
      .catch(() => { if (active) setPeople([]); });
    return () => { active = false; };
  }, [agencyId]);

  return (
    <section className="form-section">
      <div className="section-copy">
        <h2>{t("platform.detail.peopleTitle")}</h2>
        <p>{t("platform.detail.peopleCopy")}</p>
      </div>
      <div className="form-fields">
        {people === null ? (
          <p className="muted">{t("common.loading")}</p>
        ) : people.length === 0 ? (
          <p className="muted">{t("platform.detail.peopleNone")}</p>
        ) : (
          <ul className="platform-people">
            {people.map((person) => (
              <li key={person.id}>
                <span className="entity-avatar tiny">
                  <UserRound size={15} />
                </span>
                <span className="platform-people-id">
                  <strong>{person.name}</strong>
                  <small>{person.email}</small>
                </span>
                <span className={`status ${person.role === "admin" ? "status-active" : "status-inactive"}`}>
                  <i />
                  {person.role === "admin" ? t("platform.detail.peopleAdmin") : t("platform.detail.peopleAgent")}
                </span>
                <small className="platform-people-since">
                  {t("platform.detail.peopleSince", { date: new Date(person.created_at).toLocaleDateString(lang === "es" ? "es-ES" : "en-GB") })}
                </small>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function ClientsTab({ agency }: { agency: PlatformAgency }) {
  const t = useT();
  const [clients, setClients] = useState<PlatformClient[] | null>(null);

  useEffect(() => {
    let active = true;
    api<PlatformClient[]>(`/platform/agencies/${agency.id}/clients`)
      .then((rows) => { if (active) setClients(rows); })
      .catch(() => {});
    return () => { active = false; };
  }, [agency.id]);

  if (clients === null) return null;
  if (clients.length === 0) {
    return <EmptyState icon={<Building2 size={22} />} title={t("platform.detail.clientsEmpty")} description="" />;
  }
  return (
    <div className="table-shell" style={{ marginTop: 20 }}>
      <table>
        <thead>
          <tr>
            <th>{t("platform.create.name")}</th>
            <th>{t("platform.detail.state")}</th>
            <th>{t("platform.detail.mode")}</th>
            <th>{t("platform.overview.agents")}</th>
          </tr>
        </thead>
        <tbody>
          {clients.map((client) => (
            <tr key={client.id}>
              <td><span className="table-link">{client.name}</span><small style={{ marginLeft: 8, color: "var(--muted)" }}>{client.portal_slug}</small></td>
              <td>{client.is_active ? t("platform.detail.active") : t("platform.list.blocked")}</td>
              <td>{client.data_mode}</td>
              <td>{t("platform.detail.clientAgents", { count: client.agent_count })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UsageTab({ agency }: { agency: PlatformAgency }) {
  const t = useT();
  const [usage, setUsage] = useState<PlatformUsage | null>(null);

  useEffect(() => {
    let active = true;
    api<PlatformUsage>(`/platform/agencies/${agency.id}/usage`)
      .then((rows) => { if (active) setUsage(rows); })
      .catch(() => {});
    return () => { active = false; };
  }, [agency.id]);

  if (usage === null) return null;
  if (usage.total.replies === 0) {
    return <EmptyState icon={<Building2 size={22} />} title={t("platform.detail.usageEmpty")} description="" />;
  }
  return (
    <>
      <div className="metrics-grid" style={{ marginTop: 20, gridTemplateColumns: "repeat(3, 1fr)" }}>
        <article className="metric-card"><small>{t("platform.detail.replies")}</small><strong>{usage.total.replies}</strong></article>
        <article className="metric-card"><small>{t("platform.detail.tokensIn")}</small><strong>{tokens(usage.total.input_tokens)}</strong></article>
        <article className="metric-card"><small>{t("platform.detail.cost")}</small><strong>{cost(usage.total.cost_usd)}</strong></article>
      </div>
      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>{t("platform.detail.day")}</th>
              <th>{t("platform.detail.replies")}</th>
              <th>{t("platform.detail.tokensIn")}</th>
              <th>{t("platform.detail.tokensOut")}</th>
              <th>{t("platform.detail.cost")}</th>
            </tr>
          </thead>
          <tbody>
            {usage.days.map((day) => (
              <tr key={day.date}>
                <td>{day.date}</td>
                <td>{day.replies}</td>
                <td>{tokens(day.input_tokens)}</td>
                <td>{tokens(day.output_tokens)}</td>
                <td>{cost(day.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function InfrastructureTab({ agency }: { agency: PlatformAgency }) {
  const t = useT();
  const apiError = useApiError();
  const toast = useToast();
  const [clients, setClients] = useState<PlatformInfrastructureClient[] | null>(null);
  const [moving, setMoving] = useState<string | null>(null);

  const load = useCallback(() => api<PlatformInfrastructureClient[]>(`/platform/agencies/${agency.id}/infrastructure`)
    .then(setClients)
    .catch(() => {}), [agency.id]);
  useEffect(() => { void load(); }, [load]);

  /** The platform's own move of a client's data or files: the same verified copy
   * the agency makes, audited with who did it. */
  async function move(client: PlatformInfrastructureClient, kind: "data" | "files", target: string) {
    setMoving(`${client.client_id}:${kind}`);
    try {
      const path = kind === "data" ? "datastore" : "storage";
      await api(`/platform/agencies/${agency.id}/clients/${client.client_id}/${path}/switch`, { method: "POST", body: JSON.stringify({ target }) });
      toast.success(t("agencyBackend.platform.moved"));
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setMoving(null); await load(); }
  }

  if (clients === null) return null;
  if (clients.length === 0) {
    return <EmptyState icon={<Building2 size={22} />} title={t("platform.detail.infraEmpty")} description="" />;
  }
  return (
    <div className="table-shell" style={{ marginTop: 20 }}>
      <table>
        <thead>
          <tr>
            <th>{t("platform.create.name")}</th>
            <th>{t("platform.detail.mode")}</th>
            <th>{t("platform.detail.datastore")}</th>
            <th>{t("platform.detail.storage")}</th>
          </tr>
        </thead>
        <tbody>
          {clients.map((client) => (
            <tr key={client.client_id}>
              <td>{client.client_name}<small style={{ marginLeft: 8, color: "var(--muted)" }}>{client.portal_slug}</small></td>
              <td>
                {client.data_mode === "central" ? t("agencyBackend.platform.modeCentral")
                  : client.data_mode === "supabase" ? t("agencyBackend.platform.modeSupabase")
                  : client.data_mode === "agency" ? t("agencyBackend.platform.modeAgency")
                  : t("agencyBackend.platform.modeSwitching")}
                {client.data_mode !== "switching" && <select
                  style={{ display: "block", marginTop: 6 }}
                  aria-label={t("agencyBackend.platform.move")}
                  disabled={moving !== null}
                  value=""
                  onChange={(event) => { if (event.target.value) void move(client, "data", event.target.value); }}
                >
                  <option value="">{moving === `${client.client_id}:data` ? "…" : t("agencyBackend.platform.move")}</option>
                  {client.data_mode !== "central" && <option value="central">{t("agencyBackend.platform.modeCentral")}</option>}
                  {client.data_mode !== "agency" && <option value="agency">{t("agencyBackend.platform.modeAgency")}</option>}
                  {client.data_mode !== "supabase" && <option value="supabase">{t("agencyBackend.platform.modeSupabase")}</option>}
                </select>}
                {client.agency_schema?.retired_at && client.data_mode !== "agency" && <small style={{ display: "block", color: "var(--muted)" }}>{t("agencyBackend.location.retiredCopy", { date: new Date(client.agency_schema.retired_at).toLocaleDateString() })}</small>}
              </td>
              <td>
                {client.datastore ? (
                  <>
                    <StatusPill status={client.datastore.status} />
                    {client.datastore.last_error && <small style={{ display: "block", color: "var(--red-text)" }}>{apiError(client.datastore.last_error)}</small>}
                  </>
                ) : t("platform.detail.none")}
              </td>
              <td>
                {client.storage ? (
                  <>
                    <StatusPill status={client.storage.status} />
                    {client.storage.hosted_by === "agency"
                      ? <small style={{ display: "block", color: "var(--muted)" }}>{t("agencyBackend.platform.filesHosted")}</small>
                      : client.storage.bucket && <small style={{ display: "block", color: "var(--muted)" }}>{client.storage.bucket}</small>}
                    <button
                      type="button"
                      className="button small"
                      style={{ marginTop: 6 }}
                      disabled={moving !== null}
                      onClick={() => void move(client, "files", client.storage?.hosted_by === "agency" ? "client" : "agency")}
                    >{moving === `${client.client_id}:files` ? "…" : client.storage.hosted_by === "agency" ? t("agencyBackend.location.filesToClient") : t("agencyBackend.location.filesToAgency")}</button>
                  </>
                ) : t("platform.detail.none")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function PlatformAgencyPage() {
  const t = useT();
  const router = useRouter();
  const { slug, tab: segments } = useParams<{ slug: string; tab?: string[] }>();
  const [agency, setAgency] = useState<PlatformAgency | null>(null);
  const [missing, setMissing] = useState(false);
  const { tab } = tabFromSegments(PLATFORM_AGENCY_TABS, segments, "summary");

  useEffect(() => {
    let active = true;
    api<PlatformAgency>(`/platform/agencies/by-slug/${encodeURIComponent(slug)}`)
      .then((current) => { if (active) setAgency(current); })
      .catch(() => { if (active) setMissing(true); });
    return () => { active = false; };
  }, [slug, router]);

  if (missing) {
    return (
      <PlatformShell>
        <EmptyState
          icon={<Building2 size={22} />}
          title={t("platform.list.empty")}
          description=""
          action={<Link className="button primary" href="/superadmin/agencies">{t("platform.nav.agencies")}</Link>}
        />
      </PlatformShell>
    );
  }
  if (!agency) return <PlatformShell><div className="app-loader" /></PlatformShell>;

  return (
    <PlatformShell>
      <PageHead
        eyebrow={t("platform.overview.agencies")}
        title={agency.name}
        description={`/superadmin/agencies/${agency.slug}`}
        action={
          <span className={`status ${agency.access_status === "blocked" ? "status-inactive" : "status-active"}`}>
            <i />{agency.access_status === "blocked" ? t("platform.detail.blocked") : t("platform.detail.active")}
          </span>
        }
      />
      <div className="section-tabs-wrap">
        <SectionTabs
          value={tab}
          tabs={PLATFORM_AGENCY_TABS.map((item) => ({
            id: item,
            label: t(`platform.detail.tabs.${item}` as const),
            href: platformAgencyPath(agency.slug, item),
          }))}
        />
      </div>
      {tab === "summary" && <SummaryTab agency={agency} />}
      {tab === "clients" && <ClientsTab agency={agency} />}
      {tab === "usage" && <UsageTab agency={agency} />}
      {tab === "plan" && <PlanTab agency={agency} />}
      {tab === "infrastructure" && <InfrastructureTab agency={agency} />}
    </PlatformShell>
  );
}
