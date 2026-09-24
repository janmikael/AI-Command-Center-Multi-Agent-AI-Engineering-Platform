"use client";

import { toast } from "sonner";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAgents, useAssignTask, type TaskDetailOut } from "@/lib/api/hooks";

const UNASSIGNED = "__unassigned__";

export function AssignAgentSelect({ task }: { task: TaskDetailOut["task"] }) {
  const { data: agents } = useAgents();
  const assign = useAssignTask();

  function onValueChange(value: string | null) {
    const agentSlug = !value || value === UNASSIGNED ? null : value;
    if (agentSlug === task.assignee_slug) return;
    assign.mutate(
      { key: task.key, version: task.version, agentSlug },
      { onError: (error) => toast.error("Couldn't assign", { description: error.message }) }
    );
  }

  const labelFor = (value: string) =>
    value === UNASSIGNED ? "Unassigned" : (agents?.find((a) => a.slug === value)?.display_name ?? value);

  return (
    <Select value={task.assignee_slug ?? UNASSIGNED} onValueChange={onValueChange}>
      <SelectTrigger className="w-full sm:w-56" aria-label="Assignee">
        {/* Select.Value shows the raw value by default; it needs an explicit render
         * function to show the agent's display name instead of their slug. */}
        <SelectValue>{(value: string) => labelFor(value)}</SelectValue>
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
  );
}
