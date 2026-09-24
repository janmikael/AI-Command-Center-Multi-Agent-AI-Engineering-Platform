import { describe, expect, it } from "vitest";

import { canDropOn, groupTasksByColumn } from "@/lib/kanban";
import { BOARD_COLUMNS, OTHER_COLUMN_STATUSES } from "@/lib/format";
import type { TaskOut } from "@/lib/api/hooks";

function task(overrides: Partial<TaskOut> = {}): TaskOut {
  return {
    key: "ACC-1",
    title: "Task",
    status: "backlog",
    priority: 3,
    progress_pct: 0,
    version: 0,
    project_slug: "acc",
    assignee_slug: null,
    assignee_name: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    allowed_transitions: ["planning", "cancelled"],
    ...overrides,
  };
}

describe("groupTasksByColumn", () => {
  it("buckets every board column, even when empty", () => {
    const { columns } = groupTasksByColumn([]);
    expect(Object.keys(columns).sort()).toEqual([...BOARD_COLUMNS].sort());
    for (const status of BOARD_COLUMNS) expect(columns[status]).toEqual([]);
  });

  it("sorts each task into its own status column", () => {
    const backlog = task({ key: "A", status: "backlog" });
    const working = task({ key: "B", status: "working" });
    const { columns } = groupTasksByColumn([backlog, working]);
    expect(columns.backlog).toEqual([backlog]);
    expect(columns.working).toEqual([working]);
    expect(columns.planning).toEqual([]);
  });

  it("puts every non-board status into Other, none dropped", () => {
    const others = OTHER_COLUMN_STATUSES.map((status, i) => task({ key: `O${i}`, status }));
    const { other, columns } = groupTasksByColumn(others);
    expect(other).toHaveLength(OTHER_COLUMN_STATUSES.length);
    expect(other.map((t) => t.status).sort()).toEqual([...OTHER_COLUMN_STATUSES].sort());
    for (const status of BOARD_COLUMNS) expect(columns[status]).toEqual([]);
  });

  it("preserves task order within a column", () => {
    const a = task({ key: "A", status: "backlog", priority: 1 });
    const b = task({ key: "B", status: "backlog", priority: 2 });
    const { columns } = groupTasksByColumn([a, b]);
    expect(columns.backlog.map((t) => t.key)).toEqual(["A", "B"]);
  });
});

describe("canDropOn", () => {
  it("allows a drop onto an explicitly allowed target", () => {
    const t = task({ status: "backlog", allowed_transitions: ["planning", "cancelled"] });
    expect(canDropOn(t, "planning")).toBe(true);
  });

  it("rejects a drop onto a status not in allowed_transitions", () => {
    const t = task({ status: "backlog", allowed_transitions: ["planning", "cancelled"] });
    expect(canDropOn(t, "completed")).toBe(false);
  });

  it("rejects dropping a task back onto its own current column", () => {
    // Even if the state machine happened to list it (it never does), same-column is a no-op.
    const t = task({ status: "backlog", allowed_transitions: ["backlog", "planning"] });
    expect(canDropOn(t, "backlog")).toBe(false);
  });

  it("rejects any drop on a terminal task with no allowed transitions", () => {
    const t = task({ status: "completed", allowed_transitions: [] });
    expect(canDropOn(t, "working")).toBe(false);
  });
});
