"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { PlatformShell } from "@/components/platform-shell";
import { EmptyState, PageHead } from "@/components/ui";
import { ScrollText } from "lucide-react";
import type { PlatformAuditEvent } from "@/types";

export default function PlatformAuditPage() {
  const t = useT();
  const [events, setEvents] = useState<PlatformAuditEvent[] | null>(null);

  useEffect(() => {
    let active = true;
    api<PlatformAuditEvent[]>("/platform/audit-events?limit=100")
      .then((rows) => { if (active) setEvents(rows); })
      .catch(() => {});
    return () => { active = false; };
  }, []);

  return (
    <PlatformShell>
      <PageHead eyebrow={t("platform.overview.eyebrow")} title={t("platform.audit.title")} description={t("platform.audit.description")} />
      {events === null ? null : events.length === 0 ? (
        <EmptyState icon={<ScrollText size={22} />} title={t("platform.audit.empty")} description="" />
      ) : (
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>{t("platform.audit.when")}</th>
                <th>{t("platform.audit.actor")}</th>
                <th>{t("platform.audit.action")}</th>
                <th>{t("platform.audit.target")}</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td>{new Date(event.created_at).toLocaleString()}</td>
                  <td>{event.actor_name || "—"}</td>
                  <td>{event.action}</td>
                  <td>{event.target_agency_id ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </PlatformShell>
  );
}
