import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { telemetrySocket, type ConnectionStatus } from "../lib/wsClient";
import { isAiActionEvent, isTelemetrySnapshot, type AiActionEvent, type EventRead, type TelemetrySnapshot } from "../lib/types";

const MAX_EVENTS = 60;
const MAX_AI_ACTION_EVENTS = 20;

type TelemetryContextValue = {
  status: ConnectionStatus;
  /** The latest full TelemetrySnapshot pushed over /ws/telemetry, or null
   * until the first frame arrives (initial load / reconnect). */
  snapshot: TelemetrySnapshot | null;
  /** Every EventRead seen so far this session, oldest first, capped —
   * accumulated from each snapshot's optional `events` field (only present
   * on ticks that produced new ones; see backend SimulationService._broadcast). */
  events: EventRead[];
  /** AI_ACTION_* lifecycle broadcasts (see app.neurocore.actions), oldest
   * first, capped — surfaced separately since they carry a PendingAction,
   * not a telemetry reading. */
  aiActionEvents: AiActionEvent[];
};

const TelemetryContext = createContext<TelemetryContextValue | null>(null);

export function TelemetryProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ConnectionStatus>(telemetrySocket.getStatus());
  const [snapshot, setSnapshot] = useState<TelemetrySnapshot | null>(null);
  const [events, setEvents] = useState<EventRead[]>([]);
  const [aiActionEvents, setAiActionEvents] = useState<AiActionEvent[]>([]);
  const seenEventIds = useRef(new Set<string>());

  useEffect(() => {
    // Idempotent — see TelemetrySocket.connect. Called once per app
    // lifetime; the module-level beforeunload listener in wsClient.ts is
    // what actually tears it down (see the objective's "connect on
    // startup ... clean up on shutdown" requirement).
    telemetrySocket.connect();

    const unsubscribeStatus = telemetrySocket.onStatusChange(setStatus);
    const unsubscribeMessage = telemetrySocket.onMessage((message) => {
      if (isAiActionEvent(message)) {
        setAiActionEvents((current) => [...current, message].slice(-MAX_AI_ACTION_EVENTS));
        return;
      }
      if (isTelemetrySnapshot(message)) {
        setSnapshot(message);
        if (message.events && message.events.length > 0) {
          const fresh = message.events.filter((event) => {
            if (seenEventIds.current.has(event.id)) return false;
            seenEventIds.current.add(event.id);
            return true;
          });
          if (fresh.length > 0) {
            setEvents((current) => {
              const next = [...current, ...fresh].slice(-MAX_EVENTS);
              // Keep the id-dedupe set bounded too, matching the visible window.
              seenEventIds.current = new Set(next.map((event) => event.id));
              return next;
            });
          }
        }
      }
    });

    return () => {
      unsubscribeStatus();
      unsubscribeMessage();
    };
  }, []);

  const value = useMemo<TelemetryContextValue>(
    () => ({ status, snapshot, events, aiActionEvents }),
    [status, snapshot, events, aiActionEvents],
  );

  return <TelemetryContext.Provider value={value}>{children}</TelemetryContext.Provider>;
}

export function useTelemetry(): TelemetryContextValue {
  const ctx = useContext(TelemetryContext);
  if (!ctx) throw new Error("useTelemetry must be used within a TelemetryProvider");
  return ctx;
}
