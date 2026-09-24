"use client";

import { Pencil } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useUpdateTask, type TaskDetailOut } from "@/lib/api/hooks";
import { PRIORITY_LABEL } from "@/lib/format";

export function EditTaskDialog({ detail }: { detail: TaskDetailOut }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(detail.task.title);
  const [description, setDescription] = useState(detail.description);
  const [priority, setPriority] = useState(String(detail.task.priority));
  const updateTask = useUpdateTask();

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (next) {
      // Reset to the latest server values each time the dialog opens.
      setTitle(detail.task.title);
      setDescription(detail.description);
      setPriority(String(detail.task.priority));
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    updateTask.mutate(
      {
        key: detail.task.key,
        version: detail.task.version,
        body: { title: title.trim(), description, priority: Number(priority) },
      },
      {
        onSuccess: () => {
          toast.success("Task updated");
          setOpen(false);
        },
        onError: (error) => toast.error("Couldn't save", { description: error.message }),
      }
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <Pencil data-icon="inline-start" />
        Edit
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={onSubmit} className="contents">
          <DialogHeader>
            <DialogTitle>Edit {detail.task.key}</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="edit-title">Title</Label>
              <Input
                id="edit-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
                maxLength={200}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="edit-description">Description</Label>
              <Textarea
                id="edit-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={4}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="edit-priority">Priority</Label>
              <Select value={priority} onValueChange={(v) => setPriority(v ?? "3")}>
                <SelectTrigger id="edit-priority" className="w-full">
                  <SelectValue>{(v: string) => PRIORITY_LABEL[Number(v)]}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(PRIORITY_LABEL).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button type="submit" disabled={!title.trim() || updateTask.isPending}>
              {updateTask.isPending ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
