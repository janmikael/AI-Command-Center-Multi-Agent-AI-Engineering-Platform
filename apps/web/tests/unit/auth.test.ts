import { describe, expect, it } from "vitest";

import { loginErrorMessage } from "@/lib/auth/messages";
import { safeNextPath } from "@/lib/auth/redirect";

describe("safeNextPath", () => {
  it.each([
    ["/tasks", "/tasks"],
    ["/tasks/ACC-1?tab=events", "/tasks/ACC-1?tab=events"],
    [["/agents", "/other"], "/agents"],
  ])("keeps same-site path %j", (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });

  it.each([
    undefined,
    null,
    "",
    "tasks",
    "//evil.example",
    "/\\evil.example",
    "https://evil.example/",
    "javascript:alert(1)",
    "/login",
    "/login?next=/x",
  ])("falls back to / for %j", (input) => {
    expect(safeNextPath(input)).toBe("/");
  });
});

describe("loginErrorMessage", () => {
  it("does not reveal whether the email exists", () => {
    expect(loginErrorMessage(401)).toBe("Invalid email or password.");
  });

  it("rounds rate-limit wait up to whole minutes", () => {
    expect(loginErrorMessage(429, "61")).toMatch(/2 minutes/);
    expect(loginErrorMessage(429, "30")).toMatch(/1 minute\./);
    expect(loginErrorMessage(429, null)).toMatch(/1 minute\./);
  });

  it("explains an origin mismatch", () => {
    expect(loginErrorMessage(403)).toMatch(/configured address/);
  });

  it("falls back to a generic message", () => {
    expect(loginErrorMessage(502)).toMatch(/HTTP 502/);
  });
});
