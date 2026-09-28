"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ScrollText } from "lucide-react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { PlatformShell } from "@/components/platform-shell";
import { EmptyState, PageHead } from "@/components/ui";
import type { PlatformOverview } from "@/types";

function tokens(value: number): string {
  return value.toLocaleString();
}

function cost(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `$${value.toFixed(4)}`;
}

export default function PlatformOverviewPage() {
  const t = useT();
  const [data, setData] = useState<PlatformOverview | null>(null);

  useEffect(() => {
    let active = true;
    api<PlatformOverview>("/platform/overview")
      .then((current) => { if (active) setData(current); })
      .catch(() => {});
    return () => { active = false; };
  }, []);

  return (
    <PlatformShell>
      <PageHead
        eyebrow={t("platform.overview.eyebrow")}
        title={t("platform.overview.title")}
        description={t("platform.overview.description")}
        action={<Link className="button primary" href="/superadmin/agencies/new">+ {t("platform.list.newAgency")}</Link>}
      />
      {data && (
        <>
          <div className="metrics-grid">
            <article className="metric-card"><small>{t("platform.overview.agencies")}</small><strong>{data.agencies}</strong></article>
            <article className="metric-card"><small>{t("platform.overview.blocked")}</small><strong>{data.blocked_agencies}</strong></article>
            <article className="metric-card"><small>{t("platform.overview.clients")}</small><strong>{data.clients}</strong></article>
            <article className="metric-card"><small>{t("platform.overview.agents")}</small><strong>{data.agents}</strong></article>
          </div>
          <div className="table-shell">
            <table>
              <tbody>
                <tr><th>{t("platform.overview.usageTitle")}</th><td /></tr>
                <tr><td>{t("platform.overview.replies")}</td><td>{data.usage.replies}</td></tr>
                <tr><td>{t("platform.overview.tokensIn")}</td><td>{tokens(data.usage.input_tokens)}</td></tr>
                <tr><td>{t("platform.overview.tokensOut")}</td><td>{tokens(data.usage.output_tokens)}</td></tr>
                <tr><td>{t("platform.overview.cost")}</td><td>{cost(data.usage.cost_usd)}</td></tr>
                {data.usage.unpriced_replies > 0 && (
                  <tr><td colSpan={2}>{t("platform.overview.unpriced", { count: data.usage.unpriced_replies })}</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <section style={{ marginTop: 24 }}>
            <h2>{t("platform.overview.linesTitle")}</h2>
            <p style={{ color: "var(--muted)", fontSize: 13 }}>{t("platform.overview.linesCopy")}</p>
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>{t("platform.overview.channelType")}</th>
                    <th>{t("platform.overview.linesUsed")}</th>
                    <th>{t("platform.overview.agenciesCapped")}</th>
                    <th>{t("platform.overview.agenciesAtLimit")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.channels.map((row) => (
                    <tr key={row.key}>
                      <td>{t(`platform.detail.modules.${row.key}` as const)}</td>
                      <td>{row.used}</td>
                      <td>{row.agencies_capped}</td>
                      <td style={{ color: row.agencies_at_limit > 0 ? "#b91c1c" : undefined }}>
                        {row.agencies_at_limit}
                        {row.agencies_over_limit > 0 ? ` (${t("platform.overview.agenciesOverLimit", { count: row.agencies_over_limit })})` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section style={{ marginTop: 24 }}>
            <h2>{t("platform.overview.recentTitle")}</h2>
            {data.recent_events.length === 0 ? (
              <EmptyState icon={<ScrollText size={20} />} title={t("platform.overview.recentEmpty")} description="" />
            ) : (
              <div className="table-shell">
                <table>
                  <thead><tr><th>{t("platform.audit.when")}</th><th>{t("platform.audit.actor")}</th><th>{t("platform.audit.action")}</th></tr></thead>
                  <tbody>
                    {data.recent_events.map((event) => (
                      <tr key={event.id}>
                        <td>{new Date(event.created_at).toLocaleString()}</td>
                        <td>{event.actor_name}</td>
                        <td>{event.action}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p style={{ marginTop: 8 }}><Link className="table-link" href="/superadmin/audit">{t("platform.overview.viewAll")}</Link></p>
          </section>
        </>
      )}
    </PlatformShell>
  );
}
