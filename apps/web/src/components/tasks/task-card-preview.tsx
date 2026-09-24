import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import type { TaskOut } from "@/lib/api/hooks";
import { initials, PRIORITY_LABEL } from "@/lib/format";

const PRIORITY_DOT: Record<number, string> = {
  1: "bg-destructive",
  2: "bg-warning",
  3: "bg-sky-500",
  4: "bg-muted-foreground/50",
  5: "bg-muted-foreground/30",
};

/**
 * The visual dnd-kit renders inside <DragOverlay> while a card is being dragged — a
 * plain div, deliberately not the real <a> TaskCard. If the actual link were what
 * tracked the cursor, mouseup would land on it and fire its native navigation on top
 * of the drop, sending the operator to the task page instead of just moving the card.
 * Keeping the overlay non-interactive is what makes the two never collide.
 */
export function TaskCardPreview({ task }: { task: TaskOut }) {
  return (
    <div className="w-72 rotate-2 cursor-grabbing rounded-lg bg-card p-3 text-sm shadow-lg ring-1 ring-primary/40">
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">{task.key}</span>
        <span
          className={`mt-1 size-1.5 shrink-0 rounded-full ${PRIORITY_DOT[task.priority]}`}
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
    </div>
  );
}
