import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { LoginForm } from "@/components/auth/login-form";
import { getOperator } from "@/lib/api/server";
import { safeNextPath } from "@/lib/auth/redirect";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const next = safeNextPath((await searchParams).next);
  if (await getOperator()) redirect(next);

  return (
    <main className="flex min-h-dvh items-center justify-center bg-muted/40 px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <div
            aria-hidden
            className="grid size-10 place-items-center rounded-xl bg-primary font-mono text-sm font-semibold text-primary-foreground"
          >
            AC
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">AI Command Center</h1>
            <p className="text-sm text-muted-foreground">Sign in to the operator console</p>
          </div>
        </div>
        <LoginForm next={next} />
      </div>
    </main>
  );
}
