import { redirect } from "next/navigation";

import { Nav } from "@/components/shell/nav";
import { OperatorMenu } from "@/components/shell/operator-menu";
import { SystemStatus } from "@/components/shell/system-status";
import { getOperator } from "@/lib/api/server";

export default async function DashboardLayout({ children }: LayoutProps<"/">) {
  // Authoritative check against the API (the proxy only checked that a cookie exists).
  const operator = await getOperator();
  if (!operator) redirect("/login");

  return (
    <div className="flex min-h-dvh">
      <aside className="hidden w-56 shrink-0 flex-col border-r bg-sidebar px-3 py-4 md:flex">
        <div className="mb-6 flex items-center gap-2 px-2.5">
          <div
            aria-hidden
            className="grid size-7 place-items-center rounded-lg bg-primary font-mono text-[11px] font-semibold text-primary-foreground"
          >
            AC
          </div>
          <span className="text-sm font-semibold tracking-tight">Command Center</span>
        </div>
        <Nav />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between gap-4 border-b bg-background/80 px-4 backdrop-blur md:px-8">
          <div className="md:hidden">
            <Nav orientation="horizontal" />
          </div>
          <div className="hidden md:block">
            <SystemStatus />
          </div>
          <OperatorMenu operator={operator} />
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 md:px-8">{children}</main>
      </div>
    </div>
  );
}
