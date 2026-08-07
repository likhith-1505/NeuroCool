import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSettings } from "../settings/SettingsContext";

export type ScenarioId = "normal" | "training-burst" | "thermal-spike" | "cooling-failure";

export type ScenarioRack = {
  id: string;
  name: string;
  x: number;
  y: number;
  temperature: number;
  gpu: number;
  jobs: number;
  prediction: "Stable" | "Watch" | "Critical Risk";
  cooling: "Optimal" | "Elevated" | "Strained";
  power: string;
  recommendation: string;
};

export type ScenarioMetrics = {
  clusterHealth: number;
  avgTemperature: number;
  power: number;
  pue: number;
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
};

type ScenarioMeta = {
  id: ScenarioId;
  label: string;
  narrative: string;
  tone: { ring: string; glow: string; aura: string };
  ai: ScenarioAi;
};

const BASE_RACKS: ScenarioRack[] = [
  {
    id: "r1",
    name: "Rack A1",
    x: 22,
    y: 36,
    temperature: 62.4,
    gpu: 46,
    jobs: 16,
    prediction: "Stable",
    cooling: "Optimal",
    power: "0.86 MW",
    recommendation: "Maintain workload distribution.",
  },
  {
    id: "r2",
    name: "Rack B2",
    x: 46,
    y: 24,
    temperature: 64.8,
    gpu: 51,
    jobs: 19,
    prediction: "Stable",
    cooling: "Optimal",
    power: "0.91 MW",
    recommendation: "Maintain workload distribution.",
  },
  {
    id: "r3",
    name: "Rack C1",
    x: 67,
    y: 42,
    temperature: 66.2,
    gpu: 54,
    jobs: 21,
    prediction: "Stable",
    cooling: "Optimal",
    power: "0.95 MW",
    recommendation: "Maintain workload distribution.",
  },
  {
    id: "r4",
    name: "Rack D4",
    x: 38,
    y: 62,
    temperature: 60.5,
    gpu: 42,
    jobs: 14,
    prediction: "Stable",
    cooling: "Optimal",
    power: "0.83 MW",
    recommendation: "Maintain workload distribution.",
  },
];

export const SCENARIOS: Record<ScenarioId, ScenarioMeta> = {
  normal: {
    id: "normal",
    label: "Normal",
    narrative: "All racks nominal · cluster running within safe thermal envelope.",
    tone: { ring: "rgba(163,126,255,0.94)", glow: "rgba(149,114,255,0.34)", aura: "rgba(146,108,255,0.16)" },
    ai: {
      situation: "All racks nominal · cluster running within safe thermal envelope.",
      reasoning: "Confidence 96/100. No anomalies detected across active zones.",
      recommendation: "Maintain workload distribution.",
      impact: "Sustained efficiency with headroom for additional burst capacity.",
      confidence: 96,
    },
  },
  "training-burst": {
    id: "training-burst",
    label: "Training Burst",
    narrative: "GPU utilization climbing cluster-wide · distributed training job active.",
    tone: { ring: "rgba(255,190,102,0.95)", glow: "rgba(255,180,90,0.36)", aura: "rgba(255,190,110,0.18)" },
    ai: {
      situation: "GPU utilization climbing cluster-wide · distributed training job active.",
      reasoning: "Confidence 88/100. Thermal load rising in line with compute demand.",
      recommendation: "Prepare proactive redistribution.",
      impact: "Preemptive rebalancing keeps thermal headroom above safe threshold.",
      confidence: 88,
    },
  },
  "thermal-spike": {
    id: "thermal-spike",
    label: "Thermal Spike",
    narrative: "Rack C1 thermal excursion detected · rapid temperature rise.",
    tone: { ring: "rgba(255,140,110,0.96)", glow: "rgba(255,120,90,0.4)", aura: "rgba(255,130,100,0.2)" },
    ai: {
      situation: "Rack C1 thermal excursion detected · rapid temperature rise.",
      reasoning: "Confidence 92/100. Prediction model flags Rack C1 approaching critical threshold.",
      recommendation: "Migrate Job #412 to Rack D4.",
      impact: "Migration resolves hotspot within 2 cycles, restoring nominal thermal range.",
      confidence: 92,
    },
  },
  "cooling-failure": {
    id: "cooling-failure",
    label: "Cooling Failure",
    narrative: "Cooling efficiency degraded across zone · heat spreading to adjacent racks.",
    tone: { ring: "rgba(255,110,148,0.96)", glow: "rgba(255,110,148,0.42)", aura: "rgba(255,120,155,0.24)" },
    ai: {
      situation: "Cooling efficiency degraded across zone · heat spreading to adjacent racks.",
      reasoning: "Confidence 97/100. Cooling subsystem fault confirmed, thermal spread accelerating.",
      recommendation: "Increase cooling and rebalance workloads immediately.",
      impact: "Emergency cooling boost prevents cascading throttling across the cluster.",
      confidence: 97,
    },
  },
};

type ReplayStep = { scenario: ScenarioId; title: string; description: string };

export const REPLAY_STEPS: ReplayStep[] = [
  { scenario: "training-burst", title: "Training Started", description: "Distributed training job dispatched across the cluster." },
  { scenario: "training-burst", title: "GPU Utilization Rising", description: "Compute load climbing across active racks." },
  { scenario: "thermal-spike", title: "Thermal Prediction", description: "Forecast model flags Rack C1 approaching critical threshold." },
  { scenario: "thermal-spike", title: "AI Recommendation", description: "Migrate Job #412 to Rack D4." },
  { scenario: "normal", title: "Migration", description: "High-entropy jobs redistributed to healthy nodes." },
  { scenario: "normal", title: "Cluster Stabilized", description: "All racks nominal · cluster running within safe thermal envelope." },
];

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function classify(temperature: number, gpu: number): { prediction: ScenarioRack["prediction"]; cooling: ScenarioRack["cooling"] } {
  if (temperature >= 85 || gpu >= 94) return { prediction: "Critical Risk", cooling: "Strained" };
  if (temperature >= 74 || gpu >= 80) return { prediction: "Watch", cooling: "Elevated" };
  return { prediction: "Stable", cooling: "Optimal" };
}

/**
 * Nudges the already-computed racks by a small, smoothly varying amount so telemetry
 * never sits perfectly still between scenario changes — the same "live" feeling as a
 * real monitoring feed. Bounded tightly enough that it reads as noise, not a new event,
 * and re-classifies prediction/cooling so nothing goes stale relative to the new reading.
 */
function applyTelemetryJitter(racks: ScenarioRack[], tick: number): ScenarioRack[] {
  if (tick === 0) return racks;
  return racks.map((rack, index) => {
    const phase = index * 13.37 + tick * 0.6;
    const tempWiggle = Math.sin(phase) * 0.35 + Math.sin(phase * 1.7) * 0.15;
    const gpuWiggle = Math.cos(phase * 0.85) * 1.1;
    const temperature = Number(clamp(rack.temperature + tempWiggle, 40, 99).toFixed(1));
    const gpu = clamp(Math.round(rack.gpu + gpuWiggle), 15, 100);
    const { prediction, cooling } = classify(temperature, gpu);
    return { ...rack, temperature, gpu, prediction, cooling };
  });
}

function applyConfidenceJitter(ai: ScenarioAi, tick: number): ScenarioAi {
  if (tick === 0) return ai;
  const wiggle = Math.round(Math.sin(tick * 0.5) * 1.6);
  return { ...ai, confidence: clamp(ai.confidence + wiggle, 1, 100) };
}

export function computeScenarioRacks(scenario: ScenarioId): ScenarioRack[] {
  return BASE_RACKS.map((rack, index) => {
    let temperature = rack.temperature;
    let gpu = rack.gpu;
    let jobs = rack.jobs;
    let recommendation = "Maintain workload distribution.";

    if (scenario === "training-burst") {
      temperature += 9 + index * 1.4;
      gpu += 26 + index * 2;
      jobs += 9;
      recommendation = "Prepare proactive redistribution.";
    } else if (scenario === "thermal-spike") {
      if (rack.id === "r3") {
        temperature += 22;
        gpu += 14;
        jobs += 6;
        recommendation = "Migrate Job #412 to Rack D4.";
      } else {
        temperature += 2;
        gpu += 3;
      }
    } else if (scenario === "cooling-failure") {
      temperature += 15 + index * 2.6;
      gpu += 6 + index;
      recommendation = "Increase cooling and rebalance workloads immediately.";
    }

    temperature = clamp(Number(temperature.toFixed(1)), 45, 96);
    gpu = clamp(Math.round(gpu), 20, 100);
    const { prediction, cooling } = classify(temperature, gpu);

    return { ...rack, temperature, gpu, jobs, prediction, cooling, recommendation };
  });
}

function computeMetrics(racks: ScenarioRack[]): ScenarioMetrics {
  const avgTemp = racks.reduce((sum, rack) => sum + rack.temperature, 0) / racks.length;
  const avgGpu = racks.reduce((sum, rack) => sum + rack.gpu, 0) / racks.length;
  const clusterHealth = clamp(100 - (avgTemp - 55) * 1.6 - (avgGpu - 45) * 0.35, 20, 99);
  const power = 0.78 + avgGpu / 110;
  const pue = 1.06 + avgTemp / 780;
  const energySaved = clamp(28 - avgGpu / 7, 6, 30);
  const avoidedThrottling = Math.max(3, Math.round((clusterHealth - 35) / 1.4));

  return {
    clusterHealth: Number(clusterHealth.toFixed(1)),
    avgTemperature: Number(avgTemp.toFixed(1)),
    power: Number(power.toFixed(2)),
    pue: Number(pue.toFixed(2)),
    energySaved: Number(energySaved.toFixed(1)),
    avoidedThrottling,
  };
}

export function mostAffectedRackId(racks: ScenarioRack[]): string {
  return racks.reduce((hottest, rack) => (rack.temperature > hottest.temperature ? rack : hottest), racks[0]).id;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

let eventSeq = 0;

function eventFor(title: string, description: string): TimelineEventItem {
  eventSeq += 1;
  return { id: `evt-${eventSeq}`, title, timestamp: "Now", description };
}

type ScenarioEngineValue = {
  scenario: ScenarioId;
  racks: ScenarioRack[];
  metrics: ScenarioMetrics;
  ai: ScenarioAi;
  timelineEvents: TimelineEventItem[];
  isReplaying: boolean;
  pulseKey: number;
  resetToken: number;
  selectScenario: (id: ScenarioId) => void;
  triggerReplay: () => void;
  resetScenario: () => void;
};

const ScenarioEngineContext = createContext<ScenarioEngineValue | null>(null);

export function ScenarioEngineProvider({ children }: { children: ReactNode }) {
  const { predictionIntervalMs, simulationMode } = useSettings();
  const [scenario, setScenario] = useState<ScenarioId>("normal");
  const [timelineEvents, setTimelineEvents] = useState<TimelineEventItem[]>([
    eventFor("Mission Control Live", "NeuroCool telemetry synchronized across all zones."),
  ]);
  const [isReplaying, setIsReplaying] = useState(false);
  const [pulseKey, setPulseKey] = useState(0);
  const [resetToken, setResetToken] = useState(0);
  const [jitterTick, setJitterTick] = useState(0);
  const replayGuard = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Live telemetry: a gentle, continuous tick that keeps temperatures, GPU load and
  // confidence drifting slightly even when nothing else has changed.
  useEffect(() => {
    const id = window.setInterval(() => setJitterTick((tick) => tick + 1), predictionIntervalMs);
    return () => window.clearInterval(id);
  }, [predictionIntervalMs]);

  const baseRacks = useMemo(() => computeScenarioRacks(scenario), [scenario]);
  const racks = useMemo(() => applyTelemetryJitter(baseRacks, jitterTick), [baseRacks, jitterTick]);
  const metrics = useMemo(() => computeMetrics(racks), [racks]);
  const ai = useMemo(() => applyConfidenceJitter(SCENARIOS[scenario].ai, jitterTick), [scenario, jitterTick]);

  const selectScenario = useCallback((id: ScenarioId) => {
    if (replayGuard.current !== 0) return;
    setScenario(id);
    setPulseKey((count) => count + 1);
    setTimelineEvents((events) => [...events, eventFor(SCENARIOS[id].label, SCENARIOS[id].narrative)].slice(-6));
  }, []);

  // Autonomous mode: the cluster manages itself, drifting between scenarios on its own
  // cadence (tied to the same prediction-interval pace as telemetry jitter) rather than
  // waiting for a person to press a button.
  const scenarioRef = useRef(scenario);
  useEffect(() => {
    scenarioRef.current = scenario;
  }, [scenario]);

  useEffect(() => {
    if (simulationMode !== "autonomous") return;
    const cycleMs = Math.max(predictionIntervalMs * 6, 14000);
    const id = window.setInterval(() => {
      if (replayGuard.current !== 0) return;
      const options = (["normal", "training-burst", "thermal-spike", "cooling-failure"] as ScenarioId[]).filter(
        (candidate) => candidate !== scenarioRef.current,
      );
      const next = options[Math.floor(Math.random() * options.length)];
      setScenario(next);
      setPulseKey((count) => count + 1);
      setTimelineEvents((events) => [...events, eventFor(SCENARIOS[next].label, SCENARIOS[next].narrative)].slice(-6));
    }, cycleMs);
    return () => window.clearInterval(id);
  }, [simulationMode, predictionIntervalMs]);

  const resetScenario = useCallback(() => {
    if (replayGuard.current !== 0) return;
    setScenario("normal");
    setPulseKey((count) => count + 1);
    setResetToken((count) => count + 1);
    setTimelineEvents([eventFor("Cluster Reset", "All zones restored to baseline configuration.")]);
  }, []);

  const triggerReplay = useCallback(() => {
    if (replayGuard.current !== 0) return;
    const token = Date.now();
    replayGuard.current = token;
    setIsReplaying(true);
    setPulseKey((count) => count + 1);

    (async () => {
      for (const step of REPLAY_STEPS) {
        if (!mounted.current || replayGuard.current !== token) return;
        setScenario(step.scenario);
        setTimelineEvents((events) => [...events, eventFor(step.title, step.description)].slice(-6));
        await sleep(1300);
      }

      if (mounted.current && replayGuard.current === token) {
        replayGuard.current = 0;
        setIsReplaying(false);
      }
    })();
  }, []);

  const value = useMemo<ScenarioEngineValue>(
    () => ({
      scenario,
      racks,
      metrics,
      ai,
      timelineEvents,
      isReplaying,
      pulseKey,
      resetToken,
      selectScenario,
      triggerReplay,
      resetScenario,
    }),
    [scenario, racks, metrics, ai, timelineEvents, isReplaying, pulseKey, resetToken, selectScenario, triggerReplay, resetScenario],
  );

  return <ScenarioEngineContext.Provider value={value}>{children}</ScenarioEngineContext.Provider>;
}

export function useScenarioEngine(): ScenarioEngineValue {
  const ctx = useContext(ScenarioEngineContext);
  if (!ctx) throw new Error("useScenarioEngine must be used within a ScenarioEngineProvider");
  return ctx;
}
