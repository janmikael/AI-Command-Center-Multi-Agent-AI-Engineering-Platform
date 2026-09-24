"use client";

import { Activity } from "lucide-react";
import { useState } from "react";

import { EventRow } from "@/components/activity/event-row";
import { EmptyState } from "@/components/shell/empty-state";
import { PageHeader } from "@/components/shell/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgents, useEventsFeed, type EventFilters } from "@/lib/api/hooks";

const ALL = "__all__";
const SEVERITIES = ["debug", "info", "warn", "error"];
const TYPES = [
  "task.created",
  "task.updated",
  "task.status_changed",
  "task.assigned",
  "agent.message",
  "agent.decision",
  "agent.registered",
  "project.created",
  "repository.added",
];

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: string[];
}) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v ?? ALL)}>
      <SelectTrigger aria-label={label} className="w-40">
        <SelectValue placeholder={label}>
          {(v: string) => (v === ALL ? `All ${label.toLowerCase()}` : v)}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>All {label.toLowerCase()}</SelectItem>
        {options.map((option) => (
          <SelectItem key={option} value={option}>
            {option}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export default function ActivityPage() {
  const [type, setType] = useState(ALL);
  const [agent, setAgent] = useState(ALL);
  const [severity, setSeverity] = useState(ALL);
  const { data: agents } = useAgents();

  const filters: EventFilters = {
    type: type === ALL ? undefined : type,
    agent: agent === ALL ? undefined : agent,
    severity: severity === ALL ? undefined : severity,
  };
  const { data, isPending, hasNextPage, isFetchingNextPage, fetchNextPage } =
    useEventsFeed(filters);

  const items = data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      <PageHeader title="Activity" description="Append-only log of everything that happens." />

      <div className="mb-4 flex flex-wrap gap-2">
        <FilterSelect label="Types" value={type} onChange={setType} options={TYPES} />
        <FilterSelect
          label="Agents"
          value={agent}
          onChange={setAgent}
          options={agents?.map((a) => a.slug) ?? []}
        />
        <FilterSelect label="Severities" value={severity} onChange={setSeverity} options={SEVERITIES} />
      </div>

      {isPending ? (
        <Skeleton className="h-96 rounded-xl" />
      ) : items.length === 0 ? (
        <EmptyState icon={Activity} title="No events" description="Nothing matches these filters yet." />
      ) : (
        <>
          <Card>
            <CardContent className="divide-y p-0 px-4">
              {items.map((event) => (
                <EventRow key={event.id} event={event} />
              ))}
            </CardContent>
          </Card>
          {hasNextPage && (
            <div className="mt-4 flex justify-center">
              <Button variant="outline" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
                {isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          )}
        </>
      )}
    </>
  );
}
