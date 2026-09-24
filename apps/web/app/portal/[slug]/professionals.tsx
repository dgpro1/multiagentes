"use client";

import { useEffect, useState } from "react";
import { ProfessionalsView } from "@/components/professionals-view";
import { api } from "@/lib/api";
import type { PortalClientDetails } from "@/types";

/** The client's professionals, from its portal. The zone the hours are read in comes from the client's details,
 * which need their own function and permission: without them the label is simply left out. */
export function PortalProfessionals({ slug, canManage, canReadClient }: { slug: string; canManage: boolean; canReadClient: boolean }) {
  const [timezone, setTimezone] = useState<string | null>(null);
  useEffect(() => {
    if (!canReadClient) return;
    api<PortalClientDetails>(`/portal/${slug}/client`).then((client) => setTimezone(client.timezone)).catch(() => {});
  }, [slug, canReadClient]);
  return <ProfessionalsView apiBase={`/portal/${slug}`} canManage={canManage} timezone={timezone} />;
}
