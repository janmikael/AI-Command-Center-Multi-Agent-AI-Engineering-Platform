"use client";

import { RunList } from "@/components/runs/run-list";
import { PageHeader } from "@/components/shell/page-header";

export default function RunsPage() {
  return (
    <>
      <PageHeader
        title="Runs"
        description="Every agent run, across every task — what it's doing, what it spent, and how it ended."
      />
      <RunList />
    </>
  );
}
