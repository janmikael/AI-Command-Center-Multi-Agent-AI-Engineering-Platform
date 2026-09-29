import { describe, expect, it } from "vitest";

import { queryKeys } from "@/lib/api/queryKeys";
import { allQueryKeys, queryKeysToInvalidate } from "@/lib/realtime/invalidate";

describe("queryKeysToInvalidate", () => {
  it("always invalidates overview and the activity feed", () => {
    const keys = queryKeysToInvalidate({});
    expect(keys).toContainEqual(queryKeys.overview);
    expect(keys).toContainEqual(queryKeys.events.all);
  });

  it("invalidates tasks only when the event has a task_key", () => {
    expect(queryKeysToInvalidate({ task_key: "ACC-1" })).toContainEqual(queryKeys.tasks.all);
    expect(queryKeysToInvalidate({ task_key: null })).not.toContainEqual(queryKeys.tasks.all);
  });

  it("invalidates agents only when the event has an agent_slug", () => {
    expect(queryKeysToInvalidate({ agent_slug: "developer" })).toContainEqual(
      queryKeys.agents.all
    );
    expect(queryKeysToInvalidate({ agent_slug: null })).not.toContainEqual(queryKeys.agents.all);
  });

  it("invalidates both when an event carries both fields (e.g. task.assigned)", () => {
    const keys = queryKeysToInvalidate({ task_key: "ACC-1", agent_slug: "developer" });
    expect(keys).toContainEqual(queryKeys.tasks.all);
    expect(keys).toContainEqual(queryKeys.agents.all);
  });

  it("never touches tasks/agents for a project-level event (neither field set)", () => {
    const keys = queryKeysToInvalidate({});
    expect(keys).not.toContainEqual(queryKeys.tasks.all);
    expect(keys).not.toContainEqual(queryKeys.agents.all);
  });

  it("invalidates governance for governance_rule.* events, which carry neither task_key nor agent_slug", () => {
    const keys = queryKeysToInvalidate({ type: "governance_rule.created" });
    expect(keys).toContainEqual(queryKeys.governance.all);
    expect(keys).not.toContainEqual(queryKeys.tasks.all);
    expect(keys).not.toContainEqual(queryKeys.agents.all);
  });

  it("invalidates governance for project_context_entry.* events too", () => {
    expect(queryKeysToInvalidate({ type: "project_context_entry.deleted" })).toContainEqual(
      queryKeys.governance.all
    );
  });

  it("does not invalidate governance for unrelated event types", () => {
    expect(queryKeysToInvalidate({ type: "task.created" })).not.toContainEqual(
      queryKeys.governance.all
    );
  });

  it("invalidates the runs list and that specific run's detail when run_id is present", () => {
    const keys = queryKeysToInvalidate({ type: "tool_call.denied", run_id: "run-1" });
    expect(keys).toContainEqual(queryKeys.runs.all);
    expect(keys).toContainEqual(queryKeys.runs.detail("run-1"));
  });

  it("does not invalidate a different run's detail query", () => {
    const keys = queryKeysToInvalidate({ run_id: "run-1" });
    expect(keys).not.toContainEqual(queryKeys.runs.detail("run-2"));
  });

  it("never touches runs when the event has no run_id", () => {
    const keys = queryKeysToInvalidate({ type: "task.created" });
    expect(keys).not.toContainEqual(queryKeys.runs.all);
  });
});

describe("allQueryKeys", () => {
  it("covers overview, events, tasks, agents, governance and runs for a resync", () => {
    const keys = allQueryKeys();
    expect(keys).toContainEqual(queryKeys.overview);
    expect(keys).toContainEqual(queryKeys.events.all);
    expect(keys).toContainEqual(queryKeys.tasks.all);
    expect(keys).toContainEqual(queryKeys.agents.all);
    expect(keys).toContainEqual(queryKeys.governance.all);
    expect(keys).toContainEqual(queryKeys.runs.all);
  });
});
