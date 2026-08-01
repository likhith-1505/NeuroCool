import { motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import type { SimulationAction } from "../components/SimulationDock";

type TwinRack = {
  id: string;
  name: string;
  x: number;
  y: number;
  temperature: number;
  gpu: number;
  jobs: number;
  prediction: string;
  cooling: string;
  power: string;
  recommendation: string;
};

type DigitalTwinWorkspaceProps = {
  simulationPulse: number;
  lastActionLabel: string;
};

const seedRacks: TwinRack[] = [
  {
    id: "r1",
    name: "Rack A1",
    x: 22,
    y: 36,
    temperature: 69.4,
    gpu: 62,
    jobs: 22,
    prediction: "Stable",
    cooling: "Optimal",
    power: "1.08 MW",
    recommendation: "Maintain workload profile.",
  },
  {
    id: "r2",
    name: "Rack B2",
    x: 46,
    y: 24,
    temperature: 74.2,
    gpu: 71,
    jobs: 29,
    prediction: "Watch",
    cooling: "Elevated",
    power: "1.22 MW",
    recommendation: "Distribute burst inference jobs.",
  },
  {
    id: "r3",
    name: "Rack C1",
    x: 67,
    y: 42,
    temperature: 82.1,
    gpu: 88,
    jobs: 34,
    prediction: "Risk Rising",
    cooling: "Strained",
    power: "1.36 MW",
    recommendation: "Immediate thermal rebalance.",
  },
  {
    id: "r4",
    name: "Rack D4",
    x: 38,
    y: 62,
    temperature: 70.5,
    gpu: 58,
    jobs: 18,
    prediction: "Stable",
    cooling: "Optimal",
    power: "1.03 MW",
    recommendation: "Reserve for migration target.",
  },
];

const links: Array<[string, string]> = [
  ["r1", "r2"],
  ["r2", "r3"],
  ["r3", "r4"],
  ["r4", "r1"],
  ["r2", "r4"],
];

function applyAction(racks: TwinRack[], action: SimulationAction | "none"): TwinRack[] {
  if (action === "none") return racks;
  if (action === "reset") return seedRacks;

  return racks.map((rack, index) => {
    const heat = action === "thermal-spike" ? 5.2 : action === "cooling-failure" ? 6.4 : action === "run-ai" ? -2.8 : 1.8;
    const load = action === "inference-spike" ? 16 : action === "training-job" ? 10 : action === "run-ai" ? -8 : 4;

    const nextTemp = Math.max(48, Math.min(95, rack.temperature + heat - (index % 2 === 0 ? 0 : 1.1)));
    const nextGpu = Math.max(25, Math.min(100, rack.gpu + load - (index % 2 === 0 ? 0 : 2)));

    return {
      ...rack,
      temperature: Number(nextTemp.toFixed(1)),
      gpu: Number(nextGpu.toFixed(0)),
      prediction: nextTemp > 83 || nextGpu > 88 ? "Critical Risk" : nextTemp > 76 ? "Watch" : "Stable",
      cooling: nextTemp > 80 ? "Strained" : nextTemp > 74 ? "Elevated" : "Optimal",
    };
  });
}

export default function DigitalTwinWorkspace({ simulationPulse, lastActionLabel }: DigitalTwinWorkspaceProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [racks, setRacks] = useState<TwinRack[]>(seedRacks);
  const [focusedRackId, setFocusedRackId] = useState("r1");

  useEffect(() => {
    if (!simulationPulse) return;
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
    const action = actionMap[lastActionLabel] ?? "none";
    setRacks((current) => applyAction(current, action));
  }, [lastActionLabel, simulationPulse]);

  const focusedRack = racks.find((rack) => rack.id === focusedRackId) ?? racks[0];
  const rackMap = new Map(racks.map((rack) => [rack.id, rack]));

  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-28 pt-3 sm:px-5 lg:px-8">
      <div className="relative w-full overflow-hidden rounded-[2.2rem] bg-[linear-gradient(170deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012))] p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07),0_28px_90px_rgba(0,0,0,0.58)] backdrop-blur-[16px] sm:p-6 lg:p-7">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <p className="text-[0.56rem] uppercase tracking-[0.24em] text-white/44">Digital Twin</p>
            <h1 className="mt-1 bg-gradient-to-r from-white to-white/72 bg-clip-text text-[1.3rem] font-medium tracking-tight text-transparent">Interactive Cluster Field</h1>
          </div>
        </div>

        <div className="grid min-h-[70vh] gap-4 xl:grid-cols-[1.7fr_1fr]">
          <div
            ref={containerRef}
            className="relative overflow-hidden rounded-[1.6rem] bg-[radial-gradient(circle_at_22%_12%,rgba(144,93,255,0.25),transparent_38%),radial-gradient(circle_at_84%_20%,rgba(97,134,255,0.2),transparent_40%),linear-gradient(165deg,#070511,#0e0922_46%,#150d30)]"
          >
            <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
              <defs>
                <linearGradient id="twin-link" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="rgba(170,141,255,0.1)" />
                  <stop offset="48%" stopColor="rgba(209,192,255,0.7)" />
                  <stop offset="100%" stopColor="rgba(120,180,255,0.1)" />
                </linearGradient>
              </defs>

              {links.map(([fromId, toId], index) => {
                const from = rackMap.get(fromId);
                const to = rackMap.get(toId);
                if (!from || !to) return null;

                const mx = (from.x + to.x) / 2;
                const my = (from.y + to.y) / 2;
                const bend = (index % 2 === 0 ? 1 : -1) * 3.2;
                const d = `M ${from.x} ${from.y} Q ${mx + bend} ${my - bend} ${to.x} ${to.y}`;

                return (
                  <g key={`${fromId}-${toId}`}>
                    <path d={d} fill="none" stroke="url(#twin-link)" strokeWidth="0.28" opacity="0.32" />
                    <path
                      d={d}
                      fill="none"
                      stroke="rgba(218,205,255,0.94)"
                      strokeWidth="0.09"
                      strokeDasharray="2.8 6.2"
                      style={{ animation: `neurocool-flow ${2 + (index % 4) * 0.45}s linear ${index * -0.3}s infinite` }}
                    />
                  </g>
                );
              })}
            </svg>

            <motion.div
              className="absolute inset-0"
              animate={{ scale: focusedRackId ? 1.02 : 1, x: focusedRackId ? -8 : 0, y: focusedRackId ? 6 : 0 }}
              transition={{ type: "spring", stiffness: 110, damping: 28 }}
            >
              {racks.map((rack) => (
                <motion.div
                  key={rack.id}
                  drag
                  dragMomentum={false}
                  dragConstraints={containerRef}
                  onDragEnd={(_, info) => {
                    const bounds = containerRef.current?.getBoundingClientRect();
                    if (!bounds) return;
                    const nextX = ((rack.x / 100) * bounds.width + info.offset.x) / bounds.width;
                    const nextY = ((rack.y / 100) * bounds.height + info.offset.y) / bounds.height;
                    setRacks((current) =>
                      current.map((node) =>
                        node.id === rack.id
                          ? {
                              ...node,
                              x: Math.min(90, Math.max(10, nextX * 100)),
                              y: Math.min(88, Math.max(12, nextY * 100)),
                            }
                          : node,
                      ),
                    );
                  }}
                  style={{ left: `${rack.x}%`, top: `${rack.y}%` }}
                  className="absolute -translate-x-1/2 -translate-y-1/2"
                >
                  <button
                    type="button"
                    onClick={() => setFocusedRackId(rack.id)}
                    className={`group relative h-24 w-24 rounded-full border border-white/12 bg-[radial-gradient(circle_at_30%_24%,rgba(255,255,255,0.24),rgba(255,255,255,0.04)_38%,rgba(14,10,32,0.95)_70%)] backdrop-blur-xl transition duration-300 hover:-translate-y-1 hover:shadow-[0_0_42px_rgba(158,123,255,0.42)] ${
                      focusedRackId === rack.id ? "shadow-[0_0_46px_rgba(165,130,255,0.56)]" : ""
                    }`}
                  >
                    <span className="absolute inset-[4px] rounded-full border border-violet-200/35" />
                    <span className="absolute inset-[14px] rounded-full bg-[radial-gradient(circle,rgba(191,164,255,0.5)_0%,rgba(119,95,255,0.04)_78%)]" />
                    <span className="relative z-10 text-[0.52rem] uppercase tracking-[0.16em] text-white/70">{rack.name}</span>
                    <span className="absolute bottom-3 left-1/2 -translate-x-1/2 text-[0.54rem] text-white/64">{rack.temperature.toFixed(1)}°C</span>
                  </button>
                </motion.div>
              ))}
            </motion.div>
          </div>

          <motion.aside
            key={focusedRack.id}
            initial={{ opacity: 0, x: 12 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.32, ease: [0.2, 0.8, 0.2, 1] }}
            className="rounded-[1.4rem] border border-white/10 bg-[linear-gradient(180deg,rgba(30,18,67,0.7),rgba(14,9,34,0.86))] p-4 backdrop-blur-xl"
          >
            <p className="text-[0.56rem] uppercase tracking-[0.2em] text-white/46">Rack Inspector</p>
            <h3 className="mt-1 text-lg font-medium text-white">{focusedRack.name}</h3>

            <div className="mt-4 space-y-2 text-[0.82rem] text-white/78">
              <p>Temperature: <span className="text-white">{focusedRack.temperature.toFixed(1)}°C</span></p>
              <p>GPU Usage: <span className="text-white">{focusedRack.gpu}%</span></p>
              <p>Running Jobs: <span className="text-white">{focusedRack.jobs}</span></p>
              <p>Prediction: <span className="text-white">{focusedRack.prediction}</span></p>
              <p>Cooling: <span className="text-white">{focusedRack.cooling}</span></p>
              <p>Power: <span className="text-white">{focusedRack.power}</span></p>
            </div>

            <div className="mt-4 rounded-xl bg-white/[0.03] p-3">
              <p className="text-[0.56rem] uppercase tracking-[0.18em] text-white/46">AI Recommendation</p>
              <p className="mt-1 text-[0.8rem] text-white/80">{focusedRack.recommendation}</p>
            </div>
          </motion.aside>
        </div>
      </div>
    </div>
  );
}
