/**
 * A single, shared reconnecting WebSocket connection to /ws/telemetry.
 * Every component that needs live telemetry subscribes to this one
 * connection (see Frontend/state/TelemetryContext.tsx) instead of opening
 * its own — see the objective's "one shared connection/state source, no
 * duplicate WebSocket connections" requirement.
 *
 * Reconnects with exponential backoff + jitter on any close/error, and
 * never fabricates data while disconnected — subscribers just stop
 * receiving updates until the next successful connection.
 */

import { WS_BASE_URL } from "./env";
import type { WsMessage } from "./types";

export type ConnectionStatus = "connecting" | "connected" | "reconnecting" | "offline";

type MessageListener = (message: WsMessage) => void;
type StatusListener = (status: ConnectionStatus) => void;

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 20000;

function backoffDelay(attempt: number): number {
  const exponential = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt);
  // Full jitter — avoids every tab reconnecting in lockstep after a shared
  // backend restart.
  return Math.random() * exponential;
}

class TelemetrySocket {
  private socket: WebSocket | null = null;
  private status: ConnectionStatus = "offline";
  private attempt = 0;
  private reconnectTimer: number | null = null;
  private closedByClient = false;
  private messageListeners = new Set<MessageListener>();
  private statusListeners = new Set<StatusListener>();

  connect(): void {
    if (this.socket || this.reconnectTimer != null) return; // already connecting/connected
    this.closedByClient = false;
    this.open();
  }

  private open(): void {
    this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");

    let socket: WebSocket;
    try {
      socket = new WebSocket(`${WS_BASE_URL}/ws/telemetry`);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.setStatus("connected");
    };

    socket.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as WsMessage;
        this.messageListeners.forEach((listener) => listener(parsed));
      } catch {
        // A malformed frame is never let through to subscribers — the UI
        // should not be able to observe an unparseable "update".
      }
    };

    const handleClose = () => {
      if (this.socket !== socket) return; // stale handler from a previous socket
      this.socket = null;
      if (this.closedByClient) {
        this.setStatus("offline");
        return;
      }
      this.scheduleReconnect();
    };

    socket.onclose = handleClose;
    socket.onerror = () => socket.close();
  }

  private scheduleReconnect(): void {
    this.setStatus("reconnecting");
    const delay = backoffDelay(this.attempt);
    this.attempt += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.closedByClient) this.open();
    }, delay);
  }

  disconnect(): void {
    this.closedByClient = true;
    if (this.reconnectTimer != null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.socket?.close();
    this.socket = null;
    this.setStatus("offline");
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.statusListeners.forEach((listener) => listener(status));
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  onMessage(listener: MessageListener): () => void {
    this.messageListeners.add(listener);
    return () => this.messageListeners.delete(listener);
  }

  onStatusChange(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }
}

// Module-level singleton — every import of this module shares the same
// connection, which is what makes "avoid duplicate connections" true by
// construction rather than by convention.
export const telemetrySocket = new TelemetrySocket();

if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", () => telemetrySocket.disconnect());
}
