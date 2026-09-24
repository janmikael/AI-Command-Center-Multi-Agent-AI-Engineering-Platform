"use client";

import { KanbanSquare } from "lucide-react";
import Link from "next/link";
import { use } from "react";

import { AssignAgentSelect } from "@/components/tasks/assign-agent-select";
import { EditTaskDialog } from "@/components/tasks/edit-task-dialog";
import { StatusHistory } from "@/components/tasks/status-history";
import { TaskEventsList } from "@/components/tasks/task-events-list";
import { TaskStatusBadge } from "@/components/tasks/task-status-badge";
import { TransitionMenu } from "@/components/tasks/transition-menu";
import { EmptyState } from "@/components/shell/empty-state";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { useTask } from "@/lib/api/hooks";
import { formatDateTime, PRIORITY_LABEL } from "@/lib/format";

export default function TaskDetailPage({ params }: PageProps<"/tasks/[key]">) {
  const { key } = use(params);
  const { data: detail, isPending, isError } = useTask(key);

  if (isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 rounded-xl" />
        <Skeleton className="h-96 rounded-xl" />
      </div>
    );
  }

  if (isError || !detail) {
    return (
      <EmptyState icon={KanbanSquare} title="Task not found" description={`No task "${key}".`} />
    );
  }

  const { task } = detail;

  return (
    <>
      <div className="mb-6">
        <Link href="/tasks" className="text-sm text-muted-foreground hover:underline">
          ← Back to board
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-muted-foreground">{task.key}</p>
            <h1 className="text-2xl font-semibold tracking-tight">{task.title}</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <EditTaskDialog detail={detail} />
            <TransitionMenu task={task} />
          </div>
        </div>
      </div>

      <Card className="mb-6">
        <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <Label className="text-xs text-muted-foreground">Status</Label>
            <div className="mt-1.5">
              <TaskStatusBadge status={task.status} />
            </div>
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Priority</Label>
            <p className="mt-1.5 text-sm">{PRIORITY_LABEL[task.priority]}</p>
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Assignee</Label>
            <div className="mt-1.5">
              <AssignAgentSelect task={task} />
            </div>
          </div>
          <div>
            <Label className="text-xs text-muted-foreground" title="Calculated automatically once subtasks exist">
              Progress
            </Label>
            <p className="mt-1.5 text-sm text-muted-foreground">
              {task.progress_pct > 0 ? `${task.progress_pct}%` : "—"}
            </p>
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="details">
        <TabsList>
          <TabsTrigger value="details">Details</TabsTrigger>
          <TabsTrigger value="history">Status history</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>

        <TabsContent value="details" className="space-y-6">
          <div>
            <h3 className="mb-2 text-sm font-medium text-muted-foreground">Description</h3>
            {detail.description ? (
              <p className="whitespace-pre-wrap text-sm">{detail.description}</p>
            ) : (
              <p className="text-sm text-muted-foreground">No description.</p>
            )}
          </div>
          {detail.acceptance_criteria.length > 0 && (
            <div>
              <h3 className="mb-2 text-sm font-medium text-muted-foreground">
                Acceptance criteria
              </h3>
              <ul className="space-y-1.5 text-sm">
                {detail.acceptance_criteria.map((c) => (
                  <li key={c.id} className="flex items-center gap-2">
                    <input type="checkbox" checked={c.done} readOnly className="rounded" />
                    <span className={c.done ? "text-muted-foreground line-through" : ""}>
                      {c.text}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">Created by</dt>
              <dd>{detail.created_by ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Created</dt>
              <dd>{formatDateTime(task.created_at)}</dd>
            </div>
            {detail.repository && (
              <div>
                <dt className="text-muted-foreground">Repository</dt>
                <dd>{detail.repository}</dd>
              </div>
            )}
            {detail.completed_at && (
              <div>
                <dt className="text-muted-foreground">Completed</dt>
                <dd>{formatDateTime(detail.completed_at)}</dd>
              </div>
            )}
          </dl>
        </TabsContent>

        <TabsContent value="history">
          <StatusHistory history={detail.status_history} />
        </TabsContent>

        <TabsContent value="activity">
          <TaskEventsList taskKey={task.key} />
        </TabsContent>
      </Tabs>
    </>
  );
}
