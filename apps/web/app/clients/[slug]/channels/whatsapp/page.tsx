"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { WhatsAppChannelView } from "@/components/channels/whatsapp-channel";
import { api } from "@/lib/api";
import { CLIENT_UUID_RE } from "@/lib/routes";
import type { Client } from "@/types";

// The agency's WhatsApp (QR) lines of a client. The body is shared with the client portal (components/channels);
// a legacy UUID address moves to the slug once, keeping query and hash.
export default function WhatsAppChannelPage() {
  const { slug } = useParams<{ slug: string }>();
  const router = useRouter();
  useEffect(() => {
    if (!CLIENT_UUID_RE.test(slug)) return;
    let cancelled = false;
    api<Client>(`/clients/${slug}`).then((loaded) => {
      if (!cancelled) router.replace(`/clients/${loaded.portal_slug}/channels/whatsapp${window.location.search}${window.location.hash}`);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [slug, router]);
  return <WhatsAppChannelView />;
}
