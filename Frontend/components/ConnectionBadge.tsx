import type { ConnectionStatus } from "../lib/wsClient";

const LABEL: Record<ConnectionStatus, string> = {
  connected: "Connected",
  connecting: "Connecting",
  reconnecting: "Reconnecting",
  offline: "Offline",
};

const DOT_COLOR: Record<ConnectionStatus, string> = {
  connected: "rgba(120,235,190,0.95)",
  connecting: "rgba(255,209,102,0.95)",
  reconnecting: "rgba(255,190,102,0.95)",
  offline: "rgba(255,110,148,0.95)",
};

/** A subtle, non-intrusive indicator of the shared /ws/telemetry
 * connection's health — see the objective's "no intrusive alerts"
 * requirement. Lives in WorkspaceNav, next to the existing scenario-status
 * pill, so it's always visible without adding a new UI surface.
 */
export default function ConnectionBadge({ status }: { status: ConnectionStatus }) {
  const color = DOT_COLOR[status];
  return (
    <div
      className="hidden items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-1 md:inline-flex"
      title={`Telemetry WebSocket: ${LABEL[status]}`}
    >
      <span className="relative flex h-1.5 w-1.5">
        {status === "connected" ? (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full" style={{ background: color, opacity: 0.6 }} />
        ) : null}
        <span
          className="relative inline-flex h-1.5 w-1.5 rounded-full"
          style={{ background: color, animation: status === "reconnecting" || status === "connecting" ? "dock-glow-pulse 1.1s ease-in-out infinite" : undefined }}
        />
      </span>
      <span className="text-[0.5rem] uppercase tracking-[0.14em] text-white/56">{LABEL[status]}</span>
    </div>
  );
}
