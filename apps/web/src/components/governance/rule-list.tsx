"use client";

import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useSetRuleEnabled, type GovernanceRuleOut } from "@/lib/api/hooks";
import { RULE_CATEGORY_LABEL, RULE_EFFECT_COLOR, RULE_EFFECT_LABEL } from "@/lib/format";
import type { components } from "@/lib/api/schema";

type RuleCategory = components["schemas"]["RuleCategory"];

const CATEGORY_ORDER: RuleCategory[] = ["security", "tools", "code", "infrastructure"];

function RuleRow({ rule }: { rule: GovernanceRuleOut }) {
  const setEnabled = useSetRuleEnabled();

  function toggle() {
    setEnabled.mutate(
      { id: rule.id, enabled: !rule.enabled },
      {
        onError: (error) => {
          toast.error("Couldn't update the rule", { description: error.message });
        },
      }
    );
  }

  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div className={rule.enabled ? "" : "opacity-50"}>
        <div className="flex items-center gap-2">
          <p className="font-medium">{rule.title}</p>
          <Badge className={RULE_EFFECT_COLOR[rule.effect]}>{RULE_EFFECT_LABEL[rule.effect]}</Badge>
          {!rule.enabled && <Badge variant="outline">Disabled</Badge>}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{rule.statement}</p>
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={toggle}
        disabled={setEnabled.isPending}
        className="shrink-0"
      >
        {rule.enabled ? "Disable" : "Enable"}
      </Button>
    </div>
  );
}

export function RuleList({ rules }: { rules: GovernanceRuleOut[] }) {
  const byCategory = new Map<RuleCategory, GovernanceRuleOut[]>();
  for (const rule of rules) {
    const group = byCategory.get(rule.category) ?? [];
    group.push(rule);
    byCategory.set(rule.category, group);
  }

  return (
    <div className="flex flex-col gap-6">
      {CATEGORY_ORDER.filter((category) => byCategory.has(category)).map((category) => (
        <section key={category}>
          <h2 className="mb-2 text-sm font-semibold text-muted-foreground">
            {RULE_CATEGORY_LABEL[category]}
          </h2>
          <Card className="px-4">
            <div className="divide-y">
              {byCategory.get(category)!.map((rule) => (
                <RuleRow key={rule.id} rule={rule} />
              ))}
            </div>
          </Card>
        </section>
      ))}
    </div>
  );
}
