"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, Bot, Briefcase, Calendar as CalendarIcon, Copy, Database, ExternalLink, FileText, GitBranch, Globe2, HardDrive, Inbox, KeyRound, LoaderCircle, Lock, Pencil, Radio, Save, Settings2, ShieldAlert, ShieldCheck, Stethoscope, Tag, Trash2, UserCheck, UserRound, Users, UserX } from "lucide-react";
import { Alert, EmptyState, Modal, StatusBadge } from "@/components/ui";
import { SectionTabs } from "@/components/section-tabs";
import { ApiIntegrations } from "@/components/api-integrations";
import { CalendarView } from "@/components/calendar-view";
import { PipelineBoard } from "@/components/pipeline-board";
import { ClientDetails } from "@/components/client-details";
import { ClientInbox } from "@/components/client-inbox";
import { ProfessionalsView } from "@/components/professionals-view";
import { ServicesView } from "@/components/services-view";
import { ResourcesView } from "@/components/resources-view";
import { DataStorePanel } from "@/components/data-store-panel";
import { TeamsView } from "@/app/portal/[slug]/teams";
import { TagsView } from "@/app/portal/[slug]/tags";
import { TemplatesView } from "@/app/portal/[slug]/templates";
import { FormSkeleton, ListRowsSkeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { PasswordInput } from "@/components/password-input";
import { api, messageFrom } from "@/lib/api";
import { CLIENT_TABS, CLIENT_UUID_RE, clientPath, tabFromSegments, type ClientTab } from "@/lib/routes";
import { useLanguage, useT } from "@/lib/i18n";
import { businessLabel, useIndustries } from "@/lib/industries";
import type { Client, ClientDomain, PortalRole, PortalUser } from "@/types";
import { ChannelsOverviewView } from "@/components/channels/channels-overview";
import { PortalFeatureToggle } from "@/components/portal-feature-toggle";
import { FEATURES_BY_CLIENT_TAB } from "@/lib/portal-features";

type Tab = ClientTab;

export default function ClientDetailPage() {
  const { t, lang } = useLanguage();
  const toast = useToast();
  const { slug, tab: segments } = useParams<{ slug: string; tab?: string[] }>();
  const router = useRouter();
  // The address is the source of truth for the tab (and, in the inbox, the open lead): reload,
  // Back/Forward and shared links all agree (lib/routes.ts).
  const route = tabFromSegments(CLIENT_TABS, segments, "details");
  const tab = route.tab;
  const leadNumber = tab === "inbox" && route.rest[0] && /^\d+$/.test(route.rest[0]) ? Number(route.rest[0]) : undefined;
  const catalog = useIndustries();
  const [client, setClient] = useState<Client | null>(null);
  const [domain, setDomain] = useState<ClientDomain | null>(null);
  // The address names the client by slug. A legacy UUID address resolves the
  // client once and moves to its slug, keeping tab, query and hash.
  useEffect(() => {
    let cancelled = false;
    const path = CLIENT_UUID_RE.test(slug) ? `/clients/${slug}` : `/clients/by-slug/${encodeURIComponent(slug)}`;
    api<Client>(path).then((loaded) => {
      if (cancelled) return;
      setClient(loaded);
      api<ClientDomain>(`/clients/${loaded.id}/domain`).then(setDomain).catch(() => {});
      if (loaded.portal_slug !== slug) {
        const rest = segments ? `/${segments.join("/")}` : "";
        router.replace(`/clients/${loaded.portal_slug}${rest}${window.location.search}${window.location.hash}`);
      }
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [slug]);
  // An unknown first segment goes to the default tab, and a legacy /clients/{slug}?tab=channels
  // (older channel pages and API redirects still build it) moves to /clients/{slug}/channels;
  // either way the rest of the query is kept.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const legacy = params.get("tab");
    if (route.known && legacy === null) return;
    let target: Tab = route.known ? route.tab : "details";
    if (legacy !== null) {
      target = CLIENT_TABS.find((item) => item === legacy) ?? target;
      params.delete("tab");
    }
    const query = params.toString();
    router.replace(`${clientPath(client?.portal_slug ?? slug, target)}${query ? `?${query}` : ""}${window.location.hash}`);
  }, [route.known, route.tab, slug, client?.portal_slug, router]);
  const [busy, setBusy] = useState(false);

  async function savePortal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true);
    const data = new FormData(event.currentTarget);
    const payload: Record<string, unknown> = { portal_enabled: data.get("portal_enabled") === "on", portal_slug: data.get("portal_slug"), portal_title: data.get("portal_title") };
    // The form only renders once the client is loaded, so client.id is set;
    // the slug fallback covers the unreachable unloaded case for the types.
    try {
      const saved = await api<Client>(`/clients/${client?.id ?? slug}/portal`, { method: "PATCH", body: JSON.stringify(payload) });
      setClient(saved);
      if (!CLIENT_UUID_RE.test(slug) && saved.portal_slug !== slug) {
        const rest = segments ? `/${segments.join("/")}` : "";
        router.replace(`/clients/${saved.portal_slug}${rest}${window.location.search}${window.location.hash}`);
      }
      toast.success(t("clients.detail.portalUpdated"));
    }
    catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }

  if (!client) return <div className="page"><FormSkeleton sections={2} /></div>;
  // The portal lives under whatever host is serving this app, so the prefix
  // and the preview follow the browser origin. A verified custom domain
  // serves the portal at its root instead (see proxy.ts).
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const defaultPortalUrl = `${origin}/portal/${client.portal_slug}`;
  const portalUrl = domain?.verified && domain.domain ? `https://${domain.domain}` : defaultPortalUrl;
  return <div className="page">
    <div className="stitch-breadcrumb-bar">
      <Link href="/clients" className="stitch-back-pill">
        <ArrowLeft size={16} /> <span>{t("clients.detail.back")}</span>
      </Link>
    </div>

    <header className="stitch-client-hero">
      <div className="stitch-hero-left">
        <div className="stitch-hero-avatar">
          {client.name.slice(0, 2).toUpperCase()}
        </div>
        <div className="stitch-hero-text">
          <div className="stitch-hero-name-row">
            <h1 className="stitch-hero-name">{client.name}</h1>
            <StatusBadge active={client.is_active} />
          </div>
          <p className="stitch-hero-sub">
            {businessLabel(catalog, client, lang) || t("clients.detail.industryUndefined")} · {client.agents.length === 1 ? t("clients.detail.agentOne", { count: client.agents.length }) : t("clients.detail.agentMany", { count: client.agents.length })}
          </p>
        </div>
      </div>
      <div>
        <Link href={`/agents/new?client=${client.id}`} className="stitch-hero-cta">
          <Bot size={16} /> <span>{t("clients.detail.newAgent")}</span>
        </Link>
      </div>
    </header>

    <SectionTabs<Tab> className="client-tabs" value={tab} tabs={[
      { id: "details", label: t("clients.detail.tabDetails"), icon: Settings2, href: clientPath(client.portal_slug, "details") },
      { id: "agents", label: t("clients.detail.tabAgents"), icon: Bot, badge: client.agents.length, href: clientPath(client.portal_slug, "agents") },
      { id: "channels", label: t("clients.detail.tabChannels"), icon: Radio, badge: 4, href: clientPath(client.portal_slug, "channels") },
      { id: "inbox", label: t("clients.detail.tabInbox"), icon: Inbox, href: clientPath(client.portal_slug, "inbox") },
      { id: "teams", label: t("clients.detail.tabTeams"), icon: Users, href: clientPath(client.portal_slug, "teams") },
      { id: "professionals", label: t("clients.detail.tabProfessionals"), icon: Stethoscope, href: clientPath(client.portal_slug, "professionals") },
      { id: "services", label: t("clients.detail.tabServices"), icon: Briefcase, href: clientPath(client.portal_slug, "services") },
      { id: "library", label: t("clients.detail.tabLibrary"), icon: HardDrive, href: clientPath(client.portal_slug, "library") },
      { id: "database", label: t("clients.detail.tabDatabase"), icon: Database, href: clientPath(client.portal_slug, "database") },
      { id: "tags", label: t("clients.detail.tabTags"), icon: Tag, href: clientPath(client.portal_slug, "tags") },
      { id: "templates", label: t("clients.detail.tabTemplates"), icon: FileText, href: clientPath(client.portal_slug, "templates") },
      { id: "calendar", label: t("clients.detail.tabCalendar"), icon: CalendarIcon, href: clientPath(client.portal_slug, "calendar") },
      { id: "pipeline", label: t("clients.detail.tabPipeline"), icon: GitBranch, href: clientPath(client.portal_slug, "pipeline") },
      { id: "api", label: t("clients.detail.tabApi"), icon: KeyRound, href: clientPath(client.portal_slug, "api") },
      { id: "portal", label: t("clients.detail.tabPortal"), icon: Globe2, href: clientPath(client.portal_slug, "portal") },
    ]} />

    {tab !== "channels" && FEATURES_BY_CLIENT_TAB[tab] && (
      <PortalFeatureToggle
        client={client}
        keys={FEATURES_BY_CLIENT_TAB[tab]}
        onChange={setClient}
        title={tab === "portal" ? t("clients.detail.portalFeaturesTitle") : undefined}
        extra={
          tab === "agents" ? (
            <div className="stitch-banner-stat-pill">
              <strong>{t("clients.detail.tabAgents")}</strong>
              <span>
                {client.agents.length === 1
                  ? t("clients.detail.agentOne", { count: client.agents.length })
                  : t("clients.detail.agentMany", { count: client.agents.length })}
              </span>
            </div>
          ) : undefined
        }
      />
    )}
    {tab === "details" && <ClientDetails mode="agency" clientId={client.id} client={client} onChange={setClient} />}

    {tab === "agents" && (client.agents.length ? (
      <ul className="stitch-agents-grid">
          {client.agents.map((agent) => (
            <li key={agent.id} className="stitch-agent-card">
              <div className="stitch-agent-card-head">
                <div className="stitch-agent-card-lead">
                  <div className="stitch-agent-icon-box">
                    <Bot size={22} />
                  </div>
                  <div className="stitch-agent-title-col">
                    <h3 className="stitch-agent-name">{agent.name}</h3>
                    <div className="stitch-agent-badges-row">
                      <span className={agent.is_active ? "stitch-badge-active" : "stitch-badge-inactive"}>
                        {agent.is_active && <span className="stitch-badge-active-dot" />}
                        {agent.is_active ? "Activo" : "Inactivo"}
                      </span>
                    </div>
                  </div>
                </div>

                <Link
                  href={`/agents/${agent.id}`}
                  className="stitch-config-btn"
                  title="Configurar agente"
                >
                  <span>Configurar</span>
                  <ArrowRight size={14} />
                </Link>
              </div>

              <div className="stitch-agent-card-foot">
                <span style={{ fontSize: "12px", color: "var(--muted)" }}>
                  Agente conversacional
                </span>
                <Link
                  href={`/agents/${agent.id}`}
                  style={{ fontSize: "12.5px", fontWeight: 600, color: "#00876c", display: "inline-flex", alignItems: "center", gap: "4px", textDecoration: "none" }}
                >
                  <span>Ver detalles</span>
                  <ArrowRight size={13} />
                </Link>
              </div>
            </li>
          ))}
        </ul>
    ) : (
      <EmptyState
        icon={<Bot />}
        title={t("clients.detail.agentsEmptyTitle")}
        description={t("clients.detail.agentsEmptyDescription")}
        action={
          <Link href={`/agents/new?client=${client.id}`} className="stitch-action-pill">
            <Bot size={16} /> <span>{t("clients.detail.createAgent")}</span>
          </Link>
        }
      />
    ))}

    {tab === "channels" && (
      <ChannelsOverviewView
        client={{ id: client.id, slug: client.portal_slug, name: client.name }}
        clientData={client}
        onClientChange={setClient}
      />
    )}

    {tab === "inbox" && (
      <div className="embedded-portal-view client-inbox-view" style={{ "--portal-color": "#00876c" } as React.CSSProperties}>
        <ClientInbox clientId={client.id} clientName={client.name} portalSlug={client.portal_slug} urlNumber={leadNumber} />
      </div>
    )}

    {/* Teams and WhatsApp templates are the client's own, managed here or from its portal; the views are the portal's, pointed at the agency routes. */}
    {tab === "teams" && <div className="embedded-portal-view"><TeamsView base={`/clients/${client.id}`} /></div>}
    {tab === "professionals" && <div className="embedded-portal-view"><ProfessionalsView apiBase={`/clients/${client.id}`} canManage timezone={client.timezone} /></div>}
    {tab === "services" && <div className="embedded-portal-view"><ServicesView apiBase={`/clients/${client.id}`} canManage currency={client.currency} /></div>}
    {tab === "database" && <div className="embedded-portal-view"><DataStorePanel clientId={client.id} /></div>}
    {tab === "library" && <div className="embedded-portal-view"><ResourcesView apiBase={`/clients/${client.id}`} canManage /></div>}
    {tab === "tags" && <div className="embedded-portal-view"><TagsView base={`/clients/${client.id}/contact-tags`} canManage /></div>}
    {tab === "templates" && <div className="embedded-portal-view"><TemplatesView base={`/clients/${client.id}`} /></div>}
    {tab === "calendar" && <CalendarView base={`/clients/${client.id}`} canManage />}
    {tab === "pipeline" && <div className="embedded-portal-view client-pipeline-view"><PipelineBoard base={`/clients/${client.id}`} clientSlug={client.portal_slug} canManage clientName={client.name} /></div>}
    {tab === "api" && <div className="embedded-portal-view"><ApiIntegrations clientId={client.id} clientName={client.name} /></div>}
    {tab === "portal" && <><form className="page-form" onSubmit={savePortal}><section className="form-section"><div className="section-copy"><h2>{t("clients.detail.portalTitle")}</h2><p>{t("clients.detail.portalCopy")}</p></div><div className="form-fields"><label>{t("clients.detail.portalTitleLabel")}<input name="portal_title" defaultValue={client.portal_title} placeholder={t("clients.detail.portalTitlePlaceholder", { name: client.name })} /></label><label>{t("clients.detail.portalUrl")}<div className="slug-input"><span>{origin.replace(/^https?:\/\//, "")}/portal/</span><input name="portal_slug" defaultValue={client.portal_slug} /></div></label><div className="url-preview"><code>{portalUrl}</code><button type="button" onClick={() => navigator.clipboard.writeText(portalUrl)}><Copy size={15} /> {t("clients.detail.copy")}</button>{client.portal_enabled && <a href={portalUrl} target="_blank"><ExternalLink size={15} /> {t("clients.detail.open")}</a>}</div><label className="switch-row"><span><strong>{t("clients.detail.publishPortal")}</strong><small>{t("clients.detail.publishPortalHint")}</small></span><input name="portal_enabled" type="checkbox" defaultChecked={client.portal_enabled} /></label></div></section><div className="form-footer"><button className="button primary" disabled={busy}>{busy ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />} {t("clients.detail.savePortal")}</button></div></form><PortalUsers clientId={client.id} /><PortalDomain clientId={client.id} domain={domain} onChange={setDomain} /></>}
  </div>;
}

function PortalUsers({ clientId }: { clientId: string }) {
  const t = useT();
  const toast = useToast();
  const [users, setUsers] = useState<PortalUser[] | null>(null);
  const [busy, setBusy] = useState(false);
  // "new" opens the create dialog; a user opens the edit dialog for them.
  const [editing, setEditing] = useState<PortalUser | "new" | null>(null);
  const [deleting, setDeleting] = useState<PortalUser | null>(null);
  const [suspending, setSuspending] = useState<PortalUser | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setUsers(await api<PortalUser[]>(`/clients/${clientId}/portal-users`));
  }, [clientId]);
  useEffect(() => { load().catch(() => {}); }, [load]);

  function open(target: PortalUser | "new") { setModalError(null); setEditing(target); }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    const data = new FormData(event.currentTarget);
    const password = String(data.get("password") || "");
    if (editing === "new" && password !== String(data.get("password_confirm") || "")) { setModalError(t("clients.detail.portalUserPasswordMismatch")); return; }
    const payload: Record<string, string> = { name: String(data.get("name") || "").trim(), email: String(data.get("email") || "").trim(), role: String(data.get("role") || "agent") };
    if (password) payload.password = password;
    setBusy(true); setModalError(null);
    try {
      if (editing === "new") {
        await api(`/clients/${clientId}/portal-users`, { method: "POST", body: JSON.stringify(payload) });
        toast.success(t("clients.detail.portalUserAdded"));
      } else {
        await api(`/clients/${clientId}/portal-users/${editing.id}`, { method: "PATCH", body: JSON.stringify(payload) });
        toast.success(t("clients.detail.portalUserSaved"));
      }
      setEditing(null);
      await load();
    } catch (err) { setModalError(messageFrom(err)); } finally { setBusy(false); }
  }
  async function setActive(u: PortalUser, active: boolean) {
    setBusy(true); setModalError(null);
    try {
      await api(`/clients/${clientId}/portal-users/${u.id}`, { method: "PATCH", body: JSON.stringify({ is_active: active }) });
      setSuspending(null);
      await load();
    } catch (err) { if (suspending) setModalError(messageFrom(err)); else toast.error(messageFrom(err)); } finally { setBusy(false); }
  }
  async function removeUser() {
    if (!deleting) return;
    setBusy(true); setModalError(null);
    try {
      await api(`/clients/${clientId}/portal-users/${deleting.id}`, { method: "DELETE" });
      toast.success(t("clients.detail.portalUserRemoved"));
      setDeleting(null);
      await load();
    } catch (err) { setModalError(messageFrom(err)); } finally { setBusy(false); }
  }

  const creating = editing === "new";
  const current = editing && editing !== "new" ? editing : null;
  // The first person at a business runs it; the ones added later work the inbox.
  const defaultRole: PortalRole = current ? current.role : (users?.length ? "agent" : "admin");
  const roleLabel = (role: PortalRole) => (role === "admin" ? t("clients.detail.portalUserRoleAdmin") : t("clients.detail.portalUserRoleAgent"));

  return <section className="form-section"><div className="section-copy"><h2>{t("clients.detail.portalUsersTitle")}</h2><p>{t("clients.detail.portalUsersCopy")}</p></div><div className="form-fields">
    {users === null ? <ListRowsSkeleton rows={2} /> : users.length ? <div className="table-shell"><table className="data-table"><tbody>
      {users.map((u) => <tr key={u.id}>
        <td><span className="entity-cell"><span className="agent-avatar"><UserRound size={17} /></span><span><span className="name-line"><strong>{u.name || u.email}</strong><span className={`mini-badge ${u.role === "admin" ? "human" : "ai"}`}>{roleLabel(u.role)}</span><StatusBadge active={u.is_active} /></span>{u.name && <small style={{ display: "block", color: "#89909d" }}>{u.email}</small>}</span></span></td>
        <td className="row-end"><span className="row-actions">
          <button type="button" className="button secondary small" disabled={busy} onClick={() => (u.is_active ? (setModalError(null), setSuspending(u)) : setActive(u, true))}>{u.is_active ? <><UserX size={14} /> {t("clients.detail.portalUserSuspend")}</> : <><UserCheck size={14} /> {t("clients.detail.portalUserActivate")}</>}</button>
          <span className="row-sep" />
          <button type="button" className="icon-button" onClick={() => open(u)} aria-label={t("clients.detail.portalUserEdit")} title={t("clients.detail.portalUserEdit")}><Pencil size={15} /></button>
          <button type="button" className="icon-button danger-icon" onClick={() => { setModalError(null); setDeleting(u); }} aria-label={t("clients.detail.portalUserRemove")} title={t("clients.detail.portalUserRemove")}><Trash2 size={15} /></button>
        </span></td>
      </tr>)}
    </tbody></table></div> : <p className="field-help">{t("clients.detail.portalUsersEmpty")}</p>}
    <button type="button" className="button secondary align-start" onClick={() => open("new")}><UserRound size={15} /> {t("clients.detail.portalUserAdd")}</button>

    <Modal open={editing !== null} title={creating ? t("clients.detail.portalUserAddTitle") : t("clients.detail.portalUserEditTitle", { name: current?.name || current?.email || "" })} description={creating ? t("clients.detail.portalUserAddCopy") : undefined} onClose={() => setEditing(null)}>
      <form key={creating ? "new" : current?.id} className="modal-form" onSubmit={save}>
        <div className="form-grid">
          <label>{t("clients.detail.portalUserName")}<input name="name" required minLength={2} maxLength={160} defaultValue={current?.name || ""} autoFocus /></label>
          <label>{t("clients.detail.portalUserEmail")}<input name="email" required type="email" defaultValue={current?.email || ""} placeholder={t("clients.detail.portalEmailPlaceholder")} /></label>
        </div>
        <label>{t("clients.detail.portalUserRole")}<select name="role" defaultValue={defaultRole}><option value="admin">{t("clients.detail.portalUserRoleAdmin")}</option><option value="agent">{t("clients.detail.portalUserRoleAgent")}</option></select><span className="field-help">{t("clients.detail.portalUserRoleHint")}</span></label>
        {creating ? <div className="form-grid">
          <label>{t("clients.detail.portalUserPassword")}<PasswordInput name="password" required minLength={8} autoComplete="new-password" placeholder={t("clients.detail.portalPasswordMin")} /></label>
          <label>{t("clients.detail.portalUserConfirmPassword")}<PasswordInput name="password_confirm" required minLength={8} autoComplete="new-password" placeholder={t("clients.detail.portalUserConfirmPlaceholder")} /></label>
        </div> : <label>{t("clients.detail.portalUserNewPassword")}<PasswordInput name="password" minLength={8} autoComplete="new-password" placeholder={t("clients.detail.portalUserPasswordKeep")} /><span className="field-help">{t("clients.detail.portalUserNewPasswordHint")}</span></label>}
        {modalError && <Alert>{modalError}</Alert>}
        <div className="modal-actions"><button type="button" className="button" onClick={() => setEditing(null)}>{t("common.cancel")}</button><button className="button primary" disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : creating ? t("clients.detail.portalUserAdd") : t("common.saveChanges")}</button></div>
      </form>
    </Modal>
    <Modal open={suspending !== null} title={t("clients.detail.portalUserSuspendTitle", { name: suspending?.name || suspending?.email || "" })} onClose={() => setSuspending(null)}>
      <div className="modal-form">
        <p className="modal-copy">{t("clients.detail.portalUserSuspendCopy")}</p>
        {modalError && <Alert>{modalError}</Alert>}
        <div className="modal-actions"><button type="button" className="button" onClick={() => setSuspending(null)}>{t("common.cancel")}</button><button type="button" className="button primary" disabled={busy} onClick={() => suspending && setActive(suspending, false)}>{busy ? <LoaderCircle className="spin" size={16} /> : <><UserX size={15} /> {t("clients.detail.portalUserSuspend")}</>}</button></div>
      </div>
    </Modal>
    <Modal open={deleting !== null} title={t("clients.detail.portalUserRemoveTitle", { name: deleting?.name || deleting?.email || "" })} onClose={() => setDeleting(null)}>
      <div className="modal-form">
        <p className="modal-copy">{t("clients.detail.portalUserRemoveCopy")}</p>
        {modalError && <Alert>{modalError}</Alert>}
        <div className="modal-actions"><button type="button" className="button" onClick={() => setDeleting(null)}>{t("common.cancel")}</button><button type="button" className="button danger" disabled={busy} onClick={removeUser}>{busy ? <LoaderCircle className="spin" size={16} /> : <><Trash2 size={15} /> {t("clients.detail.portalUserRemove")}</>}</button></div>
      </div>
    </Modal>
  </div></section>;
}

function PortalDomain({ clientId, domain, onChange }: { clientId: string; domain: ClientDomain | null; onChange: (domain: ClientDomain) => void }) {
  const t = useT();
  const toast = useToast();
  const setDomain = onChange;
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setInput(domain?.domain || ""); }, [domain?.domain]);

  async function save() {
    setBusy(true);
    try { const d = await api<ClientDomain>(`/clients/${clientId}/domain`, { method: "PUT", body: JSON.stringify({ domain: input.trim().toLowerCase() }) }); setDomain(d); toast.success(t("clients.detail.domainSaved")); }
    catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }
  async function verify() {
    setBusy(true);
    try { const d = await api<ClientDomain>(`/clients/${clientId}/domain/verify`, { method: "POST" }); setDomain(d); toast.success(t("clients.detail.domainVerified")); }
    catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true);
    try { const d = await api<ClientDomain>(`/clients/${clientId}/domain`, { method: "DELETE" }); setDomain(d); setInput(""); toast.success(t("clients.detail.domainRemoved")); }
    catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }

  return <section className="form-section domain-section"><div className="section-copy"><h2>{t("clients.detail.domainTitle")}</h2><p>{t("clients.detail.domainCopy")}</p></div><div className="form-fields">
    <label>{t("clients.detail.domainLabel")}<div className="domain-input"><Globe2 size={16} /><input value={input} onChange={(e) => setInput(e.target.value)} placeholder="chat.brand.com" /><button type="button" className="button secondary" onClick={save} disabled={busy || !input.trim()}><Save size={15} /> {t("clients.detail.domainSave")}</button></div></label>
    {domain?.domain && <>
      <div className={`domain-status ${domain.verified ? "ok" : "pending"}`}>{domain.verified ? <><ShieldCheck size={16} /> {t("clients.detail.domainStatusVerified")}</> : <><ShieldAlert size={16} /> {t("clients.detail.domainStatusPending")}</>}</div>
      {!domain.verified && <div className="dns-instructions">
        <p>{t("clients.detail.domainDnsIntro")}</p>
        <table className="dns-table"><thead><tr><th>{t("clients.detail.domainDnsType")}</th><th>{t("clients.detail.domainDnsHost")}</th><th>{t("clients.detail.domainDnsValue")}</th></tr></thead><tbody>
          <tr><td>CNAME</td><td><code>{domain.domain}</code></td><td><code>{t("clients.detail.domainCnameTarget")}</code></td></tr>
          <tr><td>TXT</td><td><code>{domain.txt_host}</code></td><td><code>{domain.txt_value}</code></td></tr>
        </tbody></table>
        <div className="domain-actions"><button type="button" className="button primary" onClick={verify} disabled={busy}>{busy ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />} {t("clients.detail.domainVerify")}</button></div>
      </div>}
      <div className="form-footer"><button type="button" className="button danger" onClick={remove} disabled={busy}><Trash2 size={15} /> {t("clients.detail.domainRemove")}</button></div>
    </>}
  </div></section>;
}

