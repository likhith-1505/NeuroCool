import { useEffect, useMemo, useState } from "react";
import AIPanel from "./AIPanel";
import ClusterCanvas, { type ClusterRack } from "./ClusterCanvas";
import MetricsRibbon from "./MetricsRibbon";
import Timeline from "./Timeline";
import type { SimulationAction } from "./components/SimulationDock";

type MissionControlPageProps = {
  simulationPulse: number;
  lastActionLabel: string;
};

const initialRacks: ClusterRack[] = [
  {
    id: "rack-a1",
    rackName: "Rack A1",
    temperature: 68.4,
    health: 91,
    healthScore: 91,
    gpuLoad: 64,
    prediction: "Stable",
    predictionIndicator: "Stable",
    connections: ["rack-b2", "rack-c1"],
  },
  {
    id: "rack-b2",
    rackName: "Rack B2",
    temperature: 74.8,
    health: 78,
    healthScore: 78,
    gpuLoad: 71,
    prediction: "Watch",
    predictionIndicator: "Watch",
    connections: ["rack-a1", "rack-d4"],
  },
  {
    id: "rack-c1",
    rackName: "Rack C1",
    temperature: 81.2,
    health: 58,
    healthScore: 58,
    gpuLoad: 86,
    prediction: "Risk Rising",
    predictionIndicator: "Risk Rising",
    connections: ["rack-a1", "rack-d4"],
  },
  {
    id: "rack-d4",
    rackName: "Rack D4",
    temperature: 70.1,
    health: 87,
    healthScore: 87,
    gpuLoad: 59,
    prediction: "Stable",
    predictionIndicator: "Stable",
    connections: ["rack-b2", "rack-c1"],
  },
];

function applySimulationAction(racks: ClusterRack[], action: SimulationAction | "none"): ClusterRack[] {
  if (action === "reset" || action === "none") return initialRacks;

  return racks.map((rack, index) => {
    const heatDelta =
      action === "thermal-spike" ? (index % 2 === 0 ? 4.6 : 2.8)
        : action === "cooling-failure" ? (index === 2 ? 6.8 : 3.2)
          : action === "power-surge" ? 2.2
            : action === "training-job" ? 3.6
              : action === "inference-spike" ? 2.8
                : action === "run-ai" ? -2.4
                  : action === "replay" ? 1.1
                    : 0;

    const loadDelta =
      action === "training-job" ? 10
        : action === "inference-spike" ? 14
          : action === "power-surge" ? 8
            : action === "run-ai" ? -9
              : action === "cooling-failure" ? 6
                : action === "replay" ? 4
                  : action === "thermal-spike" ? 7
                    : 0;

    const nextTemperature = Math.min(94, Math.max(48, rack.temperature + heatDelta));
    const nextGpuLoad = Math.min(100, Math.max(20, rack.gpuLoad + loadDelta));
    const nextHealth = Math.max(32, Math.min(98, rack.health - heatDelta * 1.3 - loadDelta * 0.3 + (action === "run-ai" ? 7 : 0)));

    const nextPrediction =
      nextHealth < 46 ? "Critical Risk" : nextHealth < 70 ? "Watch" : "Stable";

    return {
      ...rack,
      temperature: Number(nextTemperature.toFixed(1)),
      gpuLoad: Number(nextGpuLoad.toFixed(0)),
      health: Number(nextHealth.toFixed(0)),
      healthScore: Number(nextHealth.toFixed(0)),
      prediction: nextPrediction,
      predictionIndicator: nextPrediction,
    };
  });
}

export default function MissionControlPage({ simulationPulse, lastActionLabel }: MissionControlPageProps) {
  const [racks, setRacks] = useState<ClusterRack[]>(initialRacks);
  const [focusedRackId, setFocusedRackId] = useState(initialRacks[0].id);
  const [timelineEvents, setTimelineEvents] = useState([
    {
      id: "boot",
      title: "Mission Control Live",
      timestamp: "Now",
      description: "NeuroCool telemetry synchronized across all zones.",
    },
  ]);

  const focusedRack = useMemo(
    () => racks.find((rack) => rack.id === focusedRackId) ?? racks[0],
    [focusedRackId, racks],
  );

  const aggregate = useMemo(() => {
    const avgTemp = racks.reduce((total, rack) => total + rack.temperature, 0) / racks.length;
    const avgHealth = racks.reduce((total, rack) => total + rack.healthScore, 0) / racks.length;
    const avgLoad = racks.reduce((total, rack) => total + rack.gpuLoad, 0) / racks.length;
    const power = 4.2 + avgLoad / 56;
    const pue = 1.08 + avgTemp / 880;
    const energySaved = Math.max(8, 26 - avgLoad / 8);
    const avoidedThrottling = Math.max(4, Math.round((avgHealth - 40) / 1.6));

    return {
      avgTemp,
      avgHealth,
      power,
      pue,
      energySaved,
      avoidedThrottling,
    };
  }, [racks]);

  useEffect(() => {
    if (!simulationPulse || !lastActionLabel) return;

    const actionMap: Record<string, SimulationAction | "none"> = {
      "Training Job": "training-job",
      "Inference Spike": "inference-spike",
      "Thermal Spike": "thermal-spike",
      "Cooling Failure": "cooling-failure",
      "Power Surge": "power-surge",
      "Run AI": "run-ai",
      Replay: "replay",
      Reset: "reset",
    };

    const mappedAction = actionMap[lastActionLabel] ?? "none";
    setRacks((current) => applySimulationAction(current, mappedAction));

    setTimelineEvents((events) => {
      const next = [
        ...events,
        {
          id: `${Date.now()}-${lastActionLabel}`,
          title: lastActionLabel,
          timestamp: "Now",
          description: `${lastActionLabel} executed in mission simulation loop.`,
        },
      ];
      return next.slice(-6);
    });
  }, [lastActionLabel, simulationPulse]);

  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-28 pt-3 sm:px-5 lg:px-8">
      <div className="relative w-full overflow-hidden rounded-[2.2rem] bg-[linear-gradient(170deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012))] p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07),0_28px_90px_rgba(0,0,0,0.58)] backdrop-blur-[16px] sm:p-6 lg:p-7">
        <div className="relative flex h-full w-full flex-col gap-5">
          <MetricsRibbon
            clusterHealth={`${aggregate.avgHealth.toFixed(1)}%`}
            averageTemperature={`${aggregate.avgTemp.toFixed(1)}°C`}
            power={`${aggregate.power.toFixed(2)} MW`}
            pue={aggregate.pue.toFixed(2)}
            energySaved={`${aggregate.energySaved.toFixed(1)}%`}
            avoidedThrottling={`${aggregate.avoidedThrottling} Nodes`}
          />

          <div className="grid min-h-[66vh] flex-1 gap-5 xl:grid-cols-[1.95fr_1fr]">
            <ClusterCanvas
              racks={racks}
              focusedRackId={focusedRackId}
              onRackFocus={(rack) => setFocusedRackId(rack.id)}
              className="h-full min-h-[44rem]"
            />

            <AIPanel
              currentSituation={`${focusedRack.rackName} at ${focusedRack.temperature.toFixed(1)}°C · GPU ${focusedRack.gpuLoad}%`}
              reasoning={`Confidence ${focusedRack.healthScore}/100. Thermal drift indicates ${focusedRack.predictionIndicator.toLowerCase()} trend.`}
              recommendation="Shift high-entropy jobs from focused rack to adjacent healthy nodes and rebalance cooling load."
              expectedImpact="Projected hotspot drop within 2 cycles and improved power efficiency across the active corridor."
              executeLabel="Execute Rebalance"
            />
          </div>

          <Timeline events={timelineEvents} activeEventId={timelineEvents[timelineEvents.length - 1]?.id} />
        </div>
      </div>
    </div>
  );
}
