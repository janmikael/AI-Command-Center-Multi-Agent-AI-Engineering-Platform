"use client";

import { ShieldCheck } from "lucide-react";

import { CreateRuleDialog } from "@/components/governance/create-rule-dialog";
import { RuleList } from "@/components/governance/rule-list";
import { EmptyState } from "@/components/shell/empty-state";
import { PageHeader } from "@/components/shell/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useGovernanceRules } from "@/lib/api/hooks";

export default function GovernancePage() {
  const { data: rules, isPending } = useGovernanceRules();

  return (
    <>
      <div className="mb-6 flex items-start justify-between gap-4">
        <PageHeader
          title="Governance"
          description="Global AI Rules. These apply identically to every project — projects hold knowledge, not security exceptions."
        />
        <CreateRuleDialog />
      </div>

      {isPending ? (
        <Skeleton className="h-64 rounded-xl" />
      ) : !rules || rules.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title="No governance rules yet"
          description="Create a rule to constrain what AI agents are allowed to do."
        />
      ) : (
        <RuleList rules={rules} />
      )}
    </>
  );
}
