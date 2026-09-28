"use client";

import { useState } from "react";
import { api, messageFrom } from "@/lib/api";
import { useT, type I18nKey } from "@/lib/i18n";
import { FEATURES_WITHOUT_SCREEN, normalizeFeatures, type PortalFeature } from "@/lib/portal-features";
import { useToast } from "@/components/toast";
import type { Client } from "@/types";

const LABELS: Record<PortalFeature, I18nKey> = {
  inbox: "clients.detail.pfInbox",
  contacts: "clients.detail.pfContacts",
  pipeline: "clients.detail.pfPipeline",
  calendar: "clients.detail.pfCalendar",
  reports: "clients.detail.pfReports",
  teams: "clients.detail.pfTeams",
  tags: "clients.detail.pfTags",
  templates: "clients.detail.pfTemplates",
  canned: "clients.detail.pfCanned",
  agents: "clients.detail.pfAgents",
  api: "clients.detail.pfApi",
  "channels.whatsapp": "clients.detail.pfChannelWhatsapp",
  "channels.whatsapp_cloud": "clients.detail.pfChannelWhatsappCloud",
  "channels.instagram": "clients.detail.pfChannelInstagram",
  "channels.messenger": "clients.detail.pfChannelMessenger",
  "channels.webchat": "clients.detail.pfChannelWebchat",
  details: "clients.detail.pfDetails",
  professionals: "clients.detail.pfProfessionals",
  services: "clients.detail.pfServices",
  resources: "clients.detail.pfResources",
  appointments: "clients.detail.pfAppointments",
};

import { Eye, EyeOff, Globe2 } from "lucide-react";

/** One on/off switch per function of a client's portal. Turning one on makes that
 * function appear in the client's portal; off makes it disappear. Each change is
 * saved on its own (PATCH /clients/{id}/portal merges the keys it is given). */
export function PortalFeatureToggle({ client, keys, onChange, title, extra }: { client: Client; keys: readonly PortalFeature[]; onChange: (client: Client) => void; title?: string; extra?: React.ReactNode }) {
  const t = useT();
  const toast = useToast();
  const [busy, setBusy] = useState<PortalFeature | null>(null);
  const features = normalizeFeatures(client.portal_features);

  async function toggle(key: PortalFeature, enabled: boolean) {
    setBusy(key);
    try {
      onChange(await api<Client>(`/clients/${client.id}/portal`, { method: "PATCH", body: JSON.stringify({ portal_features: { [key]: enabled } }) }));
      toast.success(t("clients.detail.pfSaved"));
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusy(null);
    }
  }

  if (keys.length === 1) {
    const key = keys[0];
    const isChecked = Boolean(features[key]);
    return (
      <section className="stitch-feature-banner" aria-label={title ?? t("clients.detail.pfTitle")}>
        <div style={{ display: "flex", alignItems: "center", gap: "14px", minWidth: 0 }}>
          <div className="stitch-icon-badge">
            <Globe2 size={20} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "3px", minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
              <strong style={{ fontSize: "14.5px", fontWeight: 700, color: "var(--ink)", letterSpacing: "-0.2px" }}>
                {title ?? `${t("clients.detail.pfTitle")}: ${t(LABELS[key])}`}
              </strong>
              {isChecked && (
                <span className="stitch-live-pill">
                  <span className="stitch-pulse-dot" />
                  {t("clients.detail.channelConnected")}
                </span>
              )}
            </div>
            <p style={{ margin: 0, fontSize: "12px", color: "var(--muted)" }}>
              {t("clients.detail.pfHint")}
            </p>
          </div>
        </div>

        {extra && (
          <div className="stitch-feature-banner-extra">
            {extra}
          </div>
        )}

        <div className="stitch-visibility-row" style={{ minWidth: "190px" }}>
          <div className={`stitch-visibility-left ${isChecked ? "" : "muted"}`}>
            {isChecked ? <Eye size={15} /> : <EyeOff size={15} />}
            <span>{t(LABELS[key])}</span>
          </div>
          <label className="stitch-switch">
            <input
              type="checkbox"
              checked={isChecked}
              disabled={busy === key}
              onChange={(event) => void toggle(key, event.target.checked)}
            />
            <span className="stitch-slider" />
          </label>
        </div>
      </section>
    );
  }

  return (
    <section className="stitch-feature-multi" aria-label={title ?? t("clients.detail.pfTitle")}>
      <div style={{ display: "flex", alignItems: "center", gap: "14px", minWidth: 0 }}>
        <div className="stitch-icon-badge">
          <Globe2 size={20} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "3px", minWidth: 0 }}>
          <strong style={{ fontSize: "16px", fontWeight: 700, color: "var(--ink)", letterSpacing: "-0.2px" }}>
            {title ?? t("clients.detail.pfTitle")}
          </strong>
          <small style={{ fontSize: "12.5px", color: "var(--muted)" }}>
            {t("clients.detail.pfHint")}
          </small>
        </div>
      </div>

      <div className="stitch-feature-grid">
        {keys.map((key) => {
          const isChecked = Boolean(features[key]);
          return (
            <div key={key} className="stitch-feature-tile">
              <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0 }}>
                {isChecked ? <Eye size={16} style={{ color: "#00876c", flexShrink: 0 }} /> : <EyeOff size={16} style={{ color: "var(--muted)", flexShrink: 0 }} />}
                <div style={{ minWidth: 0 }}>
                  <span style={{ fontSize: "12.5px", fontWeight: 600, color: "var(--ink)", display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {t(LABELS[key])}
                  </span>
                  {FEATURES_WITHOUT_SCREEN.includes(key) && (
                    <span style={{ fontSize: "10px", color: "var(--muted)", display: "block" }}>{t("clients.detail.pfSoon")}</span>
                  )}
                </div>
              </div>
              <label className="stitch-switch">
                <input
                  type="checkbox"
                  checked={isChecked}
                  disabled={busy === key}
                  onChange={(event) => void toggle(key, event.target.checked)}
                />
                <span className="stitch-slider" />
              </label>
            </div>
          );
        })}
      </div>
    </section>
  );
}
