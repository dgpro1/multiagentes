"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Building2, Plus, Search } from "lucide-react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { PlatformShell } from "@/components/platform-shell";
import { EmptyState, PageHead } from "@/components/ui";
import { platformAgencyPath } from "@/lib/routes";
import type { PlatformAgency } from "@/types";

export default function PlatformAgenciesPage() {
  const t = useT();
  const [agencies, setAgencies] = useState<PlatformAgency[] | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    setQuery(new URLSearchParams(window.location.search).get("q") ?? "");
  }, []);

  useEffect(() => {
    let active = true;
    api<PlatformAgency[]>(`/platform/agencies?q=${encodeURIComponent(query)}`)
      .then((rows) => { if (active) setAgencies(rows); })
      .catch(() => {});
    return () => { active = false; };
  }, [query]);

  function search(next: string) {
    setQuery(next);
    const url = new URL(window.location.href);
    if (next) url.searchParams.set("q", next);
    else url.searchParams.delete("q");
    history.replaceState(null, "", url.toString());
  }

  return (
    <PlatformShell>
      <PageHead
        eyebrow={t("platform.list.eyebrow")}
        title={t("platform.list.title")}
        description={t("platform.list.description")}
        action={<Link className="button primary" href="/superadmin/agencies/new"><Plus size={16} /> {t("platform.list.newAgency")}</Link>}
      />
      <div className="toolbar" style={{ margin: "16px 0" }}>
        <div className="stitch-clients-search" style={{ maxWidth: 360 }}>
          <Search size={16} className="stitch-clients-search-icon" />
          <input
            defaultValue={query}
            placeholder={t("platform.list.search")}
            onKeyDown={(event) => { if (event.key === "Enter") search((event.target as HTMLInputElement).value); }}
          />
        </div>
      </div>
      {agencies === null ? null : agencies.length === 0 ? (
        <EmptyState
          icon={<Building2 size={22} />}
          title={t("platform.list.empty")}
          description=""
          action={<Link className="button primary" href="/superadmin/agencies/new">{t("platform.list.emptyAction")}</Link>}
        />
      ) : (
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>{t("platform.create.name")}</th>
                <th>{t("platform.overview.clients")}</th>
                <th>{t("platform.overview.agents")}</th>
              </tr>
            </thead>
            <tbody>
              {agencies.map((agency) => (
                <tr key={agency.id}>
                  <td>
                    <Link className="table-link" href={platformAgencyPath(agency.slug)}>{agency.name}</Link>
                    {agency.access_status === "blocked" && <span className="status status-inactive" style={{ marginLeft: 8 }}><i />{t("platform.list.blocked")}</span>}
                  </td>
                  <td>{t("platform.list.clients", { count: agency.client_count })}</td>
                  <td>{t("platform.list.agents", { count: agency.agent_count })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </PlatformShell>
  );
}
