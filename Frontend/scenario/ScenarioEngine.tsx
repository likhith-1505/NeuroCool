/**
 * ScenarioEngine — the shared source of "what is the cluster doing right
 * now" for every workspace (Mission Control, Digital Twin, AI Copilot,
 * Analytics). Originally a self-contained local simulation; now a thin
 * adapter that:
 *   - reads live telemetry from TelemetryContext (backed by the real
 *     /ws/telemetry WebSocket — see Frontend/state/TelemetryContext.tsx),
 *   - maps the backend's RackTelemetry/DecisionRead/ForecastPoint shapes
 *     onto the same ScenarioRack/ScenarioAi shape every page already
 *     consumes, so the pages themselves barely change, and
 *   - sends real scenario-control requests to the backend (POST
 *     /api/scenario, /reset, /replay) instead of mutating local state.
 *
 * Nothing here invents a value the backend doesn't provide — see each
 * mapping function's comment for where a field is a direct passthrough vs.
 * a labeled bucket derived from a real number (e.g. cooling_efficiency ->
 * "Optimal"/"Elevated"/"Strained").
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { apiClient } from "../lib/apiClient";
import type { ClusterTelemetry, DecisionRead, EventRead, ForecastPoint, RackTelemetry, SimulationStatusRead } from "../lib/types";
import { useSettings } from "../settings/SettingsContext";
import { useTelemetry } from "../state/TelemetryContext";

// Backend scenario keys (see backend/app/simulation/scenario_manager.py) —
// used verbatim as the frontend's ScenarioId so no translation table can
// ever drift out of sync with the backend.
export type ScenarioId = "normal" | "training_burst" | "thermal_spike" | "cooling_failure" | "power_surge";

export type RackPrediction = "Stable" | "Watch" | "Critical Risk";
export type RackCooling = "Optimal" | "Elevated" | "Strained";
export type RackHealthState = "healthy" | "warning" | "critical";

export type ScenarioRack = {
  id: string;
  name: string;
  x: number;
  y: number;
  temperature: number;
  gpu: number;
  jobs: number;
  prediction: RackPrediction;
  cooling: RackCooling;
  coolingEfficiency: number;
  power: string;
  powerDraw: number;
  healthScore: number;
  healthState: RackHealthState;
  recommendation: string;
  activeDecision: DecisionRead | null;
  forecast: ForecastPoint[];
};

export type ScenarioMetrics = {
  clusterHealth: number;
  avgTemperature: number;
  power: number;
  /** No backend field for PUE exists (see app/schemas/cluster.py) — "—"
   * rather than a fabricated number, per the integration objective. */
  pue: string;
  energySaved: number;
  avoidedThrottling: number;
};

export type TimelineEventItem = {
  id: string;
  title: string;
  timestamp: string;
  description: string;
};

export type ScenarioAi = {
  situation: string;
  reasoning: string;
  recommendation: string;
  impact: string;
  confidence: number;
  decision: DecisionRead | null;
};

type ScenarioMeta = {
  id: ScenarioId;
  label: string;
  tone: { ring: string; glow: string; aura: string };
};

export const SCENARIOS: Record<ScenarioId, ScenarioMeta> = {
  normal: {
    id: "normal",
    label: "Normal",
    tone: { ring: "rgba(163,126,255,0.94)", glow: "rgba(149,114,255,0.34)", aura: "rgba(146,108,255,0.16)" },
  },
  training_burst: {
    id: "training_burst",
    label: "Training Burst",
    tone: { ring: "rgba(255,190,102,0.95)", glow: "rgba(255,180,90,0.36)", aura: "rgba(255,190,110,0.18)" },
  },
  thermal_spike: {
    id: "thermal_spike",
    label: "Thermal Spike",
    tone: { ring: "rgba(255,140,110,0.96)", glow: "rgba(255,120,90,0.4)", aura: "rgba(255,130,100,0.2)" },
  },
  cooling_failure: {
    id: "cooling_failure",
    label: "Cooling Failure",
    tone: { ring: "rgba(255,110,148,0.96)", glow: "rgba(255,110,148,0.42)", aura: "rgba(255,120,155,0.24)" },
  },
  power_surge: {
    id: "power_surge",
    label: "Power Surge",
    tone: { ring: "rgba(120,200,255,0.96)", glow: "rgba(90,180,255,0.4)", aura: "rgba(100,190,255,0.22)" },
  },
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function hashToUnit(input: string): number {
  let h = 2166136261;
  for (let i = 0; i < input.length; i += 1) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 10000) / 10000;
}

/** A stable (id-only, never telemetry-dependent) organic layout position —
 * mirrors ClusterCanvas's own algorithm so both views place the same rack
 * in roughly the same place, but this one is needed explicitly because
 * DigitalTwinWorkspace's draggable nodes require a concrete x/y (there is
 * no such field on the backend — racks have no physical layout concept).
 */
function layoutPosition(id: string, index: number, total: number): { x: number; y: number } {
  const seed = hashToUnit(id);
  const baseAngle = (Math.PI * 2 * index) / Math.max(total, 1);
  const angle = baseAngle + (seed - 0.5) * (Math.PI / Math.max(total, 1)) * 0.7;
  const radius = 30 + (seed - 0.5) * 10;
  return {
    x: clamp(50 + Math.cos(angle) * radius * 1.08, 11, 89),
    y: clamp(52 + Math.sin(angle) * radius * 0.84, 13, 87),
  };
}

function predictionLabel(state: string): RackPrediction {
  if (state === "at_risk") return "Critical Risk";
  if (state === "watch") return "Watch";
  return "Stable";
}

function coolingLabel(coolingEfficiency: number): RackCooling {
  if (coolingEfficiency >= 70) return "Optimal";
  if (coolingEfficiency >= 42) return "Elevated";
  return "Strained";
}

function healthStateFor(status: RackTelemetry["status"]): RackHealthState {
  if (status === "healthy") return "healthy";
  if (status === "warning") return "warning";
  return "critical"; // "critical" and "offline" both read as critical in the UI's 3-tier palette
}

export function nearestForecastPoint(predictions: ForecastPoint[]): ForecastPoint | null {
  if (predictions.length === 0) return null;
  return predictions.reduce((closest, point) => (point.horizon_seconds < closest.horizon_seconds ? point : closest));
}

function toScenarioRack(
  rack: RackTelemetry,
  index: number,
  total: number,
  decisions: DecisionRead[],
  forecast: ForecastPoint[],
): ScenarioRack {
  const activeDecision = decisions.find((decision) => decision.affected_racks.includes(rack.id)) ?? null;
  const position = layoutPosition(rack.id, index, total);
  return {
    id: rack.id,
    name: rack.name,
    x: position.x,
    y: position.y,
    temperature: rack.temperature,
    gpu: Math.round(rack.gpu_utilization),
    jobs: rack.running_jobs,
    prediction: predictionLabel(rack.prediction_state),
    cooling: coolingLabel(rack.cooling_efficiency),
    coolingEfficiency: rack.cooling_efficiency,
    power: `${rack.power_draw.toFixed(1)} kW`,
    powerDraw: rack.power_draw,
    healthScore: rack.health_score,
    healthState: healthStateFor(rack.status),
    recommendation: activeDecision?.recommended_action ?? "No action required.",
    activeDecision,
    forecast,
  };
}

function toMetrics(
  cluster: { overall_health: number; average_temperature: number; total_power: number; energy_savings: number } | null,
  racks: ScenarioRack[],
): ScenarioMetrics {
  if (!cluster) {
    return { clusterHealth: 0, avgTemperature: 0, power: 0, pue: "—", energySaved: 0, avoidedThrottling: 0 };
  }
  return {
    clusterHealth: cluster.overall_health,
    avgTemperature: cluster.average_temperature,
    power: cluster.total_power,
    pue: "—",
    energySaved: cluster.energy_savings,
    // Closest real proxy for "avoided throttling nodes": racks the
    // backend does not currently classify as critical — see the module
    // docstring; not a fabricated counter.
    avoidedThrottling: racks.filter((rack) => rack.healthState !== "critical").length,
  };
}

function impactText(decision: DecisionRead | null): string {
  if (!decision) return "No remediation currently recommended.";
  const parts: string[] = [];
  if (decision.expected_temperature_reduction != null) parts.push(`${decision.expected_temperature_reduction.toFixed(1)}°C reduction`);
  if (decision.expected_power_saving != null) parts.push(`${decision.expected_power_saving.toFixed(1)} kW saved`);
  return parts.length > 0 ? `Expected: ${parts.join(", ")}.` : "Impact not yet quantified.";
}

function toAi(
  decisions: DecisionRead[],
  cluster: { overall_health: number; average_temperature: number } | null,
  scenarioLabel: string,
): ScenarioAi {
  const decision = decisions[0] ?? null;
  if (decision) {
    return {
      situation: decision.title,
      reasoning: decision.reasoning,
      recommendation: decision.recommended_action,
      impact: impactText(decision),
      confidence: decision.confidence,
      decision,
    };
  }
  const situation = cluster
    ? `${scenarioLabel} · cluster averaging ${cluster.average_temperature.toFixed(1)}°C, health ${cluster.overall_health.toFixed(0)}%.`
    : `${scenarioLabel} · awaiting telemetry.`;
  return {
    situation,
    reasoning: "No active decision — telemetry is within normal operating parameters.",
    recommendation: "Maintain current workload distribution.",
    impact: "No remediation currently recommended.",
    confidence: cluster ? cluster.overall_health : 0,
    decision: null,
  };
}

function toTimelineEvent(event: EventRead): TimelineEventItem {
  const time = new Date(event.occurred_at);
  const timestamp = Number.isNaN(time.getTime())
    ? "--:--"
    : time.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return { id: event.id, title: event.title, timestamp, description: event.message ?? event.title };
}

type ScenarioEngineValue = {
  scenario: ScenarioId;
  racks: ScenarioRack[];
  metrics: ScenarioMetrics;
  ai: ScenarioAi;
  timelineEvents: TimelineEventItem[];
  /** Full real ClusterTelemetry — for the rare display that needs a field
   * not already surfaced on `metrics` (see Analytics' efficiency ring). */
  cluster: ClusterTelemetry | null;
  /** Real forward-looking predictions (see GET /api/forecast /
   * app/schemas/forecast.py) — the only genuinely multi-point real series
   * this backend exposes; used in place of any fabricated history. */
  clusterForecast: ForecastPoint[];
  isLoading: boolean;
  isReplaying: boolean;
  /** Whether POST /api/scenario/replay would currently succeed — a fresh
   * cluster (nothing but "normal" has ever run) has no replay history
   * yet. Drives disabling the Replay control instead of letting a user
   * trigger a guaranteed 400 (see backend ScenarioManager.can_replay). */
  canReplay: boolean;
  pulseKey: number;
  resetToken: number;
  scenarioError: string | null;
  selectScenario: (id: ScenarioId) => void;
  triggerReplay: () => void;
  resetScenario: () => void;
  /** Whether the tick loop itself is running — see
   * app.simulation.state.SimulationStatus. Null only until the first
   * WebSocket message arrives. Distinct from `scenario`/`resetScenario`
   * above, which are about *what* a running simulation is doing, not
   * *whether* it's running at all. */
  simulationStatus: SimulationStatusRead | null;
  isSimulationBusy: boolean;
  startSimulation: () => void;
  pauseSimulation: () => void;
  resumeSimulation: () => void;
  resetSimulation: () => void;
};

const ScenarioEngineContext = createContext<ScenarioEngineValue | null>(null);

export function ScenarioEngineProvider({ children }: { children: ReactNode }) {
  const { snapshot, events, simulationStatus } = useTelemetry();
  const { simulationMode, predictionIntervalMs } = useSettings();
  const [pulseKey, setPulseKey] = useState(0);
  const [resetToken, setResetToken] = useState(0);
  const [pendingRequest, setPendingRequest] = useState(false);
  const [simulationBusy, setSimulationBusy] = useState(false);
  const [scenarioError, setScenarioError] = useState<string | null>(null);
  const requestGuard = useRef(0);

  const scenario = (snapshot?.scenario.key as ScenarioId | undefined) ?? "normal";
  const canReplay = snapshot?.scenario.can_replay ?? false;

  const racks = useMemo<ScenarioRack[]>(() => {
    if (!snapshot) return [];
    const forecastByRack = new Map(snapshot.rack_forecasts.map((entry) => [entry.rack_id, entry.predictions]));
    return snapshot.racks.map((rack, index) =>
      toScenarioRack(rack, index, snapshot.racks.length, snapshot.decisions, forecastByRack.get(rack.id) ?? []),
    );
  }, [snapshot]);

  const metrics = useMemo(() => toMetrics(snapshot?.cluster ?? null, racks), [snapshot, racks]);
  const ai = useMemo(
    () => toAi(snapshot?.decisions ?? [], snapshot?.cluster ?? null, SCENARIOS[scenario]?.label ?? scenario),
    [snapshot, scenario],
  );

  // Real backend Event rows only (see TelemetryContext) — an empty array
  // while IDLE/freshly connected is the honest state, not a fabricated
  // placeholder entry. Timeline.tsx renders its own empty state rather
  // than this engine inventing a fake "event" just to have something to
  // show (that previously made "0 events" render as "1 event logged").
  const timelineEvents = useMemo<TimelineEventItem[]>(() => events.slice(-12).map(toTimelineEvent), [events]);

  // A pulse (used by MissionControlPage/DigitalTwinWorkspace to refocus the
  // most-affected rack) whenever the active scenario key actually changes.
  const previousScenario = useRef(scenario);
  useEffect(() => {
    if (previousScenario.current !== scenario) {
      previousScenario.current = scenario;
      setPulseKey((count) => count + 1);
    }
  }, [scenario]);

  const isReplaying = pendingRequest || snapshot?.scenario.transition_state === "transitioning";

  const selectScenario = useCallback((id: ScenarioId) => {
    const token = ++requestGuard.current;
    setScenarioError(null);
    setPendingRequest(true);
    apiClient
      .activateScenario(id)
      .catch((error: unknown) => {
        if (requestGuard.current !== token) return;
        setScenarioError(error instanceof Error ? error.message : "Failed to change scenario.");
      })
      .finally(() => {
        if (requestGuard.current !== token) return;
        setPendingRequest(false);
      });
  }, []);

  const resetScenario = useCallback(() => {
    const token = ++requestGuard.current;
    setScenarioError(null);
    setPendingRequest(true);
    apiClient
      .resetScenario()
      .then(() => setResetToken((count) => count + 1))
      .catch((error: unknown) => {
        if (requestGuard.current !== token) return;
        setScenarioError(error instanceof Error ? error.message : "Failed to reset the cluster.");
      })
      .finally(() => {
        if (requestGuard.current !== token) return;
        setPendingRequest(false);
      });
  }, []);

  const triggerReplay = useCallback(() => {
    // Defense in depth: the Replay control is already disabled whenever
    // !canReplay (see SimulationDock/App.tsx), so this only fires for a
    // caller that ignored that — a controlled, request-free message
    // instead of a guaranteed 400 round-trip. Never auto-invoked on
    // mount; this is only ever reached from an explicit user click (see
    // callers).
    if (!canReplay) {
      setScenarioError("No previous scenario to replay yet.");
      return;
    }
    const token = ++requestGuard.current;
    setScenarioError(null);
    setPendingRequest(true);
    apiClient
      .replayScenario()
      .catch((error: unknown) => {
        if (requestGuard.current !== token) return;
        setScenarioError(error instanceof Error ? error.message : "Failed to replay the scenario.");
      })
      .finally(() => {
        if (requestGuard.current !== token) return;
        setPendingRequest(false);
      });
  }, [canReplay]);

  // Autonomous mode: periodically triggers a *real* scenario change on the
  // backend (never fakes one locally) — the cadence reuses the same
  // Settings-driven interval the app already exposed for this purpose.
  // Gated on the simulation actually running — while idle/paused a
  // scenario change is rejected by the backend (see
  // SimulationService._require_running_for_scenario), so cycling here
  // while stopped would just spam 400s for no visible effect.
  const isRunning = simulationStatus?.status === "running";
  useEffect(() => {
    if (simulationMode !== "autonomous" || !isRunning) return;
    const cycleMs = Math.max(predictionIntervalMs * 6, 14000);
    const id = window.setInterval(() => {
      if (pendingRequest) return;
      const options = (Object.keys(SCENARIOS) as ScenarioId[]).filter((candidate) => candidate !== scenario);
      const next = options[Math.floor(Math.random() * options.length)];
      selectScenario(next);
    }, cycleMs);
    return () => window.clearInterval(id);
  }, [simulationMode, isRunning, predictionIntervalMs, scenario, pendingRequest, selectScenario]);

  // --- simulation lifecycle actions (start/pause/resume/reset the tick
  // loop itself) — same "real API, no local faking" pattern as the
  // scenario actions above, but never conflated with resetScenario (which
  // only resets *which* scenario is active, and works regardless of
  // whether the simulation is running).
  const runLifecycleAction = useCallback((action: () => Promise<SimulationStatusRead>, failureMessage: string) => {
    setScenarioError(null);
    setSimulationBusy(true);
    action()
      .catch((error: unknown) => {
        setScenarioError(error instanceof Error ? error.message : failureMessage);
      })
      .finally(() => {
        setSimulationBusy(false);
      });
  }, []);

  const startSimulation = useCallback(
    () => runLifecycleAction(apiClient.startSimulation, "Failed to start the simulation."),
    [runLifecycleAction],
  );
  const pauseSimulation = useCallback(
    () => runLifecycleAction(apiClient.pauseSimulation, "Failed to pause the simulation."),
    [runLifecycleAction],
  );
  const resumeSimulation = useCallback(
    () => runLifecycleAction(apiClient.resumeSimulation, "Failed to resume the simulation."),
    [runLifecycleAction],
  );
  const resetSimulation = useCallback(
    () => runLifecycleAction(apiClient.resetSimulation, "Failed to reset the simulation."),
    [runLifecycleAction],
  );

  const value = useMemo<ScenarioEngineValue>(
    () => ({
      scenario,
      racks,
      metrics,
      ai,
      timelineEvents,
      cluster: snapshot?.cluster ?? null,
      clusterForecast: snapshot?.forecast.predictions ?? [],
      isLoading: snapshot == null,
      isReplaying,
      canReplay,
      pulseKey,
      resetToken,
      scenarioError,
      selectScenario,
      triggerReplay,
      resetScenario,
      simulationStatus,
      isSimulationBusy: simulationBusy,
      startSimulation,
      pauseSimulation,
      resumeSimulation,
      resetSimulation,
    }),
    [
      scenario, racks, metrics, ai, timelineEvents, snapshot, isReplaying, canReplay, pulseKey, resetToken, scenarioError,
      selectScenario, triggerReplay, resetScenario, simulationStatus, simulationBusy, startSimulation,
      pauseSimulation, resumeSimulation, resetSimulation,
    ],
  );

  return <ScenarioEngineContext.Provider value={value}>{children}</ScenarioEngineContext.Provider>;
}

export function useScenarioEngine(): ScenarioEngineValue {
  const ctx = useContext(ScenarioEngineContext);
  if (!ctx) throw new Error("useScenarioEngine must be used within a ScenarioEngineProvider");
  return ctx;
}

export function mostAffectedRackId(racks: ScenarioRack[]): string {
  if (racks.length === 0) return "";
  return racks.reduce((hottest, rack) => (rack.temperature > hottest.temperature ? rack : hottest), racks[0]).id;
}
