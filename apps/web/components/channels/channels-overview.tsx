"use client";

// One card per channel type of a client, with its state. The agency's Channels
// tab and the portal's Channels screen render this same grid; the portal passes
// only the types the agency switched on, and nothing else is fetched or shown.

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { ExternalLink, Eye, EyeOff, Globe2, MessageCircle, QrCode, Radio, Send } from "lucide-react";
import { moduleAllowed, moduleForChannel } from "@/lib/agency-modules";
import { ApiError, messageFrom } from "@/lib/api";
import { accountName, ChannelIcon } from "@/lib/channels";
import { useT } from "@/lib/i18n";
import { CHANNEL_TYPES, type ChannelType } from "@/lib/routes";
import { ChannelsScopeProvider, useChannelsApi, useChannelsScope, type ChannelHrefs } from "@/components/channels/scope";
import { CHANNEL_TYPE_OF_FEATURE, FEATURE_OF_CHANNEL_TYPE, normalizeFeatures } from "@/lib/portal-features";
import { QuotaStepper } from "@/components/quota-stepper";
import { useToast } from "@/components/toast";
import type { ChannelAllowance, Client, SocialChannel, WhatsAppChannel, WhatsAppCloudChannel, WidgetChannel } from "@/types";

type ChannelState = "loading" | "off" | "pending" | "connected" | "disconnected";
type ChannelStatus = { state: ChannelState; detail?: string };

function socialState(channel: SocialChannel | null): ChannelStatus {
  if (!channel) return { state: "off" };
  return { state: channel.is_enabled && channel.status === "connected" ? "connected" : "disconnected", detail: channel.username ? `@${channel.username}` : channel.display_name || "" };
}

/** The card state for a kind with several accounts: connected when any is,
 * pending when any is; the detail is the single account's, or their names. */
function manyState<T>(items: T[], stateOf: (item: T) => ChannelState, detailOf: (item: T) => string, nameOf: (item: T, n: number) => string, summary: string): ChannelStatus {
  if (!items.length) return { state: "off" };
  const states = items.map(stateOf);
  const state: ChannelState = states.includes("connected") ? "connected" : states.includes("pending") ? "pending" : "disconnected";
  if (items.length === 1) return { state, detail: detailOf(items[0]) };
  const names = items.map((item, index) => nameOf(item, index + 1));
  return { state, detail: items.length <= 3 ? names.join(" · ") : summary };
}

/** `types` limits the grid to those channel types (the portal's enabled ones); the agency shows all five. */
export function ChannelsOverviewView({
  apiBase,
  hrefFor,
  client,
  types,
  clientData,
  onClientChange,
}: {
  apiBase?: string;
  hrefFor?: ChannelHrefs;
  client?: { id: string; slug: string; name: string } | null;
  types?: readonly ChannelType[];
  clientData?: Client | null;
  onClientChange?: (client: Client) => void;
}) {
  return (
    <ChannelsScopeProvider apiBase={apiBase} hrefFor={hrefFor} client={client}>
      <ChannelsOverview types={types ?? CHANNEL_TYPES} clientData={clientData} onClientChange={onClientChange} />
    </ChannelsScopeProvider>
  );
}

function ChannelsOverview({
  types,
  clientData,
  onClientChange,
}: {
  types: readonly ChannelType[];
  clientData?: Client | null;
  onClientChange?: (client: Client) => void;
}) {
  const t = useT();
  const toast = useToast();
  const { hrefFor, client, modules } = useChannelsScope();
  const { api } = useChannelsApi();
  const id = client.id;
  const has = (type: ChannelType) => types.includes(type);
  const [states, setStates] = useState<Partial<Record<ChannelType, ChannelStatus>> | null>(null);
  const [busyFeature, setBusyFeature] = useState<string | null>(null);
  const [allowances, setAllowances] = useState<ChannelAllowance[] | null>(null);
  const [busyAllowance, setBusyAllowance] = useState<string | null>(null);
  // How many lines of each type this client may use: the agency's business, so
  // only its own view asks (the portal passes no clientData).
  const managesLines = Boolean(clientData && onClientChange);
  // The platform switched this channel off and the client has nothing connected:
  // there is nothing to look at and nothing to manage, so the card goes. With
  // numbers already connected the card stays, so they can still be seen and
  // disconnected - just not added to.
  const switchedOffWithNothing = (type: ChannelType) =>
    !moduleAllowed(modules, moduleForChannel(type)) && states?.[type]?.state === "off";
  const typesKey = types.join(",");

  useEffect(() => {
    const wanted = new Set(typesKey ? typesKey.split(",") : []);
    const missing = (err: unknown) => { if (err instanceof ApiError && err.status === 404) return null; throw err; };
    const none = <T,>(): Promise<T[]> => Promise.resolve([]);
    Promise.all([
      wanted.has("whatsapp-cloud") ? api<WhatsAppCloudChannel[]>(`/whatsapp-cloud/clients/${id}/channels`) : none<WhatsAppCloudChannel>(),
      wanted.has("whatsapp") ? api<WhatsAppChannel[]>(`/whatsapp/clients/${id}/channels`) : none<WhatsAppChannel>(),
      wanted.has("webchat") ? api<WidgetChannel>(`/webchat/channels/${id}`).catch(missing) : Promise.resolve(null),
      wanted.has("instagram") ? api<SocialChannel[]>(`/social/instagram/clients/${id}/channels`) : none<SocialChannel>(),
      wanted.has("messenger") ? api<SocialChannel[]>(`/social/messenger/clients/${id}/channels`) : none<SocialChannel>(),
    ]).then(([cloud, qr, widget, instagram, messenger]) => setStates({
      "whatsapp-cloud": manyState(cloud, (item) => item.status === "connected" ? "connected" : "disconnected", (item) => item.phone_number || item.display_name || "", (item, n) => accountName(item, t("clients.whatsappCloud.numberFallback", { n })), t("clients.detail.channelNumbers", { count: cloud.length, connected: cloud.filter((item) => item.status === "connected").length })),
      whatsapp: manyState(qr, (item) => item.status === "connected" ? "connected" : item.status === "qr" || item.status === "connecting" || item.status === "reconnecting" ? "pending" : "disconnected", (item) => item.phone_number || "", (item, n) => accountName(item, t("clients.whatsapp.lineFallback", { n })), t("clients.detail.channelNumbers", { count: qr.length, connected: qr.filter((item) => item.status === "connected").length })),
      webchat: widget ? { state: widget.is_enabled ? "connected" : "disconnected" } : { state: "off" },
      instagram: manyState(instagram, (item) => socialState(item).state, (item) => socialState(item).detail || "", (item, n) => accountName(item, t("social.accountFallback", { n })), t("clients.detail.channelAccounts", { count: instagram.length, connected: instagram.filter((item) => socialState(item).state === "connected").length })),
      messenger: manyState(messenger, (item) => socialState(item).state, (item) => socialState(item).detail || "", (item, n) => accountName(item, t("social.accountFallback", { n })), t("clients.detail.channelAccounts", { count: messenger.length, connected: messenger.filter((item) => socialState(item).state === "connected").length })),
    })).catch(() => {});
  }, [id, api, typesKey, t]);

  useEffect(() => {
    if (!managesLines) return;
    let active = true;
    api<ChannelAllowance[]>(`/clients/${id}/channel-allowances`)
      .then((rows) => { if (active) setAllowances(rows); })
      .catch(() => { if (active) setAllowances([]); });
    return () => { active = false; };
  }, [api, id, managesLines]);

  /** One number changes; the API answers with the whole list, so the screen
   * always shows what the server stored rather than what was clicked. */
  async function setAllocation(key: string, value: number | null) {
    if (!clientData) return;
    setBusyAllowance(key);
    try {
      setAllowances(await api<ChannelAllowance[]>(`/clients/${clientData.id}/channel-allowances`, {
        method: "PUT",
        body: JSON.stringify({ allocations: { [key]: value } }),
      }));
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusyAllowance(null);
    }
  }

  const stateOf = (type: ChannelType): ChannelState => states?.[type]?.state ?? "loading";
  const detailOf = (type: ChannelType): string => states?.[type]?.detail ?? "";

  const features = clientData ? normalizeFeatures(clientData.portal_features) : null;

  async function handleToggle(type: ChannelType, checked: boolean) {
    if (!clientData || !onClientChange) return;
    const featKey = FEATURE_OF_CHANNEL_TYPE[type];
    if (!featKey) return;
    setBusyFeature(featKey);
    try {
      const updated = await api<Client>(`/clients/${clientData.id}/portal`, {
        method: "PATCH",
        body: JSON.stringify({ portal_features: { [featKey]: checked } }),
      });
      onClientChange(updated);
      toast.success(checked ? t("channels.visibility.shown") : t("channels.visibility.hidden"));
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusyFeature(null);
    }
  }

  const activeCount = types.filter((type) => stateOf(type) === "connected").length;
  const unlinkedCount = types.filter((type) => stateOf(type) === "off" || stateOf(type) === "disconnected").length;

  type ChannelCardInfo = {
    type: ChannelType;
    title: string;
    icon: ReactNode;
    subtitle: string;
  };

  const channelCards: ChannelCardInfo[] = [
    {
      type: "whatsapp-cloud",
      title: t("channels.whatsappCloud.title"),
      icon: <MessageCircle size={22} />,
      subtitle: detailOf("whatsapp-cloud") || t("clients.detail.channelWhatsappAvailable", { name: client.name }),
    },
    {
      type: "whatsapp",
      title: t("channels.whatsapp.title"),
      icon: <QrCode size={22} />,
      subtitle: detailOf("whatsapp") || t("clients.detail.channelWhatsappQrAvailable"),
    },
    {
      type: "webchat",
      title: t("channels.webchat.title"),
      icon: <Globe2 size={22} />,
      subtitle: detailOf("webchat") || t("clients.detail.channelWebchatAvailable"),
    },
    {
      type: "instagram",
      title: t("social.instagram.title"),
      icon: <ChannelIcon channel="instagram" size={22} />,
      subtitle: detailOf("instagram") || t("social.instagram.description"),
    },
    {
      type: "messenger",
      title: t("social.messenger.title"),
      icon: <ChannelIcon channel="messenger" size={22} />,
      subtitle: detailOf("messenger") || t("social.messenger.description"),
    },
  ];

  return (
    <div className="stitch-channels-wrapper">
      {/* Stitch Banner & Stats Row */}
      {clientData && (
        <div className="stitch-banner">
          <div className="stitch-banner-left">
            <div className="stitch-icon-badge">
              <Radio size={20} />
            </div>
            <div className="stitch-banner-content">
              <div className="stitch-banner-title-row">
                <h2 className="stitch-banner-title">{t("channels.banner.title")}</h2>
                <span className="stitch-live-pill">
                  <span className="stitch-pulse-dot" />
                  {t("channels.banner.live")}
                </span>
              </div>
              <p className="stitch-banner-desc">
                {t("channels.banner.description", { name: client.name })}
              </p>
            </div>
          </div>
          <div className="stitch-banner-stats">
            <div className="stitch-stat-pill">
              <span className="stitch-stat-dot gray" />
              <span className="stitch-stat-num">{unlinkedCount}</span>
              <span className="stitch-stat-label">{t("channels.banner.unlinked")}</span>
            </div>
            <div className="stitch-stat-pill">
              <span className="stitch-stat-dot green" />
              <span className="stitch-stat-num">{activeCount}</span>
              <span className="stitch-stat-label">{t("channels.banner.active")}</span>
            </div>
          </div>
        </div>
      )}

      {/* The agency's own screen also decides how many lines this client may use. */}
      {managesLines && allowances && allowances.some((row) => moduleAllowed(modules, row.key)) && (
        <section className="stitch-channel-card" style={{ marginBottom: 16 }}>
          <div className="stitch-card-head">
            <div className="stitch-card-identity">
              <h3 className="stitch-card-title">{t("channels.quota.linesTitle")}</h3>
            </div>
          </div>
          <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
            {t("channels.quota.linesCopy")} {t("channels.quota.hint")}
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 16, marginTop: 12 }}>
            {allowances.filter((row) => moduleAllowed(modules, row.key)).map((row) => {
              const type = CHANNEL_TYPE_OF_FEATURE[row.key];
              const label = type ? channelCards.find((card) => card.type === type)?.title ?? row.label : row.label;
              const allowed = row.allowed;
              const over = allowed !== null && row.used > allowed;
              const line = row.agency_quota === 0
                ? t("channels.quota.notIncluded")
                : allowed === null
                  ? t("channels.quota.inUseFree", { used: row.used })
                  : over
                    ? t("channels.quota.overLimit", { used: row.used, quota: allowed })
                    : t("channels.quota.inUseOf", { used: row.used, quota: allowed });
              return (
                <div key={row.key} style={{ minWidth: 210, display: "flex", flexDirection: "column", gap: 6 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>{label}</span>
                  <QuotaStepper
                    value={row.allocation}
                    disabled={busyAllowance !== null}
                    onChange={(next) => void setAllocation(row.key, next)}
                  />
                  <small style={{ color: over ? "#b91c1c" : "var(--muted)" }}>{line}</small>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Bento Grid of Channels */}
      <section className="stitch-channels-grid">
        {channelCards.filter((card) => has(card.type) && !switchedOffWithNothing(card.type)).map((card) => {
          const state = stateOf(card.type);
          const isConnected = state === "connected";
          const featKey = FEATURE_OF_CHANNEL_TYPE[card.type];
          const isVisible = features ? Boolean(features[featKey]) : false;
          const isToggling = busyFeature === featKey;

          return (
            <div key={card.type} className="stitch-channel-card">
              <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                {/* Header */}
                <div className="stitch-card-head">
                  <div className="stitch-card-identity">
                    <div className="stitch-channel-icon-box">
                      {card.icon}
                    </div>
                    <h3 className="stitch-card-title">{card.title}</h3>
                  </div>
                  <span
                    className={`stitch-badge ${
                      isConnected
                        ? "connected"
                        : state === "pending"
                        ? "pending"
                        : "disconnected"
                    }`}
                  >
                    {isConnected
                      ? t("clients.detail.channelConnected")
                      : state === "pending"
                      ? t("clients.detail.channelPending")
                      : t("clients.detail.channelDisconnected")}
                  </span>
                </div>

                {/* Compact Portal Visibility Row (Shown in Agency Mode) */}
                {clientData && (
                  <div className="stitch-visibility-row">
                    <div className={`stitch-visibility-left ${isVisible ? "" : "muted"}`}>
                      {isVisible ? <Eye size={15} /> : <EyeOff size={15} />}
                      <span>Visible en portal</span>
                    </div>
                    <label className="stitch-switch">
                      <input
                        type="checkbox"
                        checked={isVisible}
                        disabled={isToggling}
                        onChange={(e) => void handleToggle(card.type, e.target.checked)}
                      />
                      <span className="stitch-slider" />
                    </label>
                  </div>
                )}
              </div>

              {/* Footer / Actions */}
              <div className="stitch-card-foot">
                <span className="stitch-card-detail">
                  {card.subtitle}
                </span>
                <Link className="stitch-config-btn" href={hrefFor.type(card.type)}>
                  <span>{t("clients.detail.configure")}</span>
                  <ExternalLink size={13} />
                </Link>
              </div>
            </div>
          );
        })}

        {/* Telegram Bot Tile (Completing the 3x2 Stitch layout) */}
        {clientData && (
          <div className="stitch-channel-card dashed">
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div className="stitch-card-head">
                <div className="stitch-card-identity">
                  <div className="stitch-channel-icon-box">
                    <Send size={18} />
                  </div>
                  <h3 className="stitch-card-title">Telegram Bot</h3>
                </div>
                <span className="stitch-badge disconnected">
                  Desconectado
                </span>
              </div>

              <div className="stitch-visibility-row disabled">
                <div className="stitch-visibility-left muted">
                  <EyeOff size={15} />
                  <span>Visible en portal</span>
                </div>
                <label className="stitch-switch">
                  <input type="checkbox" disabled />
                  <span className="stitch-slider" />
                </label>
              </div>
            </div>

            <div className="stitch-card-foot">
              <span className="stitch-card-detail">BotFather API</span>
              <button type="button" className="stitch-config-btn disabled">
                <span>{t("clients.detail.configure")}</span>
                <ExternalLink size={13} />
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
