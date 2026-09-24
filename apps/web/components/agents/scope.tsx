"use client";

// Where the agent screens run. The agency's screens and the client portal's
// share one implementation (components/agents/*); what differs is the API
// prefix every call carries and how the links between screens are built, so
// both travel here instead of being hard-coded in each component.
//
//   Agency  apiBase ""                       links /agents, /agents/{id}/{tab}
//   Portal  apiBase "/portal/{slug}/manage"  links /portal/{slug}/agents/...
//
// Outside a provider the agency scope applies, so a component used on its own
// (the playground page, say) behaves as it always did.

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { api as rawApi, apiUrl as rawApiUrl } from "@/lib/api";
import { agentPath, portalAgentPath, type AgentTab } from "@/lib/routes";

export type AgentHrefs = {
  /** The list of agents. */
  list: () => string;
  /** The create wizard; the agency can preselect a client. */
  create: (clientId?: string) => string;
  /** One agent, on the given tab (the default tab is the bare address). */
  agent: (id: string, tab?: AgentTab) => string;
  /** The client's own page; null where the client is fixed and there is none to visit (the portal). */
  client: ((clientId: string) => string) | null;
  /** Where the person lands after deleting an agent. */
  afterDelete: (clientId: string) => string;
  /** The playground opened from an agent's header. */
  playground: (agentId: string) => string;
};

export const agencyAgentHrefs: AgentHrefs = {
  list: () => "/agents",
  create: (clientId) => (clientId ? `/agents/new?client=${encodeURIComponent(clientId)}` : "/agents/new"),
  agent: (id, tab = "basics") => agentPath(id, tab),
  client: (clientId) => `/clients/${clientId}`,
  afterDelete: (clientId) => `/clients/${clientId}`,
  playground: () => "/playground",
};

/** The portal's addresses; `urlBase` comes from `portalBase(slug)` (empty on a client's own domain). */
export function portalAgentHrefs(urlBase: string): AgentHrefs {
  return {
    list: () => portalAgentPath(urlBase),
    create: () => portalAgentPath(urlBase, "new"),
    agent: (id, tab = "basics") => portalAgentPath(urlBase, id, tab),
    client: null,
    afterDelete: () => portalAgentPath(urlBase),
    playground: (agentId) => portalAgentPath(urlBase, agentId, "playground"),
  };
}

export type AgentsScope = {
  /** Prefix of every API call: "" for the agency, "/portal/{slug}/manage" in the portal. */
  apiBase: string;
  /** True inside a client portal: agency-only parts (client picker, provider keys, industries) stay out. */
  portal: boolean;
  hrefFor: AgentHrefs;
  /** The portal's own client, the only one an agent can be created for. */
  client: { id: string; name: string } | null;
};

const AGENCY_SCOPE: AgentsScope = { apiBase: "", portal: false, hrefFor: agencyAgentHrefs, client: null };

const ScopeContext = createContext<AgentsScope>(AGENCY_SCOPE);

export function AgentsScopeProvider({ apiBase = "", hrefFor = agencyAgentHrefs, client = null, children }: { apiBase?: string; hrefFor?: AgentHrefs; client?: { id: string; name: string } | null; children: ReactNode }) {
  const clientId = client?.id ?? null;
  const clientName = client?.name ?? null;
  const value = useMemo<AgentsScope>(
    () => ({ apiBase, portal: apiBase !== "", hrefFor, client: clientId ? { id: clientId, name: clientName ?? "" } : null }),
    [apiBase, hrefFor, clientId, clientName],
  );
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useAgentsScope(): AgentsScope {
  return useContext(ScopeContext);
}

/** `api()` and `apiUrl()` with the scope's prefix in front of every path. */
export function useAgentsApi() {
  const { apiBase } = useAgentsScope();
  return useMemo(() => ({
    api: <T,>(path: string, options?: RequestInit) => rawApi<T>(`${apiBase}${path}`, options),
    apiUrl: (path: string) => rawApiUrl(`${apiBase}${path}`),
  }), [apiBase]);
}
