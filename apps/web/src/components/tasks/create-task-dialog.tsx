"use client";

import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";
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
import { useAgents, useCreateTask } from "@/lib/api/hooks";
import { PRIORITY_LABEL } from "@/lib/format";

const UNASSIGNED = "__unassigned__";

export function CreateTaskDialog() {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("3");
  const [assignee, setAssignee] = useState(UNASSIGNED);
  const { data: agents } = useAgents();
  const createTask = useCreateTask();
  const router = useRouter();

  function reset() {
    setTitle("");
    setDescription("");
    setPriority("3");
    setAssignee(UNASSIGNED);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    createTask.mutate(
      {
        project: "acc",
        title: title.trim(),
        description,
        priority: Number(priority),
        assignee: assignee === UNASSIGNED ? undefined : assignee,
      },
      {
        onSuccess: (detail) => {
          toast.success(`Created ${detail.task.key}`);
          setOpen(false);
          reset();
          router.push(`/tasks/${detail.task.key}`);
        },
        onError: (error) => {
          toast.error("Couldn't create the task", { description: error.message });
        },
      }
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button />}>
        <Plus data-icon="inline-start" />
        New task
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={onSubmit} className="contents">
          <DialogHeader>
            <DialogTitle>Create task</DialogTitle>
          </DialogHeader>

          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="task-title">Title</Label>
              <Input
                id="task-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
                autoFocus
                maxLength={200}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="task-description">Description</Label>
              <Textarea
                id="task-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="task-priority">Priority</Label>
                <Select value={priority} onValueChange={(v) => setPriority(v ?? "3")}>
                  <SelectTrigger id="task-priority" className="w-full">
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
              <div className="flex flex-col gap-2">
                <Label htmlFor="task-assignee">Assignee</Label>
                <Select value={assignee} onValueChange={(v) => setAssignee(v ?? UNASSIGNED)}>
                  <SelectTrigger id="task-assignee" className="w-full">
                    <SelectValue>
                      {(v: string) =>
                        v === UNASSIGNED
                          ? "Unassigned"
                          : (agents?.find((a) => a.slug === v)?.display_name ?? v)
                      }
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={UNASSIGNED}>Unassigned</SelectItem>
                    {agents?.map((agent) => (
                      <SelectItem key={agent.slug} value={agent.slug}>
                        {agent.display_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button type="submit" disabled={!title.trim() || createTask.isPending}>
              {createTask.isPending ? "Creating…" : "Create task"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
