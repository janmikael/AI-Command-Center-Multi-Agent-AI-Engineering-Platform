"use client";

import { Activity, Bot, CircleAlert, Database, KanbanSquare, Radio, Zap } from "lucide-react";
import Link from "next/link";

import { AgentCard } from "@/components/agents/agent-card";
import { EventRow } from "@/components/activity/event-row";
import { TaskStatusBadge } from "@/components/tasks/task-status-badge";
import { EmptyState } from "@/components/shell/empty-state";
import { PageHeader } from "@/components/shell/page-header";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOverview } from "@/lib/api/hooks";

function StatCard({
  title,
  value,
  icon: Icon,
}: {
  title: string;
  value: number;
  icon: typeof Bot;
}) {
  return (
    <Card>
      <CardHeader>
        <CardDescription className="flex items-center gap-2">
          <Icon className="size-4" aria-hidden />
          {title}
        </CardDescription>
        <CardTitle className="text-3xl tabular-nums">{value}</CardTitle>
      </CardHeader>
    </Card>
  );
}

function SystemPanel({
  system,
}: {
  system: { database: boolean; redis: boolean; realtime_clients: number };
}) {
  const row = (label: string, ok: boolean) => (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className={ok ? "text-success" : "text-destructive"}>{ok ? "OK" : "Down"}</span>
    </div>
  );
  return (
    <Card>
      <CardHeader>
        <CardDescription className="flex items-center gap-2">
          <Database className="size-4" aria-hidden />
          System
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-1.5">
        {row("Database", system.database)}
        {row("Redis", system.redis)}
        <div className="flex items-center justify-between text-sm">
          <span className="flex items-center gap-1.5 text-muted-foreground">
            <Radio className="size-3.5" aria-hidden /> Live connections
          </span>
          <span className="tabular-nums">{system.realtime_clients}</span>
        </div>
      </CardContent>
    </Card>
  );
}

export default function OverviewPage() {
  const { data, isPending, isError } = useOverview();

  return (
    <>
      <PageHeader title="Overview" description="What every agent is doing, right now." />

      {isError && (
        <div className="mb-6 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <CircleAlert className="size-4 shrink-0" aria-hidden />
          Couldn&apos;t load the overview. It will retry automatically.
        </div>
      )}

      {isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-28 rounded-xl" />
          ))}
        </div>
      ) : data ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              title="Active agents"
              value={data.agents.filter((a) => a.status === "working").length}
              icon={Bot}
            />
            <StatCard title="Tasks in progress" value={data.active_tasks.length} icon={KanbanSquare} />
            <StatCard title="Events today" value={data.events_last_24h} icon={Zap} />
            <SystemPanel system={data.system} />
          </div>

          <section className="mt-8">
            <h2 className="mb-3 text-sm font-medium text-muted-foreground">Agents</h2>
            {data.agents.length === 0 ? (
              <EmptyState icon={Bot} title="No agents registered" description="Run `acc seed`." />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {data.agents.map((agent) => (
                  <AgentCard key={agent.slug} agent={agent} />
                ))}
              </div>
            )}
          </section>

          <div className="mt-8 grid gap-8 lg:grid-cols-2">
            <section>
              <h2 className="mb-3 text-sm font-medium text-muted-foreground">Active tasks</h2>
              {data.active_tasks.length === 0 ? (
                <EmptyState
                  icon={KanbanSquare}
                  title="Nothing in progress"
                  description="Tasks in planning, working or review will show here."
                />
              ) : (
                <Card>
                  <CardContent className="divide-y p-0">
                    {data.active_tasks.map((task) => (
                      <Link
                        key={task.key}
                        href={`/tasks/${task.key}`}
                        className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-muted/40"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{task.title}</p>
                          <p className="text-xs text-muted-foreground">
                            {task.key}
                            {task.assignee_name ? ` · ${task.assignee_name}` : ""}
                          </p>
                        </div>
                        <TaskStatusBadge status={task.status} />
                      </Link>
                    ))}
                  </CardContent>
                </Card>
              )}
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-muted-foreground">Recent activity</h2>
              {data.recent_events.length === 0 ? (
                <EmptyState
                  icon={Activity}
                  title="No activity yet"
                  description="Agent logs, task changes and decisions stream here live."
                />
              ) : (
                <Card>
                  <CardContent className="divide-y p-0 px-4">
                    {data.recent_events.slice(0, 8).map((event) => (
                      <EventRow key={event.id} event={event} />
                    ))}
                  </CardContent>
                </Card>
              )}
            </section>
          </div>
        </>
      ) : null}
    </>
  );
}
