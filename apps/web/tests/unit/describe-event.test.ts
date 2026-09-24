import { describe, expect, it } from "vitest";

import { describeEvent, type DescribableEvent } from "@/components/activity/describe-event";

function event(overrides: Partial<DescribableEvent>): DescribableEvent {
  return { type: "x", task_key: null, agent_slug: null, payload: {}, ...overrides };
}

describe("describeEvent", () => {
  it("describes task.created with the title", () => {
    expect(
      describeEvent(
        event({ type: "task.created", task_key: "ACC-1", payload: { title: "Add health endpoint" } })
      )
    ).toBe('Created ACC-1: "Add health endpoint"');
  });

  it("describes task.status_changed with readable labels and an optional reason", () => {
    const withReason = describeEvent(
      event({
        type: "task.status_changed",
        task_key: "ACC-1",
        payload: { from_status: "backlog", to_status: "planning", reason: "ready to start" },
      })
    );
    expect(withReason).toBe("Moved ACC-1 from Backlog to Planning — ready to start");

    const withoutReason = describeEvent(
      event({
        type: "task.status_changed",
        task_key: "ACC-1",
        payload: { from_status: "working", to_status: "review", reason: null },
      })
    );
    expect(withoutReason).toBe("Moved ACC-1 from Working to Review");
  });

  it("describes an assignment and an unassignment differently", () => {
    expect(
      describeEvent(
        event({ type: "task.assigned", task_key: "ACC-1", payload: { to_agent: "developer" } })
      )
    ).toBe("Assigned ACC-1 to developer");
    expect(
      describeEvent(event({ type: "task.assigned", task_key: "ACC-1", payload: { to_agent: null } }))
    ).toBe("Unassigned ACC-1");
  });

  it("describes agent messages and decisions using the agent's own words", () => {
    expect(
      describeEvent(event({ type: "agent.message", payload: { text: "Reading the code." } }))
    ).toBe("Reading the code.");
    expect(
      describeEvent(event({ type: "agent.decision", payload: { decision: "Use approach A" } }))
    ).toBe("Use approach A");
  });

  it("falls back to the raw type for anything unrecognized", () => {
    expect(describeEvent(event({ type: "some.future.type" }))).toBe("some.future.type");
  });
});
