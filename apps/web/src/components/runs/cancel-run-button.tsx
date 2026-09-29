"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useCancelRun } from "@/lib/api/hooks";

/**
 * The one write in Phase 2.5 (PLAN-PHASE-2.5.md §3.4) — POST /runs/{id}/cancel already
 * existed (M2) and was already one authenticated API call away; this only adds a UI
 * button for it, with the same confirm-before-destructive pattern as TransitionMenu.
 */
export function CancelRunButton({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false);
  const cancel = useCancelRun(runId);

  function confirm() {
    cancel.mutate(undefined, {
      onSuccess: () => {
        toast.success("Cancellation requested");
        setOpen(false);
      },
      onError: (error) => toast.error("Couldn't cancel the run", { description: error.message }),
    });
  }

  return (
    <>
      <Button variant="destructive" size="sm" onClick={() => setOpen(true)}>
        Cancel run
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Cancel this run?</DialogTitle>
            <DialogDescription>
              The orchestrator will stop the running container. This can&apos;t be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Keep running
            </Button>
            <Button variant="destructive" onClick={confirm} disabled={cancel.isPending}>
              {cancel.isPending ? "Cancelling…" : "Cancel run"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
