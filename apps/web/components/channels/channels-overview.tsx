"use client";

// One card per channel type of a client, with its state. The agency's Channels
// tab and the portal's Channels screen render this same grid; the portal passes
// only the types the agency switched on, and nothing else is fetched or shown.

import Link from "next/link";
import { useEffect, useState } from "react";
import { Globe2, MessageCircle, QrCode } from "lucide-react";
import { ApiError } from "@/lib/api";
import { accountName, ChannelIcon } from "@/lib/channels";
import { useT } from "@/lib/i18n";
import { CHANNEL_TYPES, type ChannelType } from "@/lib/routes";
import { ChannelsScopeProvider, useChannelsApi, useChannelsScope, type ChannelHrefs } from "@/components/channels/scope";
import type { SocialChannel, WhatsAppChannel, WhatsAppCloudChannel, WidgetChannel } from "@/types";

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

/** A small dot beside the channel's name: green connected, amber connecting,
 * red disconnected, grey never set up. The words live in the tooltip. */
function ChannelStateBadge({ state }: { state: ChannelState }) {
  const t = useT();
  if (state === "loading") return null;
  const label = state === "connected" ? t("clients.detail.channelConnected") : state === "pending" ? t("clients.detail.channelPending") : state === "disconnected" ? t("clients.detail.channelDisconnected") : t("clients.detail.channelNotConnected");
  return <i className={`channel-state-dot ${state}`} title={label} aria-label={label} role="img" />;
}

/** `types` limits the grid to those channel types (the portal's enabled ones); the agency shows all five. */
export function ChannelsOverviewView({ apiBase, hrefFor, client, types }: { apiBase?: string; hrefFor?: ChannelHrefs; client?: { id: string; name: string } | null; types?: readonly ChannelType[] }) {
  return <ChannelsScopeProvider apiBase={apiBase} hrefFor={hrefFor} client={client}><ChannelsOverview types={types ?? CHANNEL_TYPES} /></ChannelsScopeProvider>;
}

function ChannelsOverview({ types }: { types: readonly ChannelType[] }) {
  const t = useT();
  const { hrefFor, client } = useChannelsScope();
  const { api } = useChannelsApi();
  const id = client.id;
  const has = (type: ChannelType) => types.includes(type);
  // One card per channel type. A client may have several accounts on a type:
  // the card is connected when any of them is, and its detail names them.
  const [states, setStates] = useState<Partial<Record<ChannelType, ChannelStatus>> | null>(null);
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
  const stateOf = (type: ChannelType): ChannelState => states?.[type]?.state ?? "loading";
  const detailOf = (type: ChannelType): string => states?.[type]?.detail ?? "";
  const live = (type: ChannelType) => stateOf(type) === "connected" ? "channel-live" : "";
  return <section className="compact-channel-grid">
    {has("whatsapp-cloud") && <article className={live("whatsapp-cloud")}><span className="whatsapp"><MessageCircle size={20} /></span><div><strong>{t("channels.whatsappCloud.title")} <ChannelStateBadge state={stateOf("whatsapp-cloud")} /></strong><small>{detailOf("whatsapp-cloud") || t("clients.detail.channelWhatsappAvailable", { name: client.name })}</small></div><Link className="button secondary" href={hrefFor.type("whatsapp-cloud")}>{t("clients.detail.configure")}</Link></article>}
    {has("whatsapp") && <article className={live("whatsapp")}><span className="whatsapp"><QrCode size={20} /></span><div><strong>{t("channels.whatsapp.title")} <ChannelStateBadge state={stateOf("whatsapp")} /></strong><small>{detailOf("whatsapp") || t("clients.detail.channelWhatsappQrAvailable")}</small></div><Link className="button secondary" href={hrefFor.type("whatsapp")}>{t("clients.detail.configure")}</Link></article>}
    {has("webchat") && <article className={live("webchat")}><span className="webchat"><Globe2 size={20} /></span><div><strong>{t("channels.webchat.title")} <ChannelStateBadge state={stateOf("webchat")} /></strong><small>{t("clients.detail.channelWebchatAvailable")}</small></div><Link className="button secondary" href={hrefFor.type("webchat")}>{t("clients.detail.configure")}</Link></article>}
    {(["instagram", "messenger"] as const).filter(has).map((provider) => <article key={provider} className={live(provider)}><span className={provider === "instagram" ? "instagram" : "facebook"}><ChannelIcon channel={provider} size={20} /></span><div><strong>{t(`social.${provider}.title`)} <ChannelStateBadge state={stateOf(provider)} /></strong><small>{detailOf(provider) || t(`social.${provider}.description`)}</small></div><Link className="button secondary" href={hrefFor.type(provider)}>{t("social.configure")}</Link></article>)}
  </section>;
}
