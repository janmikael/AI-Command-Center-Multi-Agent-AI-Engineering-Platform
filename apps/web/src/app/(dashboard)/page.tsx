import { Activity, Bot, KanbanSquare } from "lucide-react";
import type { Metadata } from "next";

import { EmptyState } from "@/components/shell/empty-state";
import { PageHeader } from "@/components/shell/page-header";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getOperator } from "@/lib/api/server";

export const metadata: Metadata = { title: "Overview" };

const SUMMARY = [
  { title: "Active agents", icon: Bot, note: "Agent registry arrives in Phase 1" },
  { title: "Tasks in progress", icon: KanbanSquare, note: "Task board arrives in Phase 1" },
  { title: "Events today", icon: Activity, note: "Activity stream arrives in Phase 1" },
];

export default async function OverviewPage() {
  const operator = await getOperator();

  return (
    <>
      <PageHeader
        title="Overview"
        description={operator ? `Signed in as ${operator.display_name}` : undefined}
      />
      <div className="grid gap-4 sm:grid-cols-3">
        {SUMMARY.map(({ title, icon: Icon, note }) => (
          <Card key={title}>
            <CardHeader>
              <CardDescription className="flex items-center gap-2">
                <Icon className="size-4" aria-hidden />
                {title}
              </CardDescription>
              <CardTitle className="text-3xl tabular-nums text-muted-foreground">–</CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-muted-foreground">{note}</CardContent>
          </Card>
        ))}
      </div>
      <section className="mt-8">
        <h2 className="mb-3 text-sm font-medium text-muted-foreground">Recent activity</h2>
        <EmptyState
          icon={Activity}
          title="No activity yet"
          description="Agent logs, task changes and decisions will stream here live once the event system is in place."
        />
      </section>
    </>
  );
}
