import { useEffect, useMemo, useState } from "react";
import AIPanel from "./AIPanel";
import ClusterCanvas, { type ClusterRack } from "./ClusterCanvas";
import MetricsRibbon from "./MetricsRibbon";
import Timeline from "./Timeline";
import { SCENARIOS, mostAffectedRackId, useScenarioEngine, type ScenarioRack } from "./scenario/ScenarioEngine";

const CONNECTIONS: Record<string, string[]> = {
  r1: ["r2", "r4"],
  r2: ["r1", "r3", "r4"],
  r3: ["r2", "r4"],
  r4: ["r1", "r2", "r3"],
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function healthScoreFor(rack: ScenarioRack): number {
  return Math.round(clamp(100 - (rack.temperature - 55) * 1.6 - (rack.gpu - 45) * 0.35, 20, 99));
}

function toClusterRacks(racks: ScenarioRack[]): ClusterRack[] {
  return racks.map((rack) => {
    const health = healthScoreFor(rack);
    return {
      id: rack.id,
      rackName: rack.name,
      temperature: rack.temperature,
      health,
      healthScore: health,
      gpuLoad: rack.gpu,
      prediction: rack.prediction,
      predictionIndicator: rack.prediction,
      connections: CONNECTIONS[rack.id],
    };
  });
}

export default function MissionControlPage() {
  const { racks: engineRacks, scenario, ai, metrics, timelineEvents, pulseKey } = useScenarioEngine();
  const [focusedRackId, setFocusedRackId] = useState(engineRacks[0].id);

  const racks = useMemo(() => toClusterRacks(engineRacks), [engineRacks]);

  useEffect(() => {
    if (!pulseKey) return;
    if (scenario !== "thermal-spike" && scenario !== "cooling-failure") return;
    setFocusedRackId(mostAffectedRackId(engineRacks));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pulseKey]);

  const focusedRack = useMemo(
    () => racks.find((rack) => rack.id === focusedRackId) ?? racks[0],
    [focusedRackId, racks],
  );

  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-36 pt-3 sm:px-5 lg:px-8">
      <div className="relative w-full overflow-hidden rounded-[2.2rem] bg-[linear-gradient(170deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012))] p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07),0_28px_90px_rgba(0,0,0,0.58)] backdrop-blur-[16px] sm:p-6 lg:p-7">
        <div className="relative flex h-full w-full flex-col gap-3">
          <MetricsRibbon
            clusterHealth={`${metrics.clusterHealth.toFixed(1)}%`}
            averageTemperature={`${metrics.avgTemperature.toFixed(1)}°C`}
            power={`${metrics.power.toFixed(2)} MW`}
            pue={metrics.pue.toFixed(2)}
            energySaved={`${metrics.energySaved.toFixed(1)}%`}
            avoidedThrottling={`${metrics.avoidedThrottling} Nodes`}
          />

          <div className="grid h-[max(18rem,calc(100dvh_-_36rem))] gap-5 xl:grid-cols-[1.95fr_1fr]">
            <ClusterCanvas
              racks={racks}
              focusedRackId={focusedRackId}
              onRackFocus={(rack) => setFocusedRackId(rack.id)}
              className="h-full"
              tintColor={SCENARIOS[scenario].tone.glow}
            />

            <AIPanel
              currentSituation={`${focusedRack.rackName} at ${focusedRack.temperature.toFixed(1)}°C · GPU ${focusedRack.gpuLoad}%`}
              reasoning={ai.reasoning}
              recommendation={ai.recommendation}
              expectedImpact={ai.impact}
              executeLabel="Execute Recommendation"
            />
          </div>

          <Timeline events={timelineEvents} activeEventId={timelineEvents[timelineEvents.length - 1]?.id} className="mt-3" />
        </div>
      </div>
    </div>
  );
}
