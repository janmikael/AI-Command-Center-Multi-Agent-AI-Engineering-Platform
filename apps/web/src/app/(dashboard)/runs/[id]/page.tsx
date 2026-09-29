"use client";

import { PlayCircle } from "lucide-react";
import Link from "next/link";
import { use } from "react";

import { FileChangesViewer } from "@/components/runs/file-changes-viewer";
import { RunSummaryHeader } from "@/components/runs/run-summary-header";
import { ToolCallTimeline } from "@/components/runs/tool-call-timeline";
import { UsagePanel } from "@/components/runs/usage-panel";
import { EmptyState } from "@/components/shell/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useRun } from "@/lib/api/hooks";

export default function RunDetailPage({ params }: PageProps<"/runs/[id]">) {
  const { id } = use(params);
  const { data: run, isPending, isError } = useRun(id);

  if (isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-40 rounded-xl" />
        <Skeleton className="h-96 rounded-xl" />
      </div>
    );
  }

  if (isError || !run) {
    return <EmptyState icon={PlayCircle} title="Run not found" description={`No run "${id}".`} />;
  }

  return (
    <>
      <div className="mb-6">
        <Link href="/runs" className="text-sm text-muted-foreground hover:underline">
          ← Back to runs
        </Link>
      </div>

      <div className="mb-6">
        <RunSummaryHeader run={run} />
      </div>

      <Tabs defaultValue="tool-calls">
        <TabsList>
          <TabsTrigger value="tool-calls">Tool calls</TabsTrigger>
          <TabsTrigger value="usage">Usage &amp; cost</TabsTrigger>
          <TabsTrigger value="file-changes">File changes</TabsTrigger>
        </TabsList>

        <TabsContent value="tool-calls">
          <ToolCallTimeline runId={run.id} />
        </TabsContent>
        <TabsContent value="usage">
          <UsagePanel runId={run.id} />
        </TabsContent>
        <TabsContent value="file-changes">
          <FileChangesViewer runId={run.id} />
        </TabsContent>
      </Tabs>
    </>
  );
}
