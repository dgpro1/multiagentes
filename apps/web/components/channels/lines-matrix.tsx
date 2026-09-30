"use client";

// The agency's planning screen: every client against every channel type, with the
// plan at the top of each column and the totals at the bottom. One request fills
// it (GET /channel-quotas); each cell saves through the per-client endpoint,
// because that is the one that owns an allocation.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { moduleAllowed, useAgencyModules } from "@/lib/agency-modules";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { QuotaStepper } from "@/components/quota-stepper";
import { useToast } from "@/components/toast";
import { CHANNEL_TYPE_OF_FEATURE } from "@/lib/portal-features";
import { clientPath } from "@/lib/routes";
import type { ChannelQuotaMatrix } from "@/types";

export function LinesMatrix() {
  const t = useT();
  const toast = useToast();
  const [matrix, setMatrix] = useState<ChannelQuotaMatrix | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setMatrix(await api<ChannelQuotaMatrix>("/channel-quotas"));
    } catch {
      // Without line quotas there is nothing to plan; the channel cards below
      // are the whole screen.
      setMatrix(null);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  // Un tipo que la plataforma apago no se lista: no hay nada que repartir
  // entre los clientes de una linea que el plan no compra.
  const modules = useAgencyModules(true);
  const shown = (matrix?.types ?? []).filter((type) => moduleAllowed(modules, type.key));

  async function setAllocation(clientId: string, key: string, value: number | null) {
    setBusy(`${clientId}:${key}`);
    try {
      await api(`/clients/${clientId}/channel-allowances`, {
        method: "PUT",
        body: JSON.stringify({ allocations: { [key]: value } }),
      });
      await load();
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusy(null);
    }
  }

  /** The type's own name, from the same copy the channel cards use. */
  function titleOf(key: string): string {
    const type = CHANNEL_TYPE_OF_FEATURE[key];
    if (type === "whatsapp") return t("channels.whatsapp.title");
    if (type === "whatsapp-cloud") return t("channels.whatsappCloud.title");
    if (type === "webchat") return t("channels.webchat.title");
    if (type === "instagram") return t("social.instagram.title");
    if (type === "messenger") return t("social.messenger.title");
    return key;
  }

  if (!matrix) return null;
  if (matrix.clients.length === 0) {
    return (
      <section style={{ marginBottom: 20 }}>
        <div className="section-copy">
          <h2>{t("channels.quota.matrixTitle")}</h2>
          <p>{t("channels.quota.matrixEmpty")}</p>
        </div>
      </section>
    );
  }

  const head = { padding: "6px 10px", fontSize: 12, color: "var(--muted)", textAlign: "left" as const, whiteSpace: "nowrap" as const };
  const cell = { padding: "8px 10px", borderTop: "1px solid var(--line)", verticalAlign: "top" as const };

  return (
    <section style={{ marginBottom: 20 }}>
      <div className="section-copy" style={{ marginBottom: 10 }}>
        <h2>{t("channels.quota.matrixTitle")}</h2>
        <p>{t("channels.quota.matrixCopy")}</p>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 760 }}>
          <thead>
            <tr>
              <th style={head}>{t("channels.toolbar.clientLabel")}</th>
              {shown.map((type) => (
                <th key={type.key} style={head}>
                  {titleOf(type.key)}
                  <br />
                  <span style={{ fontWeight: 400 }}>
                    {t("channels.quota.planLabel")}: {type.quota === null ? t("channels.quota.unlimited") : type.quota}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.clients.map((row) => (
              <tr key={row.id}>
                <td style={{ ...cell, whiteSpace: "nowrap" }}>
                  <Link href={clientPath(row.slug)}>{row.name}</Link>
                </td>
                {shown.map((type) => {
                  const used = row.used[type.key] ?? 0;
                  const allocation = row.allocations[type.key] ?? null;
                  const allowed = allocation === null ? type.quota : type.quota === null ? allocation : Math.min(allocation, type.quota);
                  const over = allowed !== null && used > allowed;
                  const excluded = type.quota === 0 && allocation === null;
                  return (
                    <td key={type.key} style={cell}>
                      <QuotaStepper
                        value={allocation}
                        disabled={busy !== null}
                        onChange={(next) => void setAllocation(row.id, type.key, next)}
                      />
                      <small style={{ display: "block", marginTop: 4, color: over ? "#b91c1c" : "var(--muted)" }}>
                        {excluded
                          ? t("channels.quota.notIncluded")
                          : allowed === null
                            ? t("channels.quota.inUseFree", { used })
                            : over
                              ? t("channels.quota.overLimit", { used, quota: allowed })
                              : t("channels.quota.inUseOf", { used, quota: allowed })}
                      </small>
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr>
              <td style={{ ...cell, fontWeight: 600 }}>{t("channels.quota.usedLabel")}</td>
              {shown.map((type) => (
                <td
                  key={type.key}
                  style={{ ...cell, fontWeight: 600, color: type.quota !== null && type.used > type.quota ? "#b91c1c" : "var(--ink)" }}
                >
                  {type.used}
                  {type.quota === null ? "" : ` / ${type.quota}`}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}
