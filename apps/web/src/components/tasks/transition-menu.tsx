"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useTransitionTask, type TaskDetailOut } from "@/lib/api/hooks";
import type { components } from "@/lib/api/schema";
import { TASK_STATUS_LABEL } from "@/lib/format";

type TaskStatus = components["schemas"]["TaskStatus"];

export function TransitionMenu({ task }: { task: TaskDetailOut["task"] }) {
  const [target, setTarget] = useState<TaskStatus | null>(null);
  const [reason, setReason] = useState("");
  const transition = useTransitionTask();

  function confirm() {
    if (!target) return;
    transition.mutate(
      { key: task.key, version: task.version, to: target, reason: reason.trim() || undefined },
      {
        onSuccess: () => {
          toast.success(`${task.key} moved to ${TASK_STATUS_LABEL[target]}`);
          setTarget(null);
          setReason("");
        },
        onError: (error) => toast.error("Couldn't change status", { description: error.message }),
      }
    );
  }

  if (task.allowed_transitions.length === 0) {
    return <span className="text-sm text-muted-foreground">No further transitions</span>;
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="outline" size="sm" />}>
          Move to…
          <ChevronDown data-icon="inline-end" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {task.allowed_transitions.map((status) => (
            <DropdownMenuItem key={status} onClick={() => setTarget(status)}>
              {TASK_STATUS_LABEL[status]}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={target !== null} onOpenChange={(open) => !open && setTarget(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>
              Move {task.key} to {target ? TASK_STATUS_LABEL[target] : ""}
            </DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Label htmlFor="transition-reason">Reason (optional)</Label>
            <Textarea
              id="transition-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTarget(null)}>
              Cancel
            </Button>
            <Button onClick={confirm} disabled={transition.isPending}>
              {transition.isPending ? "Moving…" : "Confirm"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
