import Link from "next/link";

import { describeEvent } from "@/components/activity/describe-event";
import { EventIcon } from "@/components/activity/event-icon";
import type { components } from "@/lib/api/schema";
import { formatRelativeTime, SEVERITY_COLOR } from "@/lib/format";

type EventOut = components["schemas"]["EventOut"];

export function EventRow({ event }: { event: EventOut }) {
  return (
    <div className="flex items-start gap-3 py-2.5">
      <div className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-muted">
        <EventIcon type={event.type} className="size-3.5 text-muted-foreground" />
      </div>
      <div className="min-w-0 flex-1">
        <p className={`truncate text-sm ${SEVERITY_COLOR[event.severity] ?? ""}`}>
          {describeEvent(event)}
        </p>
        <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
          <span>{event.actor_label}</span>
          <span aria-hidden>·</span>
          <time dateTime={event.ts}>{formatRelativeTime(event.ts)}</time>
          {event.task_key && (
            <>
              <span aria-hidden>·</span>
              <Link href={`/tasks/${event.task_key}`} className="underline-offset-2 hover:underline">
                {event.task_key}
              </Link>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
