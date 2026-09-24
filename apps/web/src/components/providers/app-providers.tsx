"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Toaster } from "sonner";

import { RealtimeProvider } from "@/components/providers/realtime-provider";

export function AppProviders({ children }: { children: ReactNode }) {
  // useState (not a module-level singleton) so each browser tab/SSR request gets its own
  // client and nothing leaks between requests on the server.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            refetchOnWindowFocus: true,
          },
        },
      })
  );

  return (
    <QueryClientProvider client={queryClient}>
      <RealtimeProvider>{children}</RealtimeProvider>
      <Toaster richColors position="bottom-right" />
    </QueryClientProvider>
  );
}
