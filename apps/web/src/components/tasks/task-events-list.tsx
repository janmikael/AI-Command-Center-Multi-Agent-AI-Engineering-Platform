"use client";

import { Activity } from "lucide-react";

import { EventRow } from "@/components/activity/event-row";
import { EmptyState } from "@/components/shell/empty-state";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useTaskEvents } from "@/lib/api/hooks";

export function TaskEventsList({ taskKey }: { taskKey: string }) {
  const { data, isPending, hasNextPage, isFetchingNextPage, fetchNextPage } =
    useTaskEvents(taskKey);

  if (isPending) return <Skeleton className="h-40 rounded-xl" />;

  const items = data?.pages.flatMap((page) => page.items) ?? [];
  if (items.length === 0) {
    return (
      <EmptyState icon={Activity} title="No activity yet" description="Nothing logged for this task." />
    );
  }

  return (
    <div>
      <div className="divide-y">
        {items.map((event) => (
          <EventRow key={event.id} event={event} />
        ))}
      </div>
      {hasNextPage && (
        <Button
          variant="outline"
          size="sm"
          className="mt-3"
          onClick={() => fetchNextPage()}
          disabled={isFetchingNextPage}
        >
          {isFetchingNextPage ? "Loading…" : "Load more"}
        </Button>
      )}
    </div>
  );
}
