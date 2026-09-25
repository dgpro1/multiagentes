"use client";

import { useEffect, useState } from "react";
import { ServicesView } from "@/components/services-view";
import { api } from "@/lib/api";
import type { PortalClientDetails } from "@/types";

export function PortalServices({ slug, canManage }: { slug: string; canManage: boolean }) {
  const [currency, setCurrency] = useState<string>("USD");

  useEffect(() => {
    api<PortalClientDetails>(`/portal/${slug}/client`)
      .then((client) => {
        if (client?.currency) setCurrency(client.currency);
      })
      .catch(() => {});
  }, [slug]);

  return <ServicesView apiBase={`/portal/${slug}`} canManage={canManage} currency={currency} />;
}
