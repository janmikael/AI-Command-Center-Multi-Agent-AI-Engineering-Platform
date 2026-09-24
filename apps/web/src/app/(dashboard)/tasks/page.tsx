import { KanbanSquare } from "lucide-react";
import type { Metadata } from "next";

import { EmptyState } from "@/components/shell/empty-state";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Tasks" };

export default function TasksPage() {
  return (
    <>
      <PageHeader title="Tasks" description="Backlog → Planning → Working → Review → Completed." />
      <EmptyState
        icon={KanbanSquare}
        title="No tasks yet"
        description="The Kanban board and task creation arrive in Phase 1."
      />
    </>
  );
}
