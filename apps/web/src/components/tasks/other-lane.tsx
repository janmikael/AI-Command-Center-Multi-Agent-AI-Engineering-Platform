"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";

import { TaskCard } from "@/components/tasks/task-card";
import { TaskStatusBadge } from "@/components/tasks/task-status-badge";
import type { TaskOut } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";

/** Blocked / awaiting_approval / failed / cancelled: shown, never hidden — just kept out
 * of the main board so those columns don't distract from active work. Not a drop target:
 * moving a task into one of these statuses is deliberate (with a reason), done from the
 * task detail page's transition menu, not a quick drag. */
export function OtherLane({ tasks }: { tasks: TaskOut[] }) {
  const [open, setOpen] = useState(tasks.length > 0);
  if (tasks.length === 0) return null;

  return (
    <div className="mt-6 border-t pt-4">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground"
        aria-expanded={open}
      >
        <ChevronDown className={cn("size-4 transition-transform", !open && "-rotate-90")} aria-hidden />
        Other ({tasks.length})
      </button>
      {open && (
        <div className="mt-3 flex flex-wrap gap-3">
          {tasks.map((task) => (
            <div key={task.key} className="w-64">
              <div className="mb-1">
                <TaskStatusBadge status={task.status} />
              </div>
              <TaskCard task={task} draggable={false} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
