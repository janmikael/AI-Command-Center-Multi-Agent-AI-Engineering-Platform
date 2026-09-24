"use client";

import { Plus } from "lucide-react";
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
import { useCreateRule } from "@/lib/api/hooks";
import type { components } from "@/lib/api/schema";
import { RULE_CATEGORY_LABEL, RULE_EFFECT_LABEL } from "@/lib/format";

type RuleCategory = components["schemas"]["RuleCategory"];
type RuleEffect = components["schemas"]["RuleEffect"];

export function CreateRuleDialog() {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<RuleCategory>("security");
  const [title, setTitle] = useState("");
  const [statement, setStatement] = useState("");
  const [effect, setEffect] = useState<RuleEffect>("require_approval");
  const createRule = useCreateRule();

  function reset() {
    setCategory("security");
    setTitle("");
    setStatement("");
    setEffect("require_approval");
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    createRule.mutate(
      { category, title: title.trim(), statement: statement.trim(), effect },
      {
        onSuccess: () => {
          toast.success("Rule created");
          setOpen(false);
          reset();
        },
        onError: (error) => {
          toast.error("Couldn't create the rule", { description: error.message });
        },
      }
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button />}>
        <Plus data-icon="inline-start" />
        New rule
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={onSubmit} className="contents">
          <DialogHeader>
            <DialogTitle>Create governance rule</DialogTitle>
          </DialogHeader>

          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="rule-category">Category</Label>
                <Select
                  value={category}
                  onValueChange={(v) => v && setCategory(v as RuleCategory)}
                >
                  <SelectTrigger id="rule-category" className="w-full">
                    <SelectValue>
                      {(v: RuleCategory) => RULE_CATEGORY_LABEL[v]}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(RULE_CATEGORY_LABEL).map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="rule-effect">Effect</Label>
                <Select value={effect} onValueChange={(v) => v && setEffect(v as RuleEffect)}>
                  <SelectTrigger id="rule-effect" className="w-full">
                    <SelectValue>{(v: RuleEffect) => RULE_EFFECT_LABEL[v]}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(RULE_EFFECT_LABEL).map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="rule-title">Title</Label>
              <Input
                id="rule-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
                autoFocus
                maxLength={200}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="rule-statement">Statement</Label>
              <Textarea
                id="rule-statement"
                value={statement}
                onChange={(e) => setStatement(e.target.value)}
                rows={3}
                required
              />
            </div>
          </div>

          <DialogFooter>
            <Button
              type="submit"
              disabled={!title.trim() || !statement.trim() || createRule.isPending}
            >
              {createRule.isPending ? "Creating…" : "Create rule"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
