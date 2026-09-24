// Which functions a client's portal offers. The agency switches each one per client
// (the toggles on the client's tabs); the portal shows only what is on, and the API
// refuses the rest. The catalog below mirrors apps/api/app/portal_features.py: a test
// compares the two lists, so change both together.

import { CHANNEL_TYPES, type ChannelType } from "@/lib/routes";

export const PORTAL_FEATURES = [
  { key: "inbox", default: true },
  { key: "contacts", default: true },
  { key: "pipeline", default: true },
  { key: "calendar", default: true },
  { key: "reports", default: true },
  { key: "teams", default: true },
  { key: "tags", default: true },
  { key: "templates", default: true },
  { key: "canned", default: true },
  { key: "agents", default: false },
  { key: "api", default: false },
  { key: "channels.whatsapp", default: false },
  { key: "channels.whatsapp_cloud", default: false },
  { key: "channels.instagram", default: false },
  { key: "channels.messenger", default: false },
  { key: "channels.webchat", default: false },
] as const;

export type PortalFeature = (typeof PORTAL_FEATURES)[number]["key"];

/** Functions whose portal screen is not built yet: the switch is stored, nothing shows. */
export const FEATURES_WITHOUT_SCREEN: readonly PortalFeature[] = ["api"];

export const CHANNEL_FEATURES: readonly PortalFeature[] = ["channels.whatsapp", "channels.whatsapp_cloud", "channels.instagram", "channels.messenger", "channels.webchat"];

/** The function behind each channel type's portal page (the type is the path segment of lib/routes.ts). */
export const FEATURE_OF_CHANNEL_TYPE: Record<ChannelType, PortalFeature> = {
  whatsapp: "channels.whatsapp",
  "whatsapp-cloud": "channels.whatsapp_cloud",
  instagram: "channels.instagram",
  messenger: "channels.messenger",
  webchat: "channels.webchat",
};

/** The switches shown on each tab of the client page; the Portal tab shows all of them. */
export const FEATURES_BY_CLIENT_TAB: Record<string, readonly PortalFeature[]> = {
  inbox: ["inbox"],
  agents: ["agents"],
  channels: CHANNEL_FEATURES,
  teams: ["teams"],
  tags: ["tags"],
  templates: ["templates"],
  calendar: ["calendar"],
  pipeline: ["pipeline"],
  api: ["api"],
  portal: PORTAL_FEATURES.map((entry) => entry.key),
};

/** A role permission is only as good as the function behind it. */
export const FEATURE_OF_PERMISSION: Record<string, PortalFeature> = {
  "contacts.manage": "contacts",
  "pipeline.manage": "pipeline",
  "calendar.manage": "calendar",
  "reports.view": "reports",
  "teams.manage": "teams",
  "tags.manage": "tags",
  "templates.manage": "templates",
  "canned.manage": "canned",
  "agents.manage": "agents",
};

/** Permissions that hold while ANY of several functions is on (channel management needs at least one channel type). */
const ANY_FEATURE_OF_PERMISSION: Record<string, readonly PortalFeature[]> = {
  "channels.manage": CHANNEL_FEATURES,
};

/** Whether the function behind a permission is on; a permission with no function behind it always is. */
export function permissionFeatureOn(permission: string, enabled: (key: PortalFeature) => boolean): boolean {
  const single = FEATURE_OF_PERMISSION[permission];
  if (single) return enabled(single);
  const any = ANY_FEATURE_OF_PERMISSION[permission];
  return any ? any.some(enabled) : true;
}

/** The channel types switched on for the portal, in the order the overview shows them. */
export function enabledChannelTypes(enabled: (key: PortalFeature) => boolean): ChannelType[] {
  return CHANNEL_TYPES.filter((type) => enabled(FEATURE_OF_CHANNEL_TYPE[type]));
}

const DEFAULTS = new Map<string, boolean>(PORTAL_FEATURES.map((entry) => [entry.key, entry.default]));

/** The client's full set, with any missing key at its default. */
export function normalizeFeatures(stored?: Record<string, boolean> | null): Record<PortalFeature, boolean> {
  const result = {} as Record<PortalFeature, boolean>;
  for (const entry of PORTAL_FEATURES) result[entry.key] = typeof stored?.[entry.key] === "boolean" ? Boolean(stored[entry.key]) : entry.default;
  return result;
}

/** Whether a portal session has a function. A session from an older server carries no list: defaults apply. */
export function hasFeature(enabled: readonly string[] | undefined, key: PortalFeature): boolean {
  return enabled === undefined ? Boolean(DEFAULTS.get(key)) : enabled.includes(key);
}
