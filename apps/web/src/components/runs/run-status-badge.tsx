import { Badge } from "@/components/ui/badge";
import { RUN_STATUS_COLOR, RUN_STATUS_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

export function RunStatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "border-transparent",
        RUN_STATUS_COLOR[status] ?? "bg-muted text-muted-foreground",
        className
      )}
    >
      {RUN_STATUS_LABEL[status] ?? status}
    </Badge>
  );
}
