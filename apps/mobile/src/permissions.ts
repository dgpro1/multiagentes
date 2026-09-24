import type { Session } from "./api";

/** The portal functions the app knows how to hide, and whether each is on before the server says otherwise. */
export type PortalFeature = "inbox" | "contacts" | "teams" | "reports" | "tags" | "templates" | "canned";

/** What a session from an older server (one that sends no `features` list) is treated as having. */
const DEFAULT_ON: readonly PortalFeature[] = ["inbox", "contacts", "teams", "reports", "tags", "templates", "canned"];

/** A role permission is only as good as the function behind it (same rule as the web portal). */
const FEATURE_OF_PERMISSION: Record<string, PortalFeature> = {
  "contacts.manage": "contacts",
  "reports.view": "reports",
  "teams.manage": "teams",
  "tags.manage": "tags",
  "templates.manage": "templates",
  "canned.manage": "canned",
};

/**
 * Whether the agency has this function switched on for the client. `features`
 * lists the enabled keys; an absent list means an older server, so today's
 * behaviour (the defaults) applies.
 */
export function hasFeature(session: Pick<Session, "features">, key: PortalFeature): boolean {
  return Array.isArray(session.features) ? session.features.includes(key) : DEFAULT_ON.includes(key);
}

/** The server's current grants are authoritative, including an empty list. A grant needs its function to be on. */
export function hasPermission(session: Session, permission: string): boolean {
  if (!Array.isArray(session.permissions) || !session.permissions.includes(permission)) return false;
  const feature = FEATURE_OF_PERMISSION[permission];
  return !feature || hasFeature(session, feature);
}
