import { describe, expect, it } from "vitest";

import { RULE_CATEGORY_LABEL, RULE_EFFECT_COLOR, RULE_EFFECT_LABEL } from "@/lib/format";
import type { components } from "@/lib/api/schema";

type RuleCategory = components["schemas"]["RuleCategory"];
type RuleEffect = components["schemas"]["RuleEffect"];

const CATEGORIES: RuleCategory[] = ["security", "tools", "code", "infrastructure"];
const EFFECTS: RuleEffect[] = ["allow", "require_approval", "deny", "advisory"];

describe("RULE_CATEGORY_LABEL", () => {
  it("has a human label for every category the API can return", () => {
    for (const category of CATEGORIES) {
      expect(RULE_CATEGORY_LABEL[category]).toBeTruthy();
    }
  });
});

describe("RULE_EFFECT_LABEL and RULE_EFFECT_COLOR", () => {
  it("has a label and a badge color for every effect the API can return", () => {
    for (const effect of EFFECTS) {
      expect(RULE_EFFECT_LABEL[effect]).toBeTruthy();
      expect(RULE_EFFECT_COLOR[effect]).toBeTruthy();
    }
  });

  it("gives deny and allow visually distinct colors", () => {
    expect(RULE_EFFECT_COLOR.deny).not.toBe(RULE_EFFECT_COLOR.allow);
  });

  it("marks deny as a destructive-styled badge", () => {
    expect(RULE_EFFECT_COLOR.deny).toContain("destructive");
  });
});
