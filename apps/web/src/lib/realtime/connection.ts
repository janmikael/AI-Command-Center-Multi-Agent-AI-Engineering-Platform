/**
 * A framework-agnostic WebSocket client for the /ws protocol (see backend
 * acc.api.routers.ws and PLAN-PHASE-0-1.md §3.3): hello, subscribe/unsubscribe/ping/
 * resume, exponential-backoff reconnect, catch-up via `resume` using the last seq this
 * connection actually processed.
 *
 * No React import here on purpose — this class is unit-tested directly, with a fake
 * WebSocket constructor injected, independent of any component tree.
 */

export type RealtimeStatus = "connecting" | "live" | "reconnecting";

export interface WireEvent {
  id: string;
  seq: number;
  ts: string;
  type: string;
  severity: string;
  actor: string;
  project_id: string | null;
  task_id: string | null;
  agent_id: string | null;
  task_key: string | null;
  agent_slug: string | null;
  payload: Record<string, unknown>;
}

type ServerMessage =
  | { type: "hello"; server_seq: number }
  | { type: "event"; topics: string[]; event: WireEvent }
  | { type: "resync" }
  | { type: "pong" }
  | { type: "error"; detail: string };

export interface RealtimeConnectionOptions {
  url: string;
  onStatusChange: (status: RealtimeStatus) => void;
  onEvent: (event: WireEvent) => void;
  onResync: () => void;
  /** Injectable for tests; defaults to the global WebSocket. */
  WebSocketImpl?: new (url: string) => WebSocket;
  pingIntervalMs?: number;
  minBackoffMs?: number;
  maxBackoffMs?: number;
}

const DEFAULT_PING_INTERVAL_MS = 20_000;
const DEFAULT_MIN_BACKOFF_MS = 1_000;
const DEFAULT_MAX_BACKOFF_MS = 30_000;

export class RealtimeConnection {
  private readonly opts: Required<RealtimeConnectionOptions>;
  private ws: WebSocket | null = null;
  private stopped = false;
  private attempt = 0;
  private lastProcessedSeq: number | null = null;
  private hasConnectedBefore = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  constructor(options: RealtimeConnectionOptions) {
    this.opts = {
      WebSocketImpl: WebSocket,
      pingIntervalMs: DEFAULT_PING_INTERVAL_MS,
      minBackoffMs: DEFAULT_MIN_BACKOFF_MS,
      maxBackoffMs: DEFAULT_MAX_BACKOFF_MS,
      ...options,
    };
  }

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    this.ws?.close();
    this.ws = null;
  }

  private clearTimers(): void {
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    if (this.pingTimer !== null) clearInterval(this.pingTimer);
    this.reconnectTimer = null;
    this.pingTimer = null;
  }

  private connect(): void {
    if (this.stopped) return;
    this.opts.onStatusChange("connecting");
    const ws = new this.opts.WebSocketImpl(this.opts.url);
    this.ws = ws;

    ws.onmessage = (event: MessageEvent<string>) => this.handleMessage(event.data);
    ws.onclose = () => this.handleClose();
    ws.onerror = () => {
      /* onclose always follows onerror for browser WebSockets; nothing extra to do */
    };
  }

  private handleMessage(raw: string): void {
    let message: ServerMessage;
    try {
      message = JSON.parse(raw) as ServerMessage;
    } catch {
      return;
    }

    switch (message.type) {
      case "hello": {
        this.attempt = 0;
        this.opts.onStatusChange("live");
        this.startPinging();
        if (this.hasConnectedBefore && this.lastProcessedSeq !== null) {
          // A reconnect: catch up on whatever happened while we were disconnected.
          this.send({ op: "resume", after_seq: this.lastProcessedSeq });
        } else {
          this.lastProcessedSeq = message.server_seq;
        }
        this.hasConnectedBefore = true;
        this.send({ op: "subscribe", topics: ["activity"] });
        break;
      }
      case "event": {
        this.lastProcessedSeq = message.event.seq;
        this.opts.onEvent(message.event);
        break;
      }
      case "resync": {
        this.opts.onResync();
        break;
      }
      case "pong":
      case "error":
        break;
    }
  }

  private handleClose(): void {
    this.ws = null;
    this.clearTimers();
    if (this.stopped) return;
    this.opts.onStatusChange("reconnecting");
    const backoff = Math.min(
      this.opts.minBackoffMs * 2 ** this.attempt,
      this.opts.maxBackoffMs
    );
    const jitter = Math.random() * Math.min(500, backoff);
    this.attempt += 1;
    this.reconnectTimer = setTimeout(() => this.connect(), backoff + jitter);
  }

  private startPinging(): void {
    this.pingTimer = setInterval(() => this.send({ op: "ping" }), this.opts.pingIntervalMs);
  }

  private send(payload: Record<string, unknown>): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== WEBSOCKET_OPEN) return;
    ws.send(JSON.stringify(payload));
  }
}

/** WebSocket.OPEN's standard value (1), used instead of a static lookup on the
 * injectable WebSocketImpl constructor so a minimal fake doesn't need to define it. */
const WEBSOCKET_OPEN = 1;
