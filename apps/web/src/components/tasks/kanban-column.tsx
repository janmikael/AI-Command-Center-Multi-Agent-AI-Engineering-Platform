import { useDroppable } from "@dnd-kit/core";

import { TaskCard } from "@/components/tasks/task-card";
import type { TaskOut } from "@/lib/api/hooks";
import type { components } from "@/lib/api/schema";
import { TASK_STATUS_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

type TaskStatus = components["schemas"]["TaskStatus"];

export function KanbanColumn({ status, tasks }: { status: TaskStatus; tasks: TaskOut[] }) {
  const { setNodeRef, isOver, active } = useDroppable({ id: status });
  const draggedTask = active?.data.current?.task as TaskOut | undefined;
  // While a card is over this column, show whether dropping it here would actually be
  // legal — evaluated from allowed_transitions the API already returned with the task,
  // so this needs no extra round trip.
  const wouldAccept = draggedTask ? draggedTask.allowed_transitions.includes(status) : true;

  return (
    <div className="flex w-72 shrink-0 flex-col">
      <div className="mb-2 flex items-center justify-between px-1">
        <h3 className="text-sm font-medium">{TASK_STATUS_LABEL[status]}</h3>
        <span className="text-xs tabular-nums text-muted-foreground">{tasks.length}</span>
      </div>
      <div
        ref={setNodeRef}
        data-column={status}
        data-over={isOver || undefined}
        data-rejecting={(isOver && !wouldAccept) || undefined}
        className={cn(
          "flex min-h-24 flex-1 flex-col gap-2 rounded-xl border border-dashed border-transparent bg-muted/30 p-2 transition-colors",
          "data-over:border-primary/40 data-over:bg-primary/5",
          "data-rejecting:border-destructive/50 data-rejecting:bg-destructive/5"
        )}
      >
        {tasks.map((task) => (
          <TaskCard key={task.key} task={task} />
        ))}
      </div>
    </div>
  );
}
