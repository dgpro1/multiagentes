"use client";

import { SocialChannelSetup } from "@/components/channels/social-channel-setup";
import { WebChatChannelView } from "@/components/channels/webchat-channel";
import { WhatsAppChannelView } from "@/components/channels/whatsapp-channel";
import { WhatsAppCloudChannelView } from "@/components/channels/whatsapp-cloud-channel";
import type { ChannelHrefs } from "@/components/channels/scope";
import type { ChannelType } from "@/lib/routes";

/** One channel type's page, by its address segment (the portal picks it from the URL). */
export function ChannelScreen({ type, apiBase, hrefFor, client }: { type: ChannelType; apiBase?: string; hrefFor?: ChannelHrefs; client?: { id: string; name: string } | null }) {
  const scope = { apiBase, hrefFor, client };
  switch (type) {
    case "whatsapp": return <WhatsAppChannelView {...scope} />;
    case "whatsapp-cloud": return <WhatsAppCloudChannelView {...scope} />;
    case "webchat": return <WebChatChannelView {...scope} />;
    case "instagram": return <SocialChannelSetup provider="instagram" {...scope} />;
    case "messenger": return <SocialChannelSetup provider="messenger" {...scope} />;
  }
}
