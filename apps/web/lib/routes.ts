// The URL scheme, in one place. Every screen and every lead has its own address,
// so a link can be shared, bookmarked or opened in a new tab, and Back, Forward
// and reload keep the person where they were.
//
//   Portal   /portal/{slug}/inbox            the list
//            /portal/{slug}/inbox/{number}   one lead (its short number in the client)
//            /portal/{slug}/contacts[/{id}]  /pipeline  /calendar  /reports
//            /portal/{slug}/settings[/{tab}]
//   Agency   /clients/{id}[/{tab}]           /clients/{id}/inbox/{number}
//            /agents/{id}[/{tab}]
//
// A client's own domain serves the portal from the root (proxy.ts rewrites it to
// /portal/{slug}), so the portal prefix is worked out from where the page is.

export const PORTAL_VIEWS = ["inbox", "contacts", "calendar", "pipeline", "reports", "settings"] as const;
export type PortalView = (typeof PORTAL_VIEWS)[number];

export const CLIENT_TABS = ["details", "agents", "channels", "inbox", "teams", "tags", "templates", "calendar", "pipeline", "api", "portal"] as const;
export type ClientTab = (typeof CLIENT_TABS)[number];

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
};

export function parsePortalPath(segments?: string[]): PortalRoute {
  const [first, second] = segments ?? [];
  const view = PORTAL_VIEWS.find((value) => value === first);
  if (!view) return { view: "inbox", known: false };
  const route: PortalRoute = { view, known: true };
  if (view === "inbox" && second && /^\d+$/.test(second)) route.number = Number(second);
  if (view === "contacts" && second) route.contactId = second;
  if (view === "settings" && second) route.tab = second;
  return route;
}

/** A client's address in the agency panel; "details" is the bare `/clients/{id}`. */
export function clientPath(id: string, tab: ClientTab = "details", leadNumber?: number | string | null): string {
  if (tab === "details") return `/clients/${id}`;
  return `/clients/${id}/${tab}${tab === "inbox" && leadNumber ? `/${leadNumber}` : ""}`;
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
