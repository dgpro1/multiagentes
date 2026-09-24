"use client";

import { useParams } from "next/navigation";
import { AgentDetailView } from "@/components/agents/agent-detail";

// The agency's agent editor; the address carries the tab (/agents/{id}[/{tab}]). The body is shared with the client portal (components/agents).
export default function AgentDetailPage() {
  const { id, tab } = useParams<{ id: string; tab?: string[] }>();
  return <AgentDetailView id={id} segments={tab} />;
}
