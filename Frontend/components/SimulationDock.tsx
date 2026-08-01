import { motion } from "framer-motion";

export type SimulationAction =
  | "training-job"
  | "inference-spike"
  | "thermal-spike"
  | "cooling-failure"
  | "power-surge"
  | "run-ai"
  | "replay"
  | "reset";

type SimulationDockProps = {
  onAction: (action: SimulationAction) => void;
};

const actions: Array<{ id: SimulationAction; label: string }> = [
  { id: "training-job", label: "Training Job" },
  { id: "inference-spike", label: "Inference Spike" },
  { id: "thermal-spike", label: "Thermal Spike" },
  { id: "cooling-failure", label: "Cooling Failure" },
  { id: "power-surge", label: "Power Surge" },
  { id: "run-ai", label: "Run AI" },
  { id: "replay", label: "Replay" },
  { id: "reset", label: "Reset" },
];

export default function SimulationDock({ onAction }: SimulationDockProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.2, 0.8, 0.2, 1] }}
      className="fixed bottom-5 left-1/2 z-40 w-[min(72rem,96vw)] -translate-x-1/2 rounded-2xl border border-white/10 bg-[linear-gradient(120deg,rgba(25,16,55,0.86),rgba(10,8,25,0.9))] p-2 shadow-[0_18px_50px_rgba(0,0,0,0.55)] backdrop-blur-2xl"
    >
      <div className="flex flex-wrap items-center justify-center gap-1.5">
        {actions.map((action) => (
          <button
            key={action.id}
            type="button"
            onClick={() => onAction(action.id)}
            className="rounded-full bg-white/[0.045] px-3 py-1.5 text-[0.58rem] uppercase tracking-[0.13em] text-white/72 transition duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] hover:-translate-y-[1px] hover:bg-violet-300/[0.2] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-300/70"
          >
            {action.label}
          </button>
        ))}
      </div>
    </motion.div>
  );
}
