// Which modules an agency may use. The platform switches each one per agency
// (the Plan tab of the platform panel); every module that exists today defaults
// to on. The catalog below mirrors apps/api/app/agency_features.py: a test
// compares the two lists, so change both together.

export const AGENCY_FEATURES = [
  { key: "clients", default: true },
  { key: "agents", default: true },
  { key: "inbox", default: true },
  { key: "playground", default: true },
  { key: "teams", default: true },
  { key: "templates", default: true },
  { key: "canned", default: true },
  { key: "channels.whatsapp", default: true },
  { key: "channels.whatsapp_cloud", default: true },
  { key: "channels.instagram", default: true },
  { key: "channels.messenger", default: true },
  { key: "channels.webchat", default: true },
  { key: "pipeline", default: true },
  { key: "calendar", default: true },
  { key: "appointments", default: true },
  { key: "professionals", default: true },
  { key: "services", default: true },
  { key: "knowledge", default: true },
  { key: "resources", default: true },
  { key: "storage", default: true },
  { key: "data_store", default: true },
  { key: "reports", default: true },
  { key: "integrations", default: true },
  { key: "branding", default: true },
] as const;

export type AgencyFeature = (typeof AGENCY_FEATURES)[number]["key"];

export const AGENCY_PRESETS: Record<"starter" | "pro" | "full", readonly AgencyFeature[]> = {
  starter: [
    "clients", "agents", "inbox", "playground", "teams", "templates", "canned",
    "channels.webchat", "pipeline", "calendar", "reports", "knowledge",
  ],
  pro: AGENCY_FEATURES.filter((entry) => entry.key !== "storage" && entry.key !== "data_store").map((entry) => entry.key),
  full: AGENCY_FEATURES.map((entry) => entry.key),
};

export const PRESET_NAMES = Object.keys(AGENCY_PRESETS) as (keyof typeof AGENCY_PRESETS)[];

// The channel types the platform can put a *number* on, next to the on/off
// switch: five WhatsApp QR lines, three numbers, one Instagram account. Mirrors
// CATALOG in apps/api/app/channel_quotas.py, which a test keeps equal; listed
// explicitly so adding a channel module never silently grows a quota row.
export const QUOTA_FEATURES = [
  "channels.whatsapp",
  "channels.whatsapp_cloud",
  "channels.instagram",
  "channels.messenger",
  "channels.webchat",
] as const satisfies readonly AgencyFeature[];

export type QuotaFeature = (typeof QUOTA_FEATURES)[number];

export function isQuotaFeature(key: AgencyFeature): key is QuotaFeature {
  return (QUOTA_FEATURES as readonly string[]).includes(key);
}
