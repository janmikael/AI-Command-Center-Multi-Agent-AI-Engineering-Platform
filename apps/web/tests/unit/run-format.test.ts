import { describe, expect, it } from "vitest";

import {
  ACTIVE_RUN_STATUSES,
  formatCostUsd,
  formatDurationMs,
  RUN_STATUS_COLOR,
  RUN_STATUS_LABEL,
} from "@/lib/format";

const RUN_STATUSES = [
  "queued",
  "starting",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "timed_out",
];

describe("RUN_STATUS_LABEL and RUN_STATUS_COLOR", () => {
  it("has a label and a badge color for every status acc.domain.agent_run.RunStatus defines", () => {
    for (const status of RUN_STATUSES) {
      expect(RUN_STATUS_LABEL[status]).toBeTruthy();
      expect(RUN_STATUS_COLOR[status]).toBeTruthy();
    }
  });

  it("gives failed and succeeded visually distinct colors", () => {
    expect(RUN_STATUS_COLOR.failed).not.toBe(RUN_STATUS_COLOR.succeeded);
  });

  it("marks failed and timed_out as destructive-styled", () => {
    expect(RUN_STATUS_COLOR.failed).toContain("destructive");
    expect(RUN_STATUS_COLOR.timed_out).toContain("destructive");
  });
});

describe("ACTIVE_RUN_STATUSES", () => {
  it("matches acc.domain.agent_run.ACTIVE exactly: queued, starting, running", () => {
    expect(ACTIVE_RUN_STATUSES).toEqual(new Set(["queued", "starting", "running"]));
  });

  it("excludes every terminal status", () => {
    for (const status of ["succeeded", "failed", "cancelled", "timed_out"]) {
      expect(ACTIVE_RUN_STATUSES.has(status)).toBe(false);
    }
  });
});

describe("formatCostUsd", () => {
  it("formats a string cost (as RunOut.cost_usd arrives over the wire)", () => {
    expect(formatCostUsd("0.123456")).toBe("$0.1235");
  });

  it("formats a numeric cost", () => {
    expect(formatCostUsd(2)).toBe("$2.0000");
  });

  it("formats zero cleanly", () => {
    expect(formatCostUsd("0")).toBe("$0.0000");
  });

  it("falls back to an em dash for a non-numeric value rather than throwing", () => {
    expect(formatCostUsd("not-a-number")).toBe("—");
  });
});

describe("formatDurationMs", () => {
  it("shows an em dash for null (a call not yet completed)", () => {
    expect(formatDurationMs(null)).toBe("—");
  });

  it("shows milliseconds under one second", () => {
    expect(formatDurationMs(250)).toBe("250 ms");
  });

  it("shows seconds at and above one second", () => {
    expect(formatDurationMs(1500)).toBe("1.5 s");
  });
});
