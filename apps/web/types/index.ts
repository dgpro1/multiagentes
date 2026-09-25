export type User = {
  id: string;
  name: string;
  email: string;
  role: string;
  agency: Agency;
};

export type Agency = { id: string; name: string; slug: string; brand_color: string; logo_url: string | null };

export type AgentSummary = { id: string; name: string; is_active: boolean };

export type IndustryLabel = { en: string; es: string };
export type BusinessType = { code: string; label: IndustryLabel };
export type Industry = { code: string; label: IndustryLabel; types: BusinessType[] };

export type Client = {
  id: string;
  name: string;
  industry: string;
  business_type: string;
  business_custom: string;
  timezone: string;
  is_active: boolean;
  portal_slug: string;
  portal_enabled: boolean;
  /** Which functions the client's portal offers (see lib/portal-features.ts). */
  portal_features: Record<string, boolean>;
  portal_title: string;
  portal_domain: string | null;
  portal_domain_verified: boolean;
  logo_url: string | null;
  /** The person in charge on the client's side (shown as the default responsible user of a lead). */
  owner_name: string | null;
  /** ISO 4217 code the client's deal values are written in. */
  currency: string;
  address: string | null;
  google_maps_url: string | null;
  business_hours: WeeklyHours | null;
  agents: AgentSummary[];
  created_at: string;
  updated_at: string;
};

export type PortalRole = "admin" | "agent";
export type PortalUser = {
  id: string;
  name: string;
  email: string;
  role: PortalRole;
  is_active: boolean;
  devices: number;
  created_at: string;
};

export type ClientDomain = {
  domain: string | null;
  verified: boolean;
  txt_host: string | null;
  txt_value: string | null;
};

export type Agent = {
  id: string;
  client_id: string;
  provider: string;
  name: string;
  instructions: string;
  personality: string;
  brief_summary: string;
  brief_products: string;
  brief_audience: string;
  brief_policies: string;
  brief_dos: string;
  brief_donts: string;
  model: string;
  prompt_language: "en" | "es";
  temperature: number;
  max_tokens: number;
  memory_limit: number;
  phone_handover_minutes: number;
  reply_delay_min_seconds: number;
  reply_delay_max_seconds: number;
  image_enabled: boolean;
  image_model: string;
  audio_enabled: boolean;
  audio_model: string;
  embedding_model: string;
  is_active: boolean;
  client: Client;
  created_at: string;
  updated_at: string;
};

export type Provider = {
  provider: string;
  label: string;
  configured: boolean;
  // "agency": a key of the agency's own; "deployment": one the deployment lends; "none": no key.
  source?: "agency" | "deployment" | "none";
  api_key_masked: string;
};

export type ProviderTest = { ok: boolean; message: string; models: string[] };

export type KnowledgeDocument = {
  id: string;
  filename: string;
  status: "processed" | "error" | "pending";
  error_message: string | null;
  character_count: number;
  created_at: string;
  // Embedding model of the stored chunks; null when nothing is indexed.
  indexed_model: string | null;
  chunk_count: number;
};
export type EmbeddingModelInfo = { id: string; provider: string; label: string; context_window: number; input_price_per_1k: number; note: string };

export type QAPair = { id: string; question: string; answer: string };

export type ToolParam = { name: string; type: "string" | "number" | "integer" | "boolean"; description: string; required: boolean };
export type McpCachedTool = { name: string; description: string; input_schema?: Record<string, unknown>; read_only?: boolean; destructive?: boolean };
export type AgentTool = {
  id: string;
  agent_id: string;
  type: "http" | "mcp";
  name: string;
  description: string;
  enabled: boolean;
  url: string;
  http_method: string;
  prompt_instructions: string;
  body_params: ToolParam[];
  query_params: ToolParam[];
  timeout_seconds: number;
  transport: "sse" | "streamable_http";
  cached_tools: McpCachedTool[];
  tools_cached_at: string | null;
  // null: every cached tool is exposed; a list restricts the server to it.
  enabled_tools: string[] | null;
  has_headers: boolean;
  created_at: string;
  updated_at: string;
};
export type ToolCallMeta = { name: string; arguments: Record<string, unknown>; result_preview: string; is_error: boolean };

export type Source = { id: string; filename: string; excerpt: string };
export type Attachment = { id: string; kind: "image" | "audio" | "video" | "file" | "location"; mime: string; filename: string | null; size_bytes: number };
/** One of the two leads of an "entity_merged" audit entry, as it was when they were merged. */
export type MergeSide = { number: number; name?: string | null; price?: number | null; currency?: string | null; created_at?: string | null; channels?: string[]; custom_fields?: Record<string, unknown> };
/** One channel thread of a lead that absorbed others: the primary's own or a linked one. */
export type LinkedThread = { conversation_id: string; channel: string; label: string | null; account_label: string | null; is_primary: boolean; mode: "ai" | "human"; last_inbound_at: string | null };
export type Message = { id: string; role: "user" | "assistant" | "system"; kind?: "message" | "activity" | "note"; delivery_status?: "pending" | "sent" | "delivered" | "read" | "failed" | "unknown" | null; delivery_error?: string | null; activity?: { event: string; hours?: number | string; assignee?: string; from?: string; team?: string; target?: string; reason?: string; tag?: string; primary_number?: number; secondary_number?: number; primary?: MergeSide; secondary?: MergeSide; title?: string; date?: string; [key: string]: unknown } | null; conversation_id?: string; channel?: string; content: string; sources: Source[]; tool_calls?: ToolCallMeta[] | null; sender_type: "visitor" | "ai" | "human"; sender_name: string | null; reaction?: string | null; incoming_reaction?: string | null; quoted_message_id?: string | null; created_at: string; attachments?: Attachment[] };

export type ConversationInbox = {
  id: string;
  agent_id: string;
  agent_name: string;
  client_id: string;
  title: string;
  contact_name: string | null;
  channel: string;
  account_label?: string | null;
  mode: "ai" | "human";
  preview: string;
  unread: boolean;
  unread_count: number;
  updated_at: string;
  last_inbound_at?: string | null;
  /** Distinct channels of the lead, the primary's first; absent on older responses. */
  channels?: string[];
  /** How many other leads were merged into this one. */
  linked_count?: number;
};
export type PortalMember = { id: string; name: string; email: string; availability: "online" | "away" };
export type TeamMember = { id: string; name: string; email: string; availability: "online" | "away" };
export type Team = {
  id: string;
  name: string;
  description: string;
  strategy: "round_robin" | "least_busy";
  channels: string[];
  is_default: boolean;
  members: TeamMember[];
  open_count: number;
  unassigned_count: number;
};

export type PipelineStage = {
  id: string;
  name: string;
  color: string;
  position: number;
  conversation_count: number;
  deal_value_total: number;
};

export type PipelineCard = {
  id: string;
  /** Short number of the lead in its client, when the board sends it. */
  number?: number;
  title: string;
  contact_name: string | null;
  contact_id: string | null;
  tags: { name: string; color: string }[];
  channel: string;
  account_label: string | null;
  mode: "ai" | "human";
  status: "open" | "resolved";
  pipeline_stage_id: string | null;
  deal_value: number | null;
  preview: string;
  updated_at: string;
};

export type PipelineBoard = {
  /** ISO 4217 code deal values are written in; USD when absent. */
  currency?: string;
  stages: PipelineStage[];
  unassigned_count: number;
  cards: PipelineCard[];
};
export type Conversation = {
  id: string;
  /** Short number of the lead inside its client (#1, #2 …); it is what the URL carries. */
  number: number;
  client_id: string;
  agent_id: string;
  title: string;
  mode: "ai" | "human";
  status?: "open" | "resolved";
  resolved_at?: string | null;
  archived_at?: string | null;
  first_reply_at?: string | null;
  taken_over_at?: string | null;
  phone_pause_until?: string | null;
  waiting_since?: string | null;
  assignee_id?: string | null;
  assignee_name?: string | null;
  team_id?: string | null;
  team_name?: string | null;
  pipeline_stage_id?: string | null;
  pipeline_stage_name?: string | null;
  pipeline_stage_color?: string | null;
  deal_value?: number | null;
  reply_window_until?: string | null;
  reply_window_open?: boolean;
  human_reply_window_open?: boolean;
  human_reply_window_until?: string | null;
  reply_block_reason?: string | null;
  social_channel_id?: string | null;
  account_label?: string | null;
  channel_capabilities?: ChannelCapabilities;
  channel: string;
  external_chat_id: string | null;
  contact_name: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
  contact_id?: string | null;
  created_at: string;
  updated_at: string;
  last_inbound_at?: string | null;
  preview?: string;
  unread?: boolean;
  unread_count?: number;
  messages?: Message[];
  channels?: string[];
  linked_count?: number;
  /** The lead's channel threads, the primary first; absent means a single thread. */
  linked_threads?: LinkedThread[];
  /** The thread of the last inbound message (else the primary): where a reply goes by default. */
  reply_via_default?: string;
};

export type WhatsAppChannel = {
  id: string;
  client_id: string;
  agent_id: string;
  status: "disconnected" | "connecting" | "qr" | "connected" | "reconnecting" | "error";
  phone_number: string | null;
  display_name: string | null;
  label: string | null;
  qr_code: string | null;
  last_error: string | null;
  is_enabled: boolean;
  groups_enabled: boolean;
  calls_enabled: boolean;
  calls_message: string | null;
  has_session: boolean;
  last_connected_at: string | null;
  created_at: string;
  updated_at: string;
};

export type WidgetChannel = {
  id: string;
  client_id: string;
  agent_id: string;
  public_id: string;
  is_enabled: boolean;
  greeting: string;
  color: string;
  position: "right" | "left";
  created_at: string;
  updated_at: string;
};

export type WhatsAppCloudChannel = {
  id: string;
  client_id: string;
  agent_id: string;
  status: "disconnected" | "connected" | "error";
  phone_number: string | null;
  display_name: string | null;
  label: string | null;
  phone_number_id: string;
  waba_id: string | null;
  external_account_id: string;
  provider_profile_id: string | null;
  /** Present while the number is not linked: the hosted page to open. */
  connect_url: string | null;
  coexistence: boolean;
  coexistence_sync: {
    started_at?: string;
    offboarded_at?: string;
    contacts?: { status: string; request_id?: string; error?: string; last_received_at?: string };
    media?: { status: string; error?: string };
    history?: { status: string; progress?: number; errors?: number; request_id?: string; error?: string };
  };
  quality_rating: string | null;
  messaging_limit: string | null;
  has_access_token: boolean;
  has_app_secret: boolean;
  webhook_url: string;
  webhook_verify_token: string;
  last_error: string | null;
  is_enabled: boolean;
  last_connected_at: string | null;
  created_at: string;
  updated_at: string;
};

export type TemplateHeader = {
  format: "TEXT" | "IMAGE" | "VIDEO" | "DOCUMENT" | "LOCATION" | string;
  text: string;
  parameters: string[];
};

export type TemplateButton = {
  type: "QUICK_REPLY" | "URL" | "PHONE_NUMBER" | "COPY_CODE" | string;
  text: string;
  url: string;
  phone_number: string;
  example: string;
  /** Takes a value at send time: a URL suffix or the code to copy. */
  dynamic: boolean;
};

export type Template = {
  id: string | null;
  name: string;
  language: string;
  category: string;
  status: "APPROVED" | "PENDING" | "REJECTED" | string;
  parameter_format: "NAMED" | "POSITIONAL" | string;
  header: TemplateHeader | null;
  body: string;
  footer: string;
  buttons: TemplateButton[];
  /** Body variables in order; `variables` is their count. */
  parameters: string[];
  variables: number;
  rejected_reason: string | null;
};

export type TemplateSend = {
  name: string;
  language: string;
  /** Body values in the order of the template's parameters. */
  variables: string[];
  /** The header's variable, or the https link of its media. */
  header_value?: string;
  location?: { latitude: number; longitude: number; name?: string; address?: string } | null;
  /** One slot per button; only the dynamic ones are read. */
  button_values?: string[];
};

export type PortalReport = {
  started: number;
  resolved: number;
  open_now: number;
  inbound_messages: number;
  human_replies: number;
  ai_replies: number;
  active_contacts: number;
  agents_online: number;
  handoffs: number;
  ai_resolved: number;
  avg_first_reply_seconds: number | null;
  avg_resolution_seconds: number | null;
  by_day: { date: string; started: number; resolved: number; inbound: number; ai_replies: number; human_replies: number }[];
  by_channel: { channel: string; started: number }[];
  by_agent: { name: string; availability: string; replies: number; assigned: number; open_now: number }[];
};

export type CannedResponse = {
  id: string;
  shortcut: string;
  content: string;
  updated_at: string;
};

export type PortalChannel = {
  channel: "whatsapp" | "whatsapp_cloud" | SocialProvider;
  id?: string;
  label?: string | null;
  provider?: SocialProvider;
  external_account_id?: string | null;
  username?: string | null;
  capabilities?: ChannelCapabilities;
  status: string;
  phone_number: string | null;
  display_name: string | null;
  supports_templates: boolean;
};

export type ApiToken = {
  id: string;
  kind: string;
  token_prefix: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  request_count: number;
  created_at: string;
};

export type ApiIntegration = {
  id: string;
  name: string;
  client_id: string | null;
  client_name: string | null;
  scopes: string[];
  oauth_client_id: string | null;
  redirect_uris: string[];
  last_used_at: string | null;
  created_at: string;
  tokens: ApiToken[];
};

export type ApiTokenIssued = { token: string; token_prefix: string; expires_at: string | null };

export type WebhookSubscription = {
  id: string;
  integration_id: string;
  url: string;
  events: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type WebhookSecret = { subscription_id: string; secret: string };

export type WebhookDelivery = {
  id: string;
  subscription_id: string;
  event: string;
  status: string;
  attempts: number;
  available_at: string | null;
  last_error: string | null;
  response_code: number | null;
  created_at: string;
  sent_at: string | null;
};

export type ApiScopes = {
  scopes: { key: string; description: string }[];
  presets: Record<string, string[]>;
};

export type OAuthClientInfo = {
  client_id: string;
  name: string;
  scopes: string[];
  scope_descriptions: Record<string, string>;
};

export type ContactTag = {
  id: string;
  name: string;
  color: string;
  contact_count: number;
  route_team_id?: string | null;
  route_team_name?: string | null;
  route_assignee_id?: string | null;
  route_assignee_name?: string | null;
};

export type Contact = {
  id: string;
  name: string;
  phone: string | null;
  email: string | null;
  notes: string;
  tags?: ContactTag[];
  created_at: string;
  updated_at: string;
  conversation_count: number;
  open_count: number;
  last_activity_at: string | null;
  blocked_at?: string | null;
};

export type ContactImportError = {
  row: number;
  name: string;
  phone: string;
  reason: string;
};

export type ContactImportResult = {
  created: number;
  updated: number;
  unchanged: number;
  errors: ContactImportError[];
  truncated: number;
};

export type PortalPublic = {
  client_name: string;
  portal_title: string;
  portal_slug: string;
  agency_name: string;
  agency_brand_color: string;
  agency_logo_url: string | null;
  client_logo_url: string | null;
};

export type SocialProvider = "instagram" | "messenger";
export type ChannelCapabilities = {
  text?: boolean;
  image?: boolean;
  video?: boolean;
  file?: boolean;
  audio?: boolean;
  reactions?: boolean;
  quotes?: boolean;
  templates?: boolean;
};
export type SocialConfig = Record<SocialProvider, {
  oauth_ready: boolean;
  manual_available: boolean;
  source: "operator" | "managed" | "provider";
  webhook_url: string;
}>;
export type SocialChannel = {
  id: string;
  client_id: string;
  agent_id: string;
  provider: SocialProvider;
  external_account_id: string | null;
  display_name: string | null;
  username: string | null;
  label: string | null;
  status: string;
  is_enabled: boolean;
  has_access_token: boolean;
  has_app_secret: boolean;
  webhook_url: string;
  webhook_verify_token: string | null;
  /** Present while no account is linked: the hosted page to open. */
  connect_url?: string | null;
  token_expires_at: string | null;
  last_error: string | null;
  human_agent_enabled: boolean;
  connection_source: "manual" | "oauth" | "managed";
  app_id?: string | null;
  granted_scopes: string[];
  created_at: string;
  updated_at: string;
};

export type SocialHistoryJob = {
  id: string;
  status: "pending" | "processing" | "completed" | "failed";
  conversations_count: number;
  messages_count: number;
  max_conversations: number;
  last_error: string | null;
  limited: boolean;
  created_at: string;
  updated_at: string;
};

export type ReportGroup = { id: string | null; name: string; replies: number; input_tokens: number; output_tokens: number; cost_usd: number };

export type OpsMetrics = {
  conversations: number; contacts: number; new_contacts: number;
  ai_resolved: number; ai_resolved_pct: number; handoffs: number; open: number; unanswered: number;
  first_reply_s: number | null; resolution_s: number | null; human_wait_s: number | null;
  inbound: number; ai_replies: number; human_replies: number; delivery_failures: number; tool_errors: number;
};
export type OpsGroup = OpsMetrics & { id: string | null; name: string };
export type OpsPeriod = { day: string; conversations: number; handoffs: number; inbound: number; outbound: number };
export type Operations = { tz: string; bucket: string; totals: OpsMetrics; by_client: OpsGroup[]; by_channel: OpsGroup[]; by_period: OpsPeriod[] };
export type ReportFilters = { clients: { id: string; name: string }[]; agents: { id: string; name: string; client_id: string }[]; channels: string[]; models: string[] };

export type CostReport = {
  totals: { cost_usd: number; replies: number; conversations: number; input_tokens: number; output_tokens: number; avg_cost_per_reply_usd: number };
  by_client: ReportGroup[];
  by_agent: ReportGroup[];
  by_model: ReportGroup[];
  by_day: { date: string; replies: number; cost_usd: number }[];
  tz: string;
};

export type ReportReply = {
  id: string;
  created_at: string;
  conversation_id: string | null;
  contact_name: string | null;
  client_id: string | null;
  client_name: string | null;
  agent_id: string | null;
  agent_name: string | null;
  channel: string | null;
  model: string;
  served_by: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
  estimated: boolean;
  duration_ms: number | null;
  tools: number;
  tool_errors: number;
};

export type CalendarMemberStatus = "pending" | "connected" | "error";

export type CalendarMember = {
  id: string;
  name: string;
  role: string;
  color: string;
  status: CalendarMemberStatus;
  google_email: string | null;
  connected_at: string | null;
  last_error: string | null;
  connect_url: string;
  connect_expires_at: string;
  link_expired: boolean;
};

export type CalendarOverview = {
  oauth_ready: boolean;
  timezone: string;
  members: CalendarMember[];
};

export type CalendarEvent = {
  id: string;
  member_id: string;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  location: string;
  url: string;
};

export type CalendarEventsResult = {
  events: CalendarEvent[];
  errors: { member_id: string; detail: string }[];
};

export type CalendarConnectInfo = {
  member_name: string;
  member_role: string;
  color: string;
  client_name: string;
  has_logo: boolean;
  status: CalendarMemberStatus;
  google_email: string | null;
  oauth_ready: boolean;
  expired: boolean;
  agency_name: string;
};

/** The client's own business details, as its portal reads and edits them (GET/PATCH /portal/{slug}/client). */
export type PortalClientDetails = {
  name: string;
  industry: string;
  business_type: string;
  business_custom: string;
  timezone: string;
  logo_url: string | null;
  owner_name: string | null;
  currency: string;
  address: string | null;
  google_maps_url: string | null;
  business_hours: WeeklyHours | null;
};

export type ServiceModality = "presencial" | "online" | "a_domicilio";

/** A service or product a business offers. */
export type Service = {
  id: string;
  client_id: string;
  name: string;
  description: string;
  price: number;
  currency: string;
  duration_minutes: number;
  modality: ServiceModality;
  requires_deposit: boolean;
  deposit_amount: number | null;
  requirements: string;
  is_active: boolean;
  position: number;
  created_at: string;
  updated_at: string;
};

export type WeekDay = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
/** One working range, "HH:MM" to "HH:MM" in the client's timezone. */
export type TimeRange = [string, string];
/** Every day of the week is always present; an empty list is a day off. */
export type WeeklyHours = Record<WeekDay, TimeRange[]>;

/** A member of a client's staff (e.g. one dentist of a clinic) with their own weekly hours. */
export type Professional = {
  id: string;
  client_id: string;
  name: string;
  role: string | null;
  color: string;
  is_active: boolean;
  slot_minutes: number;
  weekly_hours: WeeklyHours;
  service_ids?: string[];
  created_at: string;
  updated_at: string;
};

/** The custom fields a client defines for its leads (the "Configure" screen of the lead card). */
export type LeadFieldType = "text" | "number" | "date" | "select" | "checkbox";
export type LeadField = {
  id: string;
  /** Stable key the value is stored under; set once when the field is created. */
  key: string;
  label: string;
  type: LeadFieldType;
  options: string[];
  position: number;
};
export type LeadStage = { id: string; name: string; color: string };
export type LeadValue = string | number | boolean | null;
export type LeadContact = {
  id: string | null;
  name: string | null;
  whatsapp_name: string | null;
  phone: string | null;
  email: string | null;
  company: string | null;
  blocked: boolean;
  tags: { id: string; name: string; color: string }[];
};
/** Everything the lead side panel shows and edits about one conversation. */
export type LeadCard = {
  conversation_id: string;
  number: number;
  channel: string;
  account_label: string | null;
  stage: LeadStage | null;
  deal_value: number | null;
  currency: string;
  responsible: { id: string | null; name: string | null; is_default: boolean };
  owner_name: string | null;
  custom_values: Record<string, LeadValue>;
  fields: LeadField[];
  contact: LeadContact;
  created_at?: string;
  linked_channels?: { conversation_id: string; channel: string; label: string | null; account_label: string | null; is_primary: boolean }[];
};
/** One candidate of the merge dialog's search. */
export type MergeCandidate = { conversation_id: string; number: number; contact_name: string | null; phone: string | null; email: string | null; channel: string; channels: string[]; stage: LeadStage | null; deal_value: number | null; created_at: string };
export type LeadMergeResult = { primary: LeadCard; secondary_number: number };

export type AppointmentStatus = "confirmed" | "cancelled" | "completed" | "no_show";

export type Appointment = {
  id: string;
  agency_id: string;
  client_id: string;
  conversation_id: string | null;
  contact_id: string | null;
  professional_id: string | null;
  service_id: string | null;
  title: string;
  start_time: string;
  end_time: string;
  duration_minutes: number;
  status: AppointmentStatus;
  notes: string | null;
  created_by_role: string | null;
  created_at: string;
  updated_at: string;
  professional_name?: string | null;
  service_name?: string | null;
  contact_name?: string | null;
};

export type AvailabilitySlot = {
  start_time: string;
  end_time: string;
  professional_id?: string | null;
  professional_name?: string | null;
};

export type AvailabilityDay = {
  date: string;
  slots: AvailabilitySlot[];
};

export type AvailabilityResponse = {
  days: AvailabilityDay[];
};

export type ScheduledMessageStatus = "pending" | "sent" | "cancelled" | "failed";

export type ScheduledMessage = {
  id: string;
  agency_id: string;
  client_id: string;
  conversation_id: string;
  via_conversation_id: string | null;
  portal_user_id: string | null;
  sender_type: string;
  sender_name: string | null;
  content: string;
  scheduled_for: string;
  status: ScheduledMessageStatus;
  failure_reason: string | null;
  sent_at: string | null;
  created_at: string;
  updated_at: string;
};

