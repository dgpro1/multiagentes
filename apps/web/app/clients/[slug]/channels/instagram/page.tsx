"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { SocialChannelSetup } from "@/components/channels/social-channel-setup";
import { api } from "@/lib/api";
import { CLIENT_UUID_RE } from "@/lib/routes";
import type { Client } from "@/types";

// A legacy UUID address moves to the slug once, keeping query and hash.
export default function InstagramChannelPage() {
  const { slug } = useParams<{ slug: string }>();
  const router = useRouter();
  useEffect(() => {
    if (!CLIENT_UUID_RE.test(slug)) return;
    let cancelled = false;
    api<Client>(`/clients/${slug}`).then((loaded) => {
      if (!cancelled) router.replace(`/clients/${loaded.portal_slug}/channels/instagram${window.location.search}${window.location.hash}`);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [slug, router]);
  return <SocialChannelSetup provider="instagram" />;
}
