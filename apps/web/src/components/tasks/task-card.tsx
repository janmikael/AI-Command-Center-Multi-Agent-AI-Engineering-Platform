import { useDraggable } from "@dnd-kit/core";
import Link from "next/link";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import type { TaskOut } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";
import { initials, PRIORITY_LABEL } from "@/lib/format";

const PRIORITY_DOT: Record<number, string> = {
  1: "bg-destructive",
  2: "bg-warning",
  3: "bg-sky-500",
  4: "bg-muted-foreground/50",
  5: "bg-muted-foreground/30",
};

export function TaskCard({ task, draggable = true }: { task: TaskOut; draggable?: boolean }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: task.key,
    disabled: !draggable,
    data: { task },
  });

  return (
    <Link
      href={`/tasks/${task.key}`}
      ref={setNodeRef}
      {...(draggable ? attributes : {})}
      {...(draggable ? listeners : {})}
      className={cn(
        "block touch-none rounded-lg bg-card p-3 text-sm ring-1 ring-foreground/10 transition-shadow hover:ring-foreground/20",
        // The real drag visual is the DragOverlay (task-card-preview.tsx); this source
        // element just dims in place so there's no draggable <a> under the cursor to
        // accidentally navigate on drop — see task-card-preview.tsx for why.
        isDragging && "opacity-30"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">{task.key}</span>
        <span
          className={cn("mt-1 size-1.5 shrink-0 rounded-full", PRIORITY_DOT[task.priority])}
          title={PRIORITY_LABEL[task.priority]}
        />
      </div>
      <p className="mt-1 line-clamp-2 font-medium">{task.title}</p>
      {task.assignee_name && (
        <div className="mt-2 flex items-center gap-1.5">
          <Avatar className="size-5">
            <AvatarFallback className="text-[10px]">{initials(task.assignee_name)}</AvatarFallback>
          </Avatar>
          <span className="truncate text-xs text-muted-foreground">{task.assignee_name}</span>
        </div>
      )}
    </Link>
  );
}
