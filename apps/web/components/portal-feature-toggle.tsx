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
};

/** One on/off switch per function of a client's portal. Turning one on makes that
 * function appear in the client's portal; off makes it disappear. Each change is
 * saved on its own (PATCH /clients/{id}/portal merges the keys it is given). */
export function PortalFeatureToggle({ client, keys, onChange, title }: { client: Client; keys: readonly PortalFeature[]; onChange: (client: Client) => void; title?: string }) {
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

  return (
    <section className="portal-feature-bar" aria-label={title ?? t("clients.detail.pfTitle")}>
      <div className="portal-feature-copy">
        <strong>{title ?? t("clients.detail.pfTitle")}</strong>
        <small>{t("clients.detail.pfHint")}</small>
      </div>
      <div className="portal-feature-list">
        {keys.map((key) => (
          <label className="switch-row" key={key}>
            <span>
              <strong>{t(LABELS[key])}</strong>
              {FEATURES_WITHOUT_SCREEN.includes(key) && <small>{t("clients.detail.pfSoon")}</small>}
            </span>
            <input type="checkbox" checked={features[key]} disabled={busy === key} onChange={(event) => void toggle(key, event.target.checked)} />
          </label>
        ))}
      </div>
    </section>
  );
}
