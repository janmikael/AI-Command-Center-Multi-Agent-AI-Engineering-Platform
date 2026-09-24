"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { allQueryKeys, queryKeysToInvalidate } from "@/lib/realtime/invalidate";
import { RealtimeConnection, type RealtimeStatus, type WireEvent } from "@/lib/realtime/connection";

const RealtimeStatusContext = createContext<RealtimeStatus>("connecting");

export function useRealtimeStatus(): RealtimeStatus {
  return useContext(RealtimeStatusContext);
}

function wsUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/ws`;
}

/**
 * Owns the single, app-lifetime WebSocket connection. Mounted once, inside the
 * dashboard layout (so it survives client-side navigation between pages), below
 * QueryClientProvider (it calls useQueryClient to invalidate on incoming events).
 *
 * By design this never writes query *data* — every event only triggers a refetch of
 * whatever's mounted. REST stays the single source of truth; the socket is a "something
 * changed, go re-ask" signal, per PLAN-PHASE-0-1.md §3.3.
 */
export function RealtimeProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<RealtimeStatus>("connecting");
  const connectionRef = useRef<RealtimeConnection | null>(null);

  useEffect(() => {
    const handleEvent = (event: WireEvent) => {
      for (const key of queryKeysToInvalidate(event)) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    };
    const handleResync = () => {
      for (const key of allQueryKeys()) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    };

    const connection = new RealtimeConnection({
      url: wsUrl(),
      onStatusChange: setStatus,
      onEvent: handleEvent,
      onResync: handleResync,
    });
    connectionRef.current = connection;
    connection.start();

    return () => {
      connection.stop();
      connectionRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- queryClient is stable for the app's lifetime
  }, []);

  return (
    <RealtimeStatusContext.Provider value={status}>{children}</RealtimeStatusContext.Provider>
  );
}
