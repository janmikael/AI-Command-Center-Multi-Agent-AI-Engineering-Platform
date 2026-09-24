"use client";

import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { KanbanColumn } from "@/components/tasks/kanban-column";
import { OtherLane } from "@/components/tasks/other-lane";
import { TaskCardPreview } from "@/components/tasks/task-card-preview";
import { useTransitionTask, type TaskOut } from "@/lib/api/hooks";
import { queryKeys } from "@/lib/api/queryKeys";
import type { components } from "@/lib/api/schema";
import { BOARD_COLUMNS } from "@/lib/format";
import { canDropOn, groupTasksByColumn } from "@/lib/kanban";

type TaskStatus = components["schemas"]["TaskStatus"];

export function KanbanBoard({ tasks, filters }: { tasks: TaskOut[]; filters: Record<string, unknown> }) {
  const queryClient = useQueryClient();
  const transition = useTransitionTask();
  const [activeTask, setActiveTask] = useState<TaskOut | null>(null);
  const { columns, other } = groupTasksByColumn(tasks);

  // A short drag needs to still register as a click-through to the task detail page;
  // requiring a few pixels of movement before a drag "starts" is what makes that work.
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  function handleDragStart(event: DragStartEvent) {
    setActiveTask((event.active.data.current?.task as TaskOut) ?? null);
  }

  function handleDragEnd(event: DragEndEvent) {
    setActiveTask(null);
    const task = event.active.data.current?.task as TaskOut | undefined;
    const target = event.over?.id as TaskStatus | undefined;
    if (!task || !target) return;
    if (!canDropOn(task, target)) {
      if (task.status !== target) {
        toast.error(`Can't move ${task.key} to that column`, {
          description: "That status change isn't allowed from here.",
        });
      }
      return;
    }

    const listKey = queryKeys.tasks.list(filters);
    const previous = queryClient.getQueryData<TaskOut[]>(listKey);
    // Optimistic: move the card immediately, then confirm with the server.
    queryClient.setQueryData<TaskOut[]>(listKey, (current) =>
      current?.map((t) => (t.key === task.key ? { ...t, status: target } : t))
    );

    transition.mutate(
      { key: task.key, version: task.version, to: target },
      {
        onError: (error) => {
          queryClient.setQueryData(listKey, previous); // roll back the optimistic move
          toast.error(`Couldn't move ${task.key}`, { description: error.message });
        },
      }
    );
  }

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setActiveTask(null)}
    >
      <div className="flex gap-4 overflow-x-auto pb-2">
        {BOARD_COLUMNS.map((status) => (
          <KanbanColumn key={status} status={status} tasks={columns[status]} />
        ))}
      </div>
      <OtherLane tasks={other} />
      <DragOverlay>{activeTask ? <TaskCardPreview task={activeTask} /> : null}</DragOverlay>
    </DndContext>
  );
}
