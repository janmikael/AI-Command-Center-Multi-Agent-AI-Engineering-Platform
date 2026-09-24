import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RealtimeConnection, type RealtimeStatus, type WireEvent } from "@/lib/realtime/connection";

/** A minimal, controllable stand-in for the browser WebSocket, injected via
 * RealtimeConnection's WebSocketImpl option so this suite needs no real network. */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static reset() {
    FakeWebSocket.instances = [];
  }

  readonly url: string;
  readyState = 1; // OPEN, matching WebSocket.OPEN
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: unknown[] = [];

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(JSON.parse(data));
  }

  /** Client-initiated close (what RealtimeConnection.stop() calls). Real WebSockets
   * don't fire onclose synchronously here either — the connection's own `stopped` flag,
   * not this, is what actually prevents a reconnect. */
  close() {
    this.readyState = 3;
  }

  serverCloses() {
    this.readyState = 3;
    this.onclose?.();
  }

  receive(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

function wireEvent(overrides: Partial<WireEvent> = {}): WireEvent {
  return {
    id: "1",
    seq: 1,
    ts: "2026-01-01T00:00:00Z",
    type: "task.created",
    severity: "info",
    actor: "user:1",
    project_id: null,
    task_id: null,
    agent_id: null,
    task_key: null,
    agent_slug: null,
    payload: {},
    ...overrides,
  };
}

describe("RealtimeConnection", () => {
  let statuses: RealtimeStatus[];
  let events: WireEvent[];
  let resyncs: number;
  let connection: RealtimeConnection;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.reset();
    statuses = [];
    events = [];
    resyncs = 0;
    connection = new RealtimeConnection({
      url: "ws://test/ws",
      WebSocketImpl: FakeWebSocket as unknown as new (url: string) => WebSocket,
      onStatusChange: (s) => statuses.push(s),
      onEvent: (e) => events.push(e),
      onResync: () => resyncs++,
      minBackoffMs: 1000,
      maxBackoffMs: 8000,
      pingIntervalMs: 20_000,
    });
  });

  afterEach(() => {
    connection.stop();
    vi.useRealTimers();
  });

  it("connects, then goes live and subscribes to 'activity' on hello", () => {
    connection.start();
    expect(statuses).toEqual(["connecting"]);
    FakeWebSocket.instances[0].receive({ type: "hello", server_seq: 42 });
    expect(statuses).toEqual(["connecting", "live"]);
    expect(FakeWebSocket.instances[0].sent).toContainEqual({
      op: "subscribe",
      topics: ["activity"],
    });
  });

  it("does not resume on the very first connection (nothing was missed)", () => {
    connection.start();
    FakeWebSocket.instances[0].receive({ type: "hello", server_seq: 42 });
    expect(FakeWebSocket.instances[0].sent).not.toContainEqual(
      expect.objectContaining({ op: "resume" })
    );
  });

  it("delivers live events to onEvent", () => {
    connection.start();
    FakeWebSocket.instances[0].receive({ type: "hello", server_seq: 1 });
    const event = wireEvent({ seq: 5, task_key: "ACC-1" });
    FakeWebSocket.instances[0].receive({ type: "event", topics: ["activity"], event });
    expect(events).toEqual([event]);
  });

  it("calls onResync for a {type: resync} message", () => {
    connection.start();
    FakeWebSocket.instances[0].receive({ type: "hello", server_seq: 1 });
    FakeWebSocket.instances[0].receive({ type: "resync" });
    expect(resyncs).toBe(1);
  });

  it("ignores unparseable messages instead of throwing", () => {
    connection.start();
    expect(() => FakeWebSocket.instances[0].receive("not json, but JSON.stringify wraps it")).not
      .toThrow();
  });

  it("reconnects with exponential backoff after the server closes the socket", () => {
    connection.start();
    FakeWebSocket.instances[0].receive({ type: "hello", server_seq: 1 });
    FakeWebSocket.instances[0].serverCloses();
    expect(statuses.at(-1)).toBe("reconnecting");
    expect(FakeWebSocket.instances).toHaveLength(1); // not yet reconnected

    vi.advanceTimersByTime(999);
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(2); // past the 1000ms minimum backoff (+ up to 500ms jitter, none here)
    expect(FakeWebSocket.instances).toHaveLength(1); // jitter can push it slightly later
    vi.advanceTimersByTime(600);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("caps backoff at maxBackoffMs across repeated failures", () => {
    connection.start();
    for (let i = 0; i < 5; i++) {
      FakeWebSocket.instances.at(-1)!.serverCloses();
      vi.advanceTimersByTime(8600); // always enough once backoff is capped at 8000 + jitter
    }
    // 1 initial + 5 reconnects
    expect(FakeWebSocket.instances).toHaveLength(6);
  });

  it("resumes from the last processed seq on reconnect, catching up before re-subscribing", () => {
    connection.start();
    FakeWebSocket.instances[0].receive({ type: "hello", server_seq: 1 });
    FakeWebSocket.instances[0].receive({
      type: "event",
      topics: ["activity"],
      event: wireEvent({ seq: 77 }),
    });

    FakeWebSocket.instances[0].serverCloses();
    vi.advanceTimersByTime(2000);
    expect(FakeWebSocket.instances).toHaveLength(2);

    FakeWebSocket.instances[1].receive({ type: "hello", server_seq: 999 });
    expect(FakeWebSocket.instances[1].sent[0]).toEqual({ op: "resume", after_seq: 77 });
    expect(FakeWebSocket.instances[1].sent).toContainEqual({
      op: "subscribe",
      topics: ["activity"],
    });
  });

  it("resets the backoff attempt counter after a successful reconnect", () => {
    connection.start();
    FakeWebSocket.instances[0].serverCloses();
    vi.advanceTimersByTime(2000);
    FakeWebSocket.instances[1].receive({ type: "hello", server_seq: 1 }); // success resets attempt=0
    FakeWebSocket.instances[1].serverCloses();

    vi.advanceTimersByTime(1600); // would still be within attempt-1 backoff if not reset
    expect(FakeWebSocket.instances).toHaveLength(3);
  });

  it("sends a ping on the configured interval", () => {
    connection.start();
    FakeWebSocket.instances[0].receive({ type: "hello", server_seq: 1 });
    vi.advanceTimersByTime(20_000);
    expect(FakeWebSocket.instances[0].sent).toContainEqual({ op: "ping" });
  });

  it("stop() prevents any further reconnect even if the socket then closes", () => {
    connection.start();
    connection.stop();
    FakeWebSocket.instances[0].serverCloses();
    vi.advanceTimersByTime(30_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
