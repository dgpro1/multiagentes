"use client";

import { useEffect, useState } from "react";
import { Alert } from "@/components/ui";
import { ClientDetails } from "@/components/client-details";
import { FormSkeleton } from "@/components/skeleton";
import { api, messageFrom } from "@/lib/api";
import type { PortalClientDetails } from "@/types";

/** The client's own business details, edited from its portal (`/portal/{slug}/client`). */
export function DetailsView({ slug }: { slug: string }) {
  const [client, setClient] = useState<PortalClientDetails | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<PortalClientDetails>(`/portal/${slug}/client`).then(setClient).catch((err) => setError(messageFrom(err)));
  }, [slug]);
  if (error) return <Alert>{error}</Alert>;
  if (!client) return <FormSkeleton sections={1} />;
  return <ClientDetails mode="portal" slug={slug} client={client} onChange={setClient} />;
}
