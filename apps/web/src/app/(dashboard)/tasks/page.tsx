"use client";

import { KanbanSquare } from "lucide-react";

import { CreateTaskDialog } from "@/components/tasks/create-task-dialog";
import { KanbanBoard } from "@/components/tasks/kanban-board";
import { EmptyState } from "@/components/shell/empty-state";
import { PageHeader } from "@/components/shell/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useTasks } from "@/lib/api/hooks";

const FILTERS = {};

export default function TasksPage() {
  const { data: tasks, isPending } = useTasks(FILTERS);

  return (
    <>
      <div className="mb-6 flex items-start justify-between gap-4">
        <PageHeader
          title="Tasks"
          description="Backlog → Planning → Working → Review → Completed."
        />
        <CreateTaskDialog />
      </div>

      {isPending ? (
        <div className="flex gap-4">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-96 w-72 shrink-0 rounded-xl" />
          ))}
        </div>
      ) : !tasks || tasks.length === 0 ? (
        <EmptyState
          icon={KanbanSquare}
          title="No tasks yet"
          description="Create the first task to see it move across the board."
        />
      ) : (
        <KanbanBoard tasks={tasks} filters={FILTERS} />
      )}
    </>
  );
}
