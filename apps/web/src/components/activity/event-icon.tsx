import {
  Bot,
  CheckCircle2,
  CircleDot,
  FolderPlus,
  GitBranch,
  Lightbulb,
  MessageSquare,
  PencilLine,
  UserPlus,
  type LucideIcon,
} from "lucide-react";

const ICON_BY_PREFIX: [string, LucideIcon][] = [
  ["task.created", FolderPlus],
  ["task.status_changed", CircleDot],
  ["task.assigned", UserPlus],
  ["task.updated", PencilLine],
  ["agent.message", MessageSquare],
  ["agent.decision", Lightbulb],
  ["agent.registered", Bot],
  ["project.created", FolderPlus],
  ["repository.added", GitBranch],
];

export function iconForEventType(type: string): LucideIcon {
  return ICON_BY_PREFIX.find(([prefix]) => type === prefix)?.[1] ?? CheckCircle2;
}

export function EventIcon({ type, className }: { type: string; className?: string }) {
  // Not actually creating a component here — just picking a reference out of the
  // static ICON_BY_PREFIX table above (every entry is a module-level import).
  const Icon = iconForEventType(type);
  // eslint-disable-next-line react-hooks/static-components
  return <Icon className={className ?? "size-4"} aria-hidden />;
}
