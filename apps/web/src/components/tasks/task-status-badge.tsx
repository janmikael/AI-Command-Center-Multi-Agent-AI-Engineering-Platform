import { Badge } from "@/components/ui/badge";
import type { components } from "@/lib/api/schema";
import { TASK_STATUS_COLOR, TASK_STATUS_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

type TaskStatus = components["schemas"]["TaskStatus"];

export function TaskStatusBadge({ status, className }: { status: TaskStatus; className?: string }) {
  return (
    <Badge variant="outline" className={cn("border-transparent", TASK_STATUS_COLOR[status], className)}>
      {TASK_STATUS_LABEL[status]}
    </Badge>
  );
}
