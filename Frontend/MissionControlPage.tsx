import { useEffect, useMemo, useState } from "react";
import AIPanel from "./AIPanel";
import ClusterCanvas, { type ClusterRack } from "./ClusterCanvas";
import LoadingState from "./components/LoadingState";
import { executeButtonProps, handleExecuteClick, useExecuteRecommendation } from "./lib/useExecuteRecommendation";
import MetricsRibbon from "./MetricsRibbon";
import { SCENARIOS, mostAffectedRackId, useScenarioEngine, type ScenarioRack } from "./scenario/ScenarioEngine";
import Timeline from "./Timeline";

function toClusterRacks(racks: ScenarioRack[]): ClusterRack[] {
  // ClusterCanvas derives nearest-neighbor links itself when `connections`
  // is omitted (see ClusterCanvas.tsx) — real racks have no backend notion
  // of physical adjacency, so that fallback is used rather than a
  // hardcoded id table that would no longer match real rack ids.
  return racks.map((rack) => ({
    id: rack.id,
    rackName: rack.name,
    temperature: rack.temperature,
    health: rack.healthScore,
    healthScore: rack.healthScore,
    healthState: rack.healthState,
    gpuLoad: rack.gpu,
    prediction: rack.prediction,
    predictionIndicator: rack.prediction,
  }));
}

// Backend-state-accurate labels — never a generic "LIVE"/"Connected" that
// doesn't distinguish "WebSocket connected" from "simulation ticking" (see
// the objective: connecting to /ws/telemetry must never read as "running").
const STATUS_COPY: Record<string, { label: string; detail: string; dot: string }> = {
  idle: {
    label: "SIMULATION READY",
    detail: "The datacenter is stable. Press Start Simulation to bring telemetry live.",
    dot: "bg-white/40",
  },
  paused: {
    label: "SIMULATION PAUSED",
    detail: "Telemetry is frozen at its current state. Press Resume to continue.",
    dot: "bg-amber-300/80",
  },
  running: {
    label: "SIMULATION LIVE",
    detail: "Telemetry is ticking from the live backend digital twin.",
    dot: "bg-emerald-300/90",
  },
  completed: {
    label: "SIMULATION READY",
    detail: "The datacenter is stable. Press Start Simulation to bring telemetry live.",
    dot: "bg-white/40",
  },
  error: {
    label: "SIMULATION READY",
    detail: "The datacenter is stable. Press Start Simulation to bring telemetry live.",
    dot: "bg-white/40",
  },
};

export default function MissionControlPage() {
  const {
    racks: engineRacks, scenario, ai, metrics, timelineEvents, pulseKey, isLoading, scenarioError, simulationStatus,
  } = useScenarioEngine();
  const [focusedRackId, setFocusedRackId] = useState<string | null>(null);
  const executionFlow = useExecuteRecommendation();

  const racks = useMemo(() => toClusterRacks(engineRacks), [engineRacks]);

  useEffect(() => {
    if (engineRacks.length > 0 && focusedRackId == null) setFocusedRackId(engineRacks[0].id);
  }, [engineRacks, focusedRackId]);

  useEffect(() => {
    if (!pulseKey) return;
    if (scenario !== "thermal_spike" && scenario !== "cooling_failure") return;
    setFocusedRackId(mostAffectedRackId(engineRacks));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pulseKey]);

  useEffect(() => {
    executionFlow.reset();
    // Ask NeuroCore fresh each time the recommended decision changes —
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ai.decision?.id]);

  const focusedRack = useMemo(
    () => racks.find((rack) => rack.id === focusedRackId) ?? racks[0],
    [focusedRackId, racks],
  );

  if (isLoading || !focusedRack) {
    return <LoadingState />;
  }

  const executeProps = executeButtonProps(executionFlow.state);

  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-36 pt-3 sm:px-5 lg:px-8">
      <div className="relative w-full overflow-hidden rounded-[2.2rem] bg-[linear-gradient(170deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012))] p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07),0_28px_90px_rgba(0,0,0,0.58)] backdrop-blur-[16px] sm:p-6 lg:p-7">
        <div className="relative flex h-full w-full flex-col gap-3">
          {scenarioError ? (
            <div className="rounded-xl border border-rose-300/25 bg-rose-300/[0.08] px-3 py-2 text-[0.68rem] text-rose-100/85">
              {scenarioError}
            </div>
          ) : null}

          {simulationStatus ? (
            <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-[0.62rem] uppercase tracking-[0.14em] text-white/56">
              <span className="relative flex h-1.5 w-1.5">
                {simulationStatus.status === "running" ? (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-300/60" />
                ) : null}
                <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${(STATUS_COPY[simulationStatus.status] ?? STATUS_COPY.idle).dot}`} />
              </span>
              {(STATUS_COPY[simulationStatus.status] ?? STATUS_COPY.idle).label}
              <span className="normal-case tracking-normal text-white/38">
                — {(STATUS_COPY[simulationStatus.status] ?? STATUS_COPY.idle).detail}
              </span>
            </div>
          ) : null}

          <MetricsRibbon
            clusterHealth={`${metrics.clusterHealth.toFixed(1)}%`}
            averageTemperature={`${metrics.avgTemperature.toFixed(1)}°C`}
            power={`${metrics.power.toFixed(2)} kW`}
            pue={metrics.pue}
            energySaved={`${metrics.energySaved.toFixed(1)}%`}
            avoidedThrottling={`${metrics.avoidedThrottling} Nodes`}
          />

          <div className="grid h-[max(18rem,calc(100dvh_-_36rem))] gap-5 xl:grid-cols-[1.95fr_1fr]">
            <ClusterCanvas
              racks={racks}
              focusedRackId={focusedRackId ?? undefined}
              onRackFocus={(rack) => setFocusedRackId(rack.id)}
              className="h-full"
              tintColor={SCENARIOS[scenario]?.tone.glow}
            />

            <AIPanel
              currentSituation={`${focusedRack.rackName} at ${focusedRack.temperature.toFixed(1)}°C · GPU ${focusedRack.gpuLoad}%`}
              reasoning={ai.reasoning}
              recommendation={ai.recommendation}
              expectedImpact={ai.impact}
              executeLabel={executeProps.label}
              executeDisabled={executeProps.disabled || !ai.decision}
              isExecuting={executeProps.executing}
              onExecute={() => handleExecuteClick(executionFlow.state, ai.decision, executionFlow.propose, executionFlow.confirm)}
            />
          </div>

          <Timeline events={timelineEvents} activeEventId={timelineEvents[timelineEvents.length - 1]?.id} className="mt-3" />
        </div>
      </div>
    </div>
  );
}
