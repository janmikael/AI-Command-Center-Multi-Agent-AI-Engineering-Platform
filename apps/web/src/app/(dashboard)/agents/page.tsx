"use client";

import { Bot } from "lucide-react";

import { AgentTable } from "@/components/agents/agent-table";
import { EmptyState } from "@/components/shell/empty-state";
import { PageHeader } from "@/components/shell/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgents } from "@/lib/api/hooks";

export default function AgentsPage() {
  const { data: agents, isPending } = useAgents();

  return (
    <>
      <PageHeader title="Agents" description="Every AI worker, its role and what it is doing." />
      {isPending ? (
        <Skeleton className="h-64 rounded-xl" />
      ) : !agents || agents.length === 0 ? (
        <EmptyState
          icon={Bot}
          title="No agents registered"
          description="The architect, developer and designer agents are seeded automatically."
        />
      ) : (
        <AgentTable agents={agents} />
      )}
    </>
  );
}
