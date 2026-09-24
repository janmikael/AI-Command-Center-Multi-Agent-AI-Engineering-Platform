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
});

describe("allQueryKeys", () => {
  it("covers overview, events, tasks and agents for a resync", () => {
    const keys = allQueryKeys();
    expect(keys).toContainEqual(queryKeys.overview);
    expect(keys).toContainEqual(queryKeys.events.all);
    expect(keys).toContainEqual(queryKeys.tasks.all);
    expect(keys).toContainEqual(queryKeys.agents.all);
  });
});
