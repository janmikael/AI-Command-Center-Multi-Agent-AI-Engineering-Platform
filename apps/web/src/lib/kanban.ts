import type { TaskOut } from "@/lib/api/hooks";
import type { components } from "@/lib/api/schema";
import { BOARD_COLUMNS } from "@/lib/format";

type TaskStatus = components["schemas"]["TaskStatus"];

export interface GroupedTasks {
  columns: Record<TaskStatus, TaskOut[]>;
  other: TaskOut[];
}

const BOARD_SET = new Set<TaskStatus>(BOARD_COLUMNS);

/** Splits the flat task list into the 5 board columns plus everything else ("Other"). */
export function groupTasksByColumn(tasks: TaskOut[]): GroupedTasks {
  const columns = {} as Record<TaskStatus, TaskOut[]>;
  for (const status of BOARD_COLUMNS) columns[status] = [];
  const other: TaskOut[] = [];
  for (const task of tasks) {
    if (BOARD_SET.has(task.status)) columns[task.status].push(task);
    else other.push(task);
  }
  return { columns, other };
}

/** Whether dropping `task` onto `target` is a legal move — the server's own state
 * machine already computed this per task (task.allowed_transitions); dragging just
 * reads it, never re-derives the rules client-side. */
export function canDropOn(task: TaskOut, target: TaskStatus): boolean {
  return task.status !== target && task.allowed_transitions.includes(target);
}
