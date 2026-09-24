"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Bot, Plus, Search } from "lucide-react";
import { useLanguage } from "@/lib/i18n";
import { businessLabel, useIndustries } from "@/lib/industries";
import { EmptyState, PageHead, StatusBadge } from "@/components/ui";
import { TableSkeleton } from "@/components/skeleton";
import { modelLabel } from "@/lib/providers";
import type { Agent, Client } from "@/types";
import { foldIncludes } from "@/lib/text";
import { AgentsScopeProvider, useAgentsApi, useAgentsScope, type AgentHrefs } from "./scope";

/** The agents list, shared by the agency panel and the client portal (see scope.tsx). */
export function AgentsListView({ apiBase, hrefFor, client }: { apiBase?: string; hrefFor?: AgentHrefs; client?: { id: string; name: string } | null }) {
  return <AgentsScopeProvider apiBase={apiBase} hrefFor={hrefFor} client={client}><AgentsList /></AgentsScopeProvider>;
}

function AgentsList() {
  const { t, lang } = useLanguage();
  const { portal, hrefFor } = useAgentsScope();
  const { api } = useAgentsApi();
  // The industry catalog is an agency screen; the portal's client is one and fixed, so it has no use for it.
  const catalog = useIndustries(!portal);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [search, setSearch] = useState("");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    // The agency filters and lists by client; the portal has exactly one, so it never asks for the list.
    Promise.all([api<Agent[]>("/agents"), portal ? Promise.resolve([] as Client[]) : api<Client[]>("/clients")])
      .then(([a, c]) => { setAgents(a); setClients(c); })
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, [api, portal]);
  const visible = useMemo(() => agents.filter((agent) => (!clientId || agent.client_id === clientId) && foldIncludes(`${agent.name} ${agent.client.name}`, search)), [agents, clientId, search]);
  const canCreate = portal || clients.length > 0;
  return <div className="page"><PageHead eyebrow={t("agents.list.eyebrow")} title={t("agents.list.title")} description={t("agents.list.description")} action={<Link href={hrefFor.create()} className="button primary"><Plus size={18} /> {t("agents.list.newAgent")}</Link>} />
    <div className="toolbar filters"><label className="search-box"><Search size={18} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("agents.list.searchPlaceholder")} /></label>{!portal && <label className="filter-select">{t("agents.list.clientLabel")}<select value={clientId} onChange={(e) => setClientId(e.target.value)}><option value="">{t("agents.list.allClients")}</option>{clients.map((client) => <option value={client.id} key={client.id}>{client.name}</option>)}</select></label>}</div>
    {!loaded ? <TableSkeleton columns={portal ? 4 : 5} /> : visible.length ? <div className="table-shell"><table className="data-table"><thead><tr><th>{t("agents.list.thAgent")}</th>{!portal && <th>{t("agents.list.thClient")}</th>}<th>{t("agents.list.thModel")}</th><th>{t("agents.list.thStatus")}</th><th /></tr></thead><tbody>{visible.map((agent) => <tr key={agent.id}><td><Link href={hrefFor.agent(agent.id)} className="entity-cell"><span className="agent-avatar"><Bot size={18} /></span><span><strong>{agent.name}</strong>{!portal && <small>{businessLabel(catalog, agent.client, lang)}</small>}</span></Link></td>{!portal && <td>{hrefFor.client ? <Link href={hrefFor.client(agent.client_id)} className="table-link">{agent.client.name}</Link> : agent.client.name}</td>}<td>{agent.model ? <span className="entity-cell"><strong>{modelLabel(agent.model)}</strong><small>{agent.model}</small></span> : <span className="muted">{t("agents.list.notConfigured")}</span>}</td><td><StatusBadge active={agent.is_active} /></td><td><Link href={hrefFor.agent(agent.id)} className="row-arrow"><ArrowRight size={17} /></Link></td></tr>)}</tbody></table></div> : <EmptyState icon={<Bot />} title={t("agents.list.emptyTitle")} description={canCreate ? t("agents.list.emptyWithClients") : t("agents.list.emptyNoClients")} action={<Link href={canCreate ? hrefFor.create() : "/clients/new"} className="button primary">{t("agents.list.continue")}</Link>} />}
  </div>;
}
