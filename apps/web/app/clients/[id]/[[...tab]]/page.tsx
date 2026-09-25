"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, Bot, Briefcase, Calendar as CalendarIcon, Copy, ExternalLink, FileText, GitBranch, Globe2, Inbox, KeyRound, LoaderCircle, Lock, Pencil, Radio, Save, Settings2, ShieldAlert, ShieldCheck, Stethoscope, Tag, Trash2, UserCheck, UserRound, Users, UserX } from "lucide-react";
import { Alert, EmptyState, Modal, StatusBadge } from "@/components/ui";
import { SectionTabs } from "@/components/section-tabs";
import { ApiIntegrations } from "@/components/api-integrations";
import { CalendarView } from "@/components/calendar-view";
import { PipelineBoard } from "@/components/pipeline-board";
import { ClientDetails } from "@/components/client-details";
import { ProfessionalsView } from "@/components/professionals-view";
import { ServicesView } from "@/components/services-view";
import { GrowingTextarea } from "@/components/growing-textarea";
import { LeadCard } from "@/components/lead-card/lead-card";
import { MergeAuditCard, isMergeActivity } from "@/components/merge-audit-card";
import { UnifiedComposerTop, type ComposerMode } from "@/components/unified-composer-top";
import { AppointmentModal } from "@/components/appointment-modal";
import { AppointmentActivityCard, isAppointmentActivity } from "@/components/appointment-activity-card";
import { VariablesPopover } from "@/components/variables-popover";
import { formatTime } from "@/lib/datetime";
import { LeadAvatarButton } from "@/components/lead-card/avatar-button";
import { LeadScopeProvider, agencyLeadScope } from "@/components/lead-card/scope";
import { useLeadPanel } from "@/components/lead-card/use-lead-panel";
import { RichText } from "@/components/rich-text";
import { TeamsView } from "@/app/portal/[slug]/teams";
import { TagsView } from "@/app/portal/[slug]/tags";
import { TemplatesView } from "@/app/portal/[slug]/templates";
import { FormSkeleton, ListRowsSkeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { PasswordInput } from "@/components/password-input";
import { ChannelDots, channelLabel, isSocialChannel, leadChannels, MessageChannelMark } from "@/lib/channels";
import { useAttachmentOwners, useReplyVia } from "@/lib/linked-threads";
import { SocialReplyNotice, useReplyPolicy } from "@/components/reply-policy";
import { api, apiUrl, messageFrom } from "@/lib/api";
import { CLIENT_TABS, clientPath, tabFromSegments, type ClientTab } from "@/lib/routes";
import { useLanguage, useT } from "@/lib/i18n";
import { businessLabel, useIndustries } from "@/lib/industries";
import type { Attachment, Client, ClientDomain, Conversation, PortalRole, PortalUser } from "@/types";
import { ChannelsOverviewView } from "@/components/channels/channels-overview";
import { PortalFeatureToggle } from "@/components/portal-feature-toggle";
import { FEATURES_BY_CLIENT_TAB } from "@/lib/portal-features";

type Tab = ClientTab;

export default function ClientDetailPage() {
  const { t, lang } = useLanguage();
  const toast = useToast();
  const { id, tab: segments } = useParams<{ id: string; tab?: string[] }>();
  const router = useRouter();
  // The address is the source of truth for the tab (and, in the inbox, the open lead): reload,
  // Back/Forward and shared links all agree (lib/routes.ts).
  const route = tabFromSegments(CLIENT_TABS, segments, "details");
  const tab = route.tab;
  const leadNumber = tab === "inbox" && route.rest[0] && /^\d+$/.test(route.rest[0]) ? Number(route.rest[0]) : undefined;
  const catalog = useIndustries();
  const [client, setClient] = useState<Client | null>(null);
  const [domain, setDomain] = useState<ClientDomain | null>(null);
  // An unknown first segment goes to the default tab, and a legacy /clients/{id}?tab=channels
  // (older channel pages and API redirects still build it) moves to /clients/{id}/channels;
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
    router.replace(`${clientPath(id, target)}${query ? `?${query}` : ""}${window.location.hash}`);
  }, [route.known, route.tab, id, router]);
  const [busy, setBusy] = useState(false);
  const load = () => api<Client>(`/clients/${id}`).then(setClient);
  useEffect(() => { load(); api<ClientDomain>(`/clients/${id}/domain`).then(setDomain); }, [id]);

  async function savePortal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true);
    const data = new FormData(event.currentTarget);
    const payload: Record<string, unknown> = { portal_enabled: data.get("portal_enabled") === "on", portal_slug: data.get("portal_slug"), portal_title: data.get("portal_title") };
    try { setClient(await api<Client>(`/clients/${id}/portal`, { method: "PATCH", body: JSON.stringify(payload) })); toast.success(t("clients.detail.portalUpdated")); }
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
    <Link href="/clients" className="back-link"><ArrowLeft size={17} /> {t("clients.detail.back")}</Link>
    <header className="entity-header"><div className="entity-avatar xl">{client.name.slice(0, 2).toUpperCase()}</div><div><div className="title-line"><h1>{client.name}</h1><StatusBadge active={client.is_active} /></div><p>{businessLabel(catalog, client, lang) || t("clients.detail.industryUndefined")} · {client.agents.length === 1 ? t("clients.detail.agentOne", { count: client.agents.length }) : t("clients.detail.agentMany", { count: client.agents.length })}</p></div><div className="header-actions"><Link href={`/agents/new?client=${client.id}`} className="button primary"><Bot size={17} /> {t("clients.detail.newAgent")}</Link></div></header>
    <SectionTabs<Tab> className="client-tabs" value={tab} tabs={[
      { id: "details", label: t("clients.detail.tabDetails"), icon: Settings2, href: clientPath(client.id, "details") },
      { id: "agents", label: t("clients.detail.tabAgents"), icon: Bot, badge: client.agents.length, href: clientPath(client.id, "agents") },
      { id: "channels", label: t("clients.detail.tabChannels"), icon: Radio, href: clientPath(client.id, "channels") },
      { id: "inbox", label: t("clients.detail.tabInbox"), icon: Inbox, href: clientPath(client.id, "inbox") },
      { id: "teams", label: t("clients.detail.tabTeams"), icon: Users, href: clientPath(client.id, "teams") },
      { id: "professionals", label: t("clients.detail.tabProfessionals"), icon: Stethoscope, href: clientPath(client.id, "professionals") },
      { id: "services", label: t("clients.detail.tabServices"), icon: Briefcase, href: clientPath(client.id, "services") },
      { id: "tags", label: t("clients.detail.tabTags"), icon: Tag, href: clientPath(client.id, "tags") },
      { id: "templates", label: t("clients.detail.tabTemplates"), icon: FileText, href: clientPath(client.id, "templates") },
      { id: "calendar", label: t("clients.detail.tabCalendar"), icon: CalendarIcon, href: clientPath(client.id, "calendar") },
      { id: "pipeline", label: t("clients.detail.tabPipeline"), icon: GitBranch, href: clientPath(client.id, "pipeline") },
      { id: "api", label: t("clients.detail.tabApi"), icon: KeyRound, href: clientPath(client.id, "api") },
      { id: "portal", label: t("clients.detail.tabPortal"), icon: Globe2, href: clientPath(client.id, "portal") },
    ]} />

    {FEATURES_BY_CLIENT_TAB[tab] && <PortalFeatureToggle client={client} keys={FEATURES_BY_CLIENT_TAB[tab]} onChange={setClient} title={tab === "portal" ? t("clients.detail.portalFeaturesTitle") : undefined} />}
    {tab === "details" && <ClientDetails mode="agency" clientId={client.id} client={client} onChange={setClient} />}

    {tab === "agents" && (client.agents.length ? <div className="table-shell"><table className="data-table"><thead><tr><th>{t("clients.detail.colAgent")}</th><th>{t("clients.detail.colStatus")}</th><th /></tr></thead><tbody>{client.agents.map((agent) => <tr key={agent.id}><td><Link className="entity-cell" href={`/agents/${agent.id}`}><span className="agent-avatar"><Bot size={18} /></span><strong>{agent.name}</strong></Link></td><td><StatusBadge active={agent.is_active} /></td><td><Link className="row-arrow" href={`/agents/${agent.id}`}><ArrowRight size={17} /></Link></td></tr>)}</tbody></table></div> : <EmptyState icon={<Bot />} title={t("clients.detail.agentsEmptyTitle")} description={t("clients.detail.agentsEmptyDescription")} action={<Link href={`/agents/new?client=${client.id}`} className="button primary">{t("clients.detail.createAgent")}</Link>} />)}

    {tab === "channels" && <ChannelsOverviewView client={{ id: client.id, name: client.name }} />}

    {tab === "inbox" && <ClientInbox clientId={client.id} urlNumber={leadNumber} />}

    {/* Teams and WhatsApp templates are the client's own, managed here or from its portal; the views are the portal's, pointed at the agency routes. */}
    {tab === "teams" && <div className="embedded-portal-view"><TeamsView base={`/clients/${client.id}`} /></div>}
    {tab === "professionals" && <div className="embedded-portal-view"><ProfessionalsView apiBase={`/clients/${client.id}`} canManage timezone={client.timezone} /></div>}
    {tab === "services" && <div className="embedded-portal-view"><ServicesView apiBase={`/clients/${client.id}`} canManage currency={client.currency} /></div>}
    {tab === "tags" && <div className="embedded-portal-view"><TagsView base={`/clients/${client.id}/contact-tags`} canManage /></div>}
    {tab === "templates" && <div className="embedded-portal-view"><TemplatesView base={`/clients/${client.id}`} /></div>}
    {tab === "calendar" && <CalendarView base={`/clients/${client.id}`} canManage />}
    {tab === "pipeline" && <PipelineBoard base={`/clients/${client.id}`} canManage />}
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

function ClientInbox({ clientId, urlNumber }: { clientId: string; urlNumber?: number }) {
  const { t, lang } = useLanguage();
  const toast = useToast();
  const router = useRouter();
  const [items, setItems] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [busy, setBusy] = useState(false);
  // A lead that absorbed others has several threads: the composer picks one and every reply names it.
  const replyVia = useReplyVia(selected);
  const owners = useAttachmentOwners(selected);
  const policy = useReplyPolicy(replyVia.policyConversation);
  // The lead card beside the thread.
  const { open: leadPanelOpen, setOpen: setLeadOpen, overlay: leadOverlay, attachLayout: attachLead } = useLeadPanel();
  const closeLead = useCallback(() => setLeadOpen(false), [setLeadOpen]);
  const leadScope = useMemo(() => agencyLeadScope(clientId), [clientId]);
  const selectedId = selected?.id;
  const attachmentUrl = useCallback((attachment: Attachment) => apiUrl(`/conversations/${owners.get(attachment.id) ?? selectedId}/attachments/${attachment.id}`), [selectedId, owners]);
  const leadOpen = Boolean(selected) && leadPanelOpen;
  const load = async () => { setItems(await api<Conversation[]>(`/conversations?client_id=${clientId}`)); };
  const [loadedInbox, setLoadedInbox] = useState(false);
  useEffect(() => { load().catch(() => {}).finally(() => setLoadedInbox(true)); }, [clientId]);
  // The lead in the address decides which thread is open (Back, Forward and pasted links land here);
  // a click below fetches the thread first, so the selection already matches by the time the address changes.
  useEffect(() => {
    if (urlNumber === undefined) { setSelected(null); return; }
    if (selected?.number === urlNumber) return;
    let cancelled = false;
    api<Conversation>(`/clients/${clientId}/conversations/number/${urlNumber}`)
      .then((detail) => {
        if (cancelled) return;
        setSelected(detail);
        // An old number of a merged lead answers with the primary: move the address to it.
        if (detail.number !== urlNumber) router.replace(clientPath(clientId, "inbox", detail.number));
      })
      .catch(() => { if (!cancelled) router.replace(clientPath(clientId, "inbox")); });
    return () => { cancelled = true; };
    // Only the address drives this; `selected` is read to skip a lead that is already open.
  }, [urlNumber, clientId]);
  async function choose(item: { id: string }) {
    const detail = await api<Conversation>(`/conversations/${item.id}`);
    setSelected(detail);
    if (detail.number !== urlNumber) router.push(clientPath(clientId, "inbox", detail.number));
  }
  async function mode(next: "ai" | "human") { if (!selected) return; setSelected(await api<Conversation>(`/conversations/${selected.id}/mode`, { method: "PATCH", body: JSON.stringify({ mode: next }) })); await load(); }
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const [composerMode, setComposerMode] = useState<ComposerMode>("chat");
  const [appointmentModalOpen, setAppointmentModalOpen] = useState(false);
  const [variablesOpen, setVariablesOpen] = useState(false);
  const [variablesQuery, setVariablesQuery] = useState("");
  const [draft, setDraft] = useState("");

  function insertTextAtCursor(text: string, replaceTriggerChar?: string) {
    const field = composerRef.current;
    if (!field) return;
    const start = field.selectionStart ?? field.value.length;
    const end = field.selectionEnd ?? start;
    const full = field.value;
    const before = full.slice(0, start);
    const after = full.slice(end);

    let newBefore = before;
    const lastBracket = before.lastIndexOf("[");
    if (lastBracket !== -1) {
      const textBetween = before.slice(lastBracket + 1);
      if (!textBetween.includes("]") && !textBetween.includes("\n")) {
        newBefore = before.slice(0, lastBracket);
      }
    } else if (replaceTriggerChar && before.endsWith(replaceTriggerChar)) {
      newBefore = before.slice(0, before.length - replaceTriggerChar.length);
    }

    const nextVal = newBefore + text + after;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
    if (setter) {
      setter.call(field, nextVal);
    } else {
      field.value = nextVal;
    }
    field.dispatchEvent(new Event("input", { bubbles: true }));
    setDraft(nextVal);
    setVariablesQuery("");
    setVariablesOpen(false);
    const newPos = newBefore.length + text.length;
    setTimeout(() => {
      field.focus();
      field.setSelectionRange(newPos, newPos);
    }, 0);
  }

  async function sendNote(content: string) {
    if (!selected || busy || !content.trim()) return;
    setBusy(true);
    try {
      setSelected(await api<Conversation>(`/conversations/${selected.id}/notes`, {
        method: "POST",
        body: JSON.stringify({ content: content.trim() }),
      }));
      if (composerRef.current) composerRef.current.value = "";
      setDraft("");
      setComposerMode("chat");
      await load();
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); composerRef.current?.focus(); }
  }

  async function reply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const content = (data.get("content") as string) || draft;
    if (composerMode === "note") {
      await sendNote(content);
      return;
    }
    if (!policy.canReply) return;
    setBusy(true);
    try {
      setSelected(await api<Conversation>(`/conversations/${selected.id}/reply`, { method: "POST", body: JSON.stringify({ content: content.trim(), ...replyVia.payload }) }));
      form.reset();
      setDraft("");
      await load();
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }
  if (!loadedInbox) return <ListRowsSkeleton rows={5} />;
  if (!items.length) return <EmptyState icon={<Inbox />} title={t("clients.detail.inboxEmptyTitle")} description={t("clients.detail.inboxEmptyDescription")} />;
  return <div ref={attachLead} className={`inbox-layout${leadOpen ? " has-lead" : ""}${leadOverlay ? " lead-overlay" : ""}`}><aside className="inbox-list"><header><strong>{t("clients.detail.conversations")}</strong><span>{items.length}</span></header>{items.map((item) => <button key={item.id} className={selected?.id === item.id ? "active" : ""} onClick={() => choose(item)}><span className="entity-avatar tiny"><UserRound size={15} /></span><span><strong>{item.title}</strong><small>#{item.number} · {leadChannels(item).length > 1 ? <ChannelDots channels={leadChannels(item)} t={t} /> : channelLabel(item.channel, t)} · {item.mode === "human" ? t("clients.detail.modeHuman") : t("clients.detail.modeAi")}</small></span></button>)}</aside><section className="inbox-thread">{selected && <><header><LeadAvatarButton channel={selected.channel} open={leadOpen} onClick={() => setLeadOpen(!leadPanelOpen)} /><div><strong>{selected.title}</strong><small>#{selected.number} · {channelLabel(selected.channel, t)}</small></div><button className={`mode-toggle ${selected.mode}`} onClick={() => mode(selected.mode === "ai" ? "human" : "ai")}>{selected.mode === "ai" ? t("clients.detail.takeControl") : t("clients.detail.returnToAi")}</button></header><div className="inbox-messages">{selected.messages?.map((message) => {
    if (isMergeActivity(message)) return <MergeAuditCard key={message.id} message={message} />;
    if (isAppointmentActivity(message)) return <AppointmentActivityCard key={message.id} message={message} />;
    const stamp = formatTime(message.created_at, lang);
    if (message.kind === "note") {
      return (
        <div key={message.id} className="internal-note-card">
          <div className="internal-note-header">
            <Lock size={12} />
            <span>{message.sender_name || t("clients.detail.senderAgent")} · {t("inbox.internalNoteBadge")}</span>
            <time>{stamp}</time>
          </div>
          <div className="internal-note-content">
            <RichText text={message.content} />
          </div>
        </div>
      );
    }
    return (
      <div key={message.id} className={`inbox-message ${message.role}`}>
        <small>{replyVia.multi && <MessageChannelMark channel={message.channel} t={t} />}{message.sender_name || (message.role === "assistant" ? t("clients.detail.senderAgent") : t("clients.detail.senderVisitor"))}</small>
        <p><RichText text={message.content} /></p>
      </div>
    );
  })}</div><SocialReplyNotice conversation={selected} blocked={policy.blocked} humanOnly={policy.humanOnly} />
  <div className={`composer-box${composerMode === "note" ? " note-mode" : ""}`} style={{ position: "relative", margin: "10px 16px 14px" }}>
    <UnifiedComposerTop
      mode={composerMode}
      onModeChange={setComposerMode}
      threads={replyVia.threads}
      channel={replyVia.thread?.channel ?? selected.channel}
      via={replyVia.via}
      onViaChange={replyVia.setVia}
      onOpenVariables={() => { setVariablesQuery(""); setVariablesOpen((v) => !v); }}
      onOpenAppointmentModal={() => setAppointmentModalOpen(true)}
    />
    <VariablesPopover
      open={variablesOpen}
      onClose={() => { setVariablesOpen(false); setVariablesQuery(""); }}
      onSelect={(val) => insertTextAtCursor(val)}
      query={variablesQuery}
      contactValues={{
        contact_name: selected.contact_name || selected.title,
        contact_phone: selected.contact_phone || (isSocialChannel(selected.channel) ? "" : (selected.external_chat_id || "").split("@")[0]),
        contact_email: selected.contact_email,
      }}
      leadNumber={selected.number}
      dealValue={selected.deal_value}
      channel={selected.channel}
    />
    <form className="inbox-composer" style={{ border: "none", padding: "8px 12px 10px", margin: 0 }} onSubmit={reply}>
      <GrowingTextarea
        ref={composerRef}
        name="content"
        placeholder={
          composerMode === "note"
            ? (t("inbox.composerNotePlaceholder") || "Escribe una nota interna para el equipo...")
            : selected.mode === "human"
            ? t("clients.detail.composerHuman")
            : t("clients.detail.composerLocked")
        }
        disabled={composerMode === "note" ? busy : (!policy.canReply || busy)}
        required={composerMode === "note" ? true : true}
        onChange={(e) => {
          const val = e.target.value;
          setDraft(val);
          const cursor = e.target.selectionStart ?? val.length;
          const before = val.slice(0, cursor);
          const lastBracket = before.lastIndexOf("[");
          if (lastBracket !== -1) {
            const textBetween = before.slice(lastBracket + 1);
            if (!textBetween.includes("]") && !textBetween.includes("\n") && textBetween.length <= 30) {
              setVariablesQuery(textBetween);
              setVariablesOpen(true);
              return;
            }
          }
          if (variablesQuery) {
            setVariablesQuery("");
            setVariablesOpen(false);
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "[" || e.code === "BracketLeft") {
            setVariablesOpen(true);
            setVariablesQuery("");
          }
          if (variablesOpen && e.key === "Escape") {
            setVariablesOpen(false);
            setVariablesQuery("");
          }
        }}
      />
      <button
        className={composerMode === "note" ? (draft.trim() ? "button primary small" : "button small") : ""}
        style={composerMode === "note" ? { backgroundColor: draft.trim() ? "#f59e0b" : undefined, borderColor: draft.trim() ? "#f59e0b" : undefined, color: draft.trim() ? "#fff" : undefined } : undefined}
        disabled={composerMode === "note" ? (busy || !draft.trim()) : (!policy.canReply || busy)}
      >
        {composerMode === "note" ? (t("inbox.composerSaveNote") || "Guardar nota") : t("clients.detail.send")}
      </button>
    </form>
  </div></>}{!selected && <div className="inline-empty"><Inbox size={22} /><div><strong>{t("clients.detail.selectConversation")}</strong></div></div>}</section>{leadOpen && selected && <LeadScopeProvider scope={leadScope}><LeadCard conversationId={selected.id} number={selected.number} messages={selected.messages ?? []} urlFor={attachmentUrl} overlay={leadOverlay} onClose={closeLead} onChanged={() => { load().catch(() => {}); }} onMerged={(primary) => { void choose({ id: primary.conversation_id }).catch(() => {}); load().catch(() => {}); }} syncKey={selected.messages?.at(-1)?.id} /></LeadScopeProvider>}{appointmentModalOpen && selected && (
    <AppointmentModal
      open={appointmentModalOpen}
      onClose={() => setAppointmentModalOpen(false)}
      base={`/clients/${clientId}`}
      conversationId={selected.id}
      contactId={selected.contact_id}
      onSuccess={() => {
        void choose({ id: selected.id });
        load().catch(() => {});
      }}
    />
  )}</div>;
}
