// The URL scheme, in one place. Every screen and every lead has its own address,
// so a link can be shared, bookmarked or opened in a new tab, and Back, Forward
// and reload keep the person where they were.
//
//   Portal   /portal/{slug}/inbox            the list
//            /portal/{slug}/inbox/{number}   one lead (its short number in the client)
//            /portal/{slug}/contacts[/{id}]  /pipeline  /calendar  /reports
//            /portal/{slug}/settings[/{tab}]
//            /portal/{slug}/agents[/new]     /agents/{id}[/{basics|knowledge|tools|playground}]
//            /portal/{slug}/channels[/{whatsapp|whatsapp-cloud|instagram|messenger|webchat}]
//            /portal/{slug}/api
//            /portal/{slug}/details          the client's own business details
//            /portal/{slug}/professionals    the staff and their weekly hours
//   Agency   /clients/{slug}[/{tab}]         /clients/{slug}/inbox/{number}
//            /clients/{slug}/channels/{whatsapp|whatsapp-cloud|instagram|messenger|webchat}
//            /agents/{id}[/{tab}]
//
// A client's own domain serves the portal from the root (proxy.ts rewrites it to
// /portal/{slug}), so the portal prefix is worked out from where the page is.

export const PORTAL_VIEWS = ["inbox", "contacts", "calendar", "pipeline", "reports", "agents", "channels", "api", "details", "professionals", "services", "library", "settings"] as const;
export type PortalView = (typeof PORTAL_VIEWS)[number];

export const CLIENT_TABS = ["details", "agents", "channels", "inbox", "teams", "professionals", "services", "library", "database", "tags", "templates", "calendar", "pipeline", "api", "portal"] as const;
export type ClientTab = (typeof CLIENT_TABS)[number];

// In the order the channel cards are shown.
export const CHANNEL_TYPES = ["whatsapp-cloud", "whatsapp", "webchat", "instagram", "messenger"] as const;
export type ChannelType = (typeof CHANNEL_TYPES)[number];

export const AGENT_TABS = ["basics", "knowledge", "tools", "playground"] as const;
export type AgentTab = (typeof AGENT_TABS)[number];

/** "/portal/{slug}" on the app's own host, "" on a client's own domain. */
export function portalBase(slug: string): string {
  if (typeof window === "undefined") return `/portal/${slug}`;
  return window.location.pathname.startsWith(`/portal/${slug}`) ? `/portal/${slug}` : "";
}

/** A portal address: `portalPath(base, "inbox", 42)` -> "/portal/x/inbox/42". */
export function portalPath(base: string, view: PortalView, id?: number | string | null): string {
  const path = `${base}/${view}${id !== undefined && id !== null && id !== "" ? `/${id}` : ""}`;
  return path || "/";
}

export type PortalRoute = {
  view: PortalView;
  /** False when the first segment is missing or not a screen: the caller redirects to the inbox. */
  known: boolean;
  /** The lead's short number, on /inbox/{number}. */
  number?: number;
  /** The contact's id, on /contacts/{id}. */
  contactId?: string;
  /** The settings tab, on /settings/{tab}. */
  tab?: string;
  /** On /agents/{id}: the agent's id, or "new" for the create wizard. */
  agentId?: string;
  /** On /agents/{id}/...: the segments after the id (the agent's tab). */
  agentSegments?: string[];
  /** On /channels/{type}: which channel type's page is open (absent on the overview). */
  channelType?: ChannelType;
};

export function parsePortalPath(segments?: string[]): PortalRoute {
  const [first, second, ...rest] = segments ?? [];
  const view = PORTAL_VIEWS.find((value) => value === first);
  if (!view) return { view: "inbox", known: false };
  const route: PortalRoute = { view, known: true };
  if (view === "inbox" && second && /^\d+$/.test(second)) route.number = Number(second);
  if (view === "contacts" && second) route.contactId = second;
  if (view === "settings" && second) route.tab = second;
  if (view === "agents" && second) { route.agentId = second; route.agentSegments = rest; }
  if (view === "channels" && second) route.channelType = CHANNEL_TYPES.find((value) => value === second);
  return route;
}

/** A portal channel address: the overview, or one channel type's page. */
export function portalChannelPath(base: string, type?: ChannelType | null): string {
  return type ? `${base}/channels/${type}` : `${base}/channels`;
}

/** A legacy agency address names the client by UUID; current ones use its slug. */
export const CLIENT_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A client's channel address in the agency panel: the Channels tab, or one channel type's page. */
export function clientChannelPath(clientSlug: string, type?: ChannelType | null): string {
  return type ? `/clients/${clientSlug}/channels/${type}` : `/clients/${clientSlug}/channels`;
}

/** A portal agent address: the list, `"new"` for the wizard, or an agent with its tab ("basics" is the bare id). */
export function portalAgentPath(base: string, agentId?: string | null, tab: AgentTab = "basics"): string {
  if (!agentId) return `${base}/agents`;
  if (agentId === "new" || tab === "basics") return `${base}/agents/${agentId}`;
  return `${base}/agents/${agentId}/${tab}`;
}

/** A client's address in the agency panel; "details" is the bare `/clients/{slug}`. */
export function clientPath(slug: string, tab: ClientTab = "details", leadNumber?: number | string | null): string {
  if (tab === "details") return `/clients/${slug}`;
  return `/clients/${slug}/${tab}${tab === "inbox" && leadNumber ? `/${leadNumber}` : ""}`;
}

/** An agent's address; "basics" is the bare `/agents/{id}`. */
export function agentPath(id: string, tab: AgentTab = "basics"): string {
  return tab === "basics" ? `/agents/${id}` : `/agents/${id}/${tab}`;
}

/** Reads the first segment of a catch-all route as one of `tabs`, or the default. */
export function tabFromSegments<T extends string>(tabs: readonly T[], segments: string[] | undefined, fallback: T): { tab: T; known: boolean; rest: string[] } {
  const [first, ...rest] = segments ?? [];
  if (!first) return { tab: fallback, known: true, rest };
  const tab = tabs.find((value) => value === first);
  return tab ? { tab, known: true, rest } : { tab: fallback, known: false, rest };
}

// The platform panel (superadmin): its own address space, resolved the same
// way as clients — slugs in page addresses, the REST API keeps UUIDs.
export const PLATFORM_AGENCY_TABS = ["summary", "plan", "clients", "usage", "infrastructure"] as const;
export type PlatformAgencyTab = (typeof PLATFORM_AGENCY_TABS)[number];

/** A platform agency address: "/superadmin/agencies/{slug}" for the summary. */
export function platformAgencyPath(slug: string, tab: PlatformAgencyTab = "summary"): string {
  return tab === "summary" ? `/superadmin/agencies/${slug}` : `/superadmin/agencies/${slug}/${tab}`;
}

/** A platform client address inside its agency. */
export function platformClientPath(agencySlug: string, clientSlug: string): string {
  return `/superadmin/agencies/${agencySlug}/clients/${clientSlug}`;
}
