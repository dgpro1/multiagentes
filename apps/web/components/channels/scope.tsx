"use client";

// Where the channel screens run. The agency's screens (a client's Channels tab
// and its five channel pages) and the client portal's share one implementation
// (components/channels/*); what differs is the API prefix every call carries,
// how the links between screens are built, and which client it is, so all of
// it travels here instead of being hard-coded in each component. Same idea as
// components/agents/scope.tsx.
//
//   Agency  apiBase ""                       links /clients/{slug}/channels[/{type}]
//   Portal  apiBase "/portal/{slug}/manage"  links /portal/{slug}/channels[/{type}]
//
// Outside a provider the agency scope applies, and the client is the `[slug]` of
// the address, so the agency's route files stay one-liners.

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { useParams } from "next/navigation";
import { api as rawApi, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { clientChannelPath, portalChannelPath, type ChannelType } from "@/lib/routes";
import type { Client } from "@/types";

export type ChannelHrefs = {
  /** The client's channels: the agency's Channels tab, the portal's Channels screen. */
  overview: () => string;
  /** One channel type's page. The OAuth and hosted-page flows come back to it. */
  type: (type: ChannelType) => string;
};

export function agencyChannelHrefs(clientSlug: string): ChannelHrefs {
  return { overview: () => clientChannelPath(clientSlug), type: (type) => clientChannelPath(clientSlug, type) };
}

/** The portal's addresses; `urlBase` comes from `portalBase(slug)` (empty on a client's own domain). */
export function portalChannelHrefs(urlBase: string): ChannelHrefs {
  return { overview: () => portalChannelPath(urlBase), type: (type) => portalChannelPath(urlBase, type) };
}

export type ChannelsScope = {
  /** Prefix of every API call: "" for the agency, "/portal/{slug}/manage" in the portal. */
  apiBase: string;
  /** True inside a client portal: the client is the session's, and there is no other client to go back to. */
  portal: boolean;
  hrefFor: ChannelHrefs;
  /** The client whose channels these are (the name is empty in the agency until the page has loaded it). */
  client: { id: string; slug: string; name: string };
};

const ScopeContext = createContext<ChannelsScope | null>(null);

export function ChannelsScopeProvider({ apiBase = "", hrefFor, client, children }: { apiBase?: string; hrefFor?: ChannelHrefs; client?: { id: string; slug: string; name: string } | null; children: ReactNode }) {
  const params = useParams<{ slug?: string }>();
  const key = client?.slug ?? params.slug ?? "";
  const value = useMemo<ChannelsScope>(
    () => ({ apiBase, portal: apiBase !== "", hrefFor: hrefFor ?? agencyChannelHrefs(key), client: { id: client?.id ?? "", slug: key, name: client?.name ?? "" } }),
    [apiBase, hrefFor, client?.id, key, client?.name],
  );
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useChannelsScope(): ChannelsScope {
  const scope = useContext(ScopeContext);
  if (!scope) throw new Error("Channel screens must render inside ChannelsScopeProvider");
  return scope;
}

/** `api()` with the scope's prefix in front of every path. */
export function useChannelsApi() {
  const { apiBase } = useChannelsScope();
  return useMemo(() => ({
    api: <T,>(path: string, options?: RequestInit) => rawApi<T>(`${apiBase}${path}`, options),
  }), [apiBase]);
}

/** The scope's client and how to load it with its agents. The agency reads
 * `/clients/{id}` (by UUID) or resolves its address slug first; the portal
 * has only the list route, of which its own client is the one row it may see. */
export function useChannelClient() {
  const t = useT();
  const { portal, client } = useChannelsScope();
  const { api } = useChannelsApi();
  const loadClient = useCallback(async (): Promise<Client> => {
    if (!portal) {
      if (client.id) return api<Client>(`/clients/${client.id}`);
      return api<Client>(`/clients/by-slug/${encodeURIComponent(client.slug)}`);
    }
    const own = (await api<Client[]>("/clients")).find((row) => row.id === client.id);
    if (!own) throw new ApiError(t("social.loadFailed"), 404);
    return own;
  }, [api, portal, client.id, client.slug, t]);
  return { clientId: client.id, loadClient };
}

/** The label of the link that leads from a channel page back to the client's channels. */
export function useBackToChannels() {
  const t = useT();
  const { portal } = useChannelsScope();
  return useCallback((clientName: string) => portal ? t("portal.inbox.nav.channelsBack") : t("clients.whatsapp.back", { name: clientName }), [portal, t]);
}
