import { Bot } from "lucide-react";
import type { Metadata } from "next";

import { EmptyState } from "@/components/shell/empty-state";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Agents" };

export default function AgentsPage() {
  return (
    <>
      <PageHeader title="Agents" description="Every AI worker, its role and what it is doing." />
      <EmptyState
        icon={Bot}
        title="No agents registered"
        description="The architect, developer and designer agents are seeded in Phase 1."
      />
    </>
  );
}
