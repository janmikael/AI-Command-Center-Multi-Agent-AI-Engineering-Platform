import { Activity } from "lucide-react";
import type { Metadata } from "next";

import { EmptyState } from "@/components/shell/empty-state";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Activity" };

export default function ActivityPage() {
  return (
    <>
      <PageHeader title="Activity" description="Append-only log of everything that happens." />
      <EmptyState
        icon={Activity}
        title="No events yet"
        description="The event stream and live WebSocket feed arrive in Phase 1."
      />
    </>
  );
}
