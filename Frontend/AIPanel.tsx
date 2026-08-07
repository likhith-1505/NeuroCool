import { AnimatePresence, motion } from "framer-motion";
import AnimatedValue from "./components/AnimatedValue";

type AIPanelProps = {
  currentSituation: string;
  reasoning: string;
  recommendation: string;
  expectedImpact: string;
  onExecute?: () => void;
  executeLabel?: string;
  executeDisabled?: boolean;
  isExecuting?: boolean;
  className?: string;
};

const sectionTransition = {
  duration: 0.4,
  ease: [0.2, 0.8, 0.2, 1] as const,
};

function extractConfidence(reasoning: string): number {
  const slash = reasoning.match(/(\d{1,3})\s*\/\s*100/);
  if (slash) return Math.min(100, Math.max(0, Number(slash[1])));

  const percent = reasoning.match(/(\d{1,3})(?:\.\d+)?\s*%/);
  if (percent) return Math.min(100, Math.max(0, Number(percent[1])));

  return 87;
}

function compact(value: string, max = 74): string {
  if (value.length <= max) return value;
  return `${value.slice(0, max).trimEnd()}…`;
}

function statusTone(confidence: number): string {
  if (confidence >= 85) return "text-emerald-200/85";
  if (confidence >= 70) return "text-violet-200/85";
  return "text-rose-200/85";
}

function statusLabel(confidence: number): string {
  if (confidence >= 85) return "High Confidence";
  if (confidence >= 70) return "Moderate Confidence";
  return "Low Confidence";
}

export default function AIPanel({
  currentSituation,
  reasoning,
  recommendation,
  expectedImpact,
  onExecute,
  executeLabel = "Execute Recommendation",
  executeDisabled = false,
  isExecuting = false,
  className,
}: AIPanelProps) {
  const confidence = extractConfidence(reasoning);
  const situation = compact(currentSituation, 66);
  const action = compact(recommendation, 64);
  const impact = compact(expectedImpact, 64);

  return (
    <motion.aside
      layout
      className={`relative h-full min-h-[22rem] w-full overflow-hidden rounded-[2rem] bg-[linear-gradient(176deg,rgba(18,12,44,0.82),rgba(10,7,24,0.9))] p-5 shadow-[0_24px_70px_rgba(0,0,0,0.56)] backdrop-blur-2xl ${className ?? ""}`}
    >
      <div className="pointer-events-none absolute inset-0 rounded-[inherit] border border-white/8" />
      <div className="pointer-events-none absolute -top-28 right-[-4rem] h-64 w-64 rounded-full bg-[radial-gradient(circle,rgba(169,132,255,0.24)_0%,rgba(169,132,255,0)_70%)] blur-3xl" />
      <div className="pointer-events-none absolute -bottom-24 left-[-5rem] h-64 w-64 rounded-full bg-[radial-gradient(circle,rgba(106,143,255,0.18)_0%,rgba(106,143,255,0)_72%)] blur-3xl" />

      <div className="relative z-10 flex h-full flex-col">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <p className="text-[0.56rem] uppercase tracking-[0.28em] text-white/44">NeuroCore</p>
            <h2 className="mt-1.5 bg-gradient-to-r from-white via-white to-white/70 bg-clip-text text-[1.2rem] font-medium tracking-tight text-transparent">
              AI Command Interface
            </h2>
          </div>
          <div className="inline-flex items-center gap-2 rounded-full border px-3 py-1.5" style={{ borderColor: "rgba(var(--accent-rgb),0.18)", background: "rgba(var(--accent-rgb),0.08)" }}>
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full" style={{ background: "rgba(var(--accent-rgb),0.65)" }} />
              <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: "rgba(var(--accent-rgb),1)", boxShadow: "0 0 12px rgba(var(--accent-rgb),0.88)" }} />
            </span>
            <span className="text-[0.56rem] uppercase tracking-[0.16em] text-white/66">Live Model</span>
          </div>
        </div>

        <div className="grid grid-cols-[auto_1fr] gap-4 rounded-2xl bg-[linear-gradient(180deg,rgba(255,255,255,0.05),rgba(255,255,255,0.015))] px-4 py-2.5">
          <div className="relative flex h-16 w-16 items-center justify-center rounded-full border border-white/12 bg-black/20">
            <div
              className="absolute inset-1 rounded-full"
              style={{
                background: `conic-gradient(rgba(187,158,255,0.96) 0% ${confidence}%, rgba(255,255,255,0.12) ${confidence}% 100%)`,
              }}
            />
            <div className="absolute inset-[7px] rounded-full bg-[#110b28]/95" />
            <div className="relative z-10 text-center">
              <p className="text-[1.02rem] font-semibold leading-none text-white">
                <AnimatedValue value={confidence} />
              </p>
              <p className="mt-0.5 text-[0.46rem] uppercase tracking-[0.2em] text-white/52">CONF</p>
            </div>
          </div>

          <div className="self-center">
            <p className={`text-[0.58rem] uppercase tracking-[0.2em] ${statusTone(confidence)}`}>{statusLabel(confidence)}</p>
            <p className="mt-1 text-[0.82rem] text-white/72">Prediction stream synchronized and ready for execution.</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <span className="rounded-full border border-white/14 bg-white/[0.04] px-2 py-0.5 text-[0.54rem] uppercase tracking-[0.14em] text-white/60">Thermal Safe</span>
              <span className="rounded-full border border-violet-300/20 bg-violet-300/[0.08] px-2 py-0.5 text-[0.54rem] uppercase tracking-[0.14em] text-violet-100/80">Auto-Rebalance</span>
              <span className="rounded-full border border-cyan-300/20 bg-cyan-300/[0.08] px-2 py-0.5 text-[0.54rem] uppercase tracking-[0.14em] text-cyan-100/80">Latency Guard</span>
            </div>
          </div>
        </div>

        <div
          className="mt-4 min-h-0 flex-1 space-y-2.5 overflow-y-auto pr-0.5"
          style={{ maskImage: "linear-gradient(180deg, black 88%, transparent 100%)", WebkitMaskImage: "linear-gradient(180deg, black 88%, transparent 100%)" }}
        >
          <div className="rounded-xl border border-white/8 bg-white/[0.02] px-3 py-2.5">
            <p className="text-[0.54rem] uppercase tracking-[0.22em] text-white/44">Current Analysis</p>
            <AnimatePresence initial={false}>
              <motion.p
                key={situation}
                initial={{ opacity: 0, y: 6, filter: "blur(4px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                exit={{ opacity: 0, y: -6, filter: "blur(3px)" }}
                transition={sectionTransition}
                className="mt-1 text-[0.82rem] leading-relaxed text-white/82"
              >
                {situation}
              </motion.p>
            </AnimatePresence>
          </div>

          <div className="rounded-xl border border-white/8 bg-white/[0.02] px-3 py-2.5">
            <p className="text-[0.54rem] uppercase tracking-[0.22em] text-white/44">Recommended Action</p>
            <AnimatePresence initial={false}>
              <motion.p
                key={action}
                initial={{ opacity: 0, y: 6, filter: "blur(4px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                exit={{ opacity: 0, y: -6, filter: "blur(3px)" }}
                transition={sectionTransition}
                className="mt-1 text-[0.82rem] leading-relaxed text-white/86"
              >
                {action}
              </motion.p>
            </AnimatePresence>
          </div>

          <div className="rounded-xl border border-white/8 bg-white/[0.02] px-3 py-2.5">
            <p className="text-[0.54rem] uppercase tracking-[0.22em] text-white/44">Impact Preview</p>
            <AnimatePresence initial={false}>
              <motion.p
                key={impact}
                initial={{ opacity: 0, y: 6, filter: "blur(4px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                exit={{ opacity: 0, y: -6, filter: "blur(3px)" }}
                transition={sectionTransition}
                className="mt-1 text-[0.82rem] leading-relaxed text-white/78"
              >
                {impact}
              </motion.p>
            </AnimatePresence>
          </div>
        </div>

        <div className="mt-3 shrink-0">
          <motion.button
            type="button"
            onClick={onExecute}
            disabled={executeDisabled || isExecuting}
            whileHover={{ y: -2, scale: 1.01 }}
            whileTap={{ scale: 0.995 }}
            className="inline-flex w-full items-center justify-center rounded-xl border px-4 py-2.5 text-[0.7rem] font-medium uppercase tracking-[0.16em] text-white shadow-[0_14px_30px_rgba(18,8,44,0.58)] transition duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] hover:shadow-[0_18px_38px_rgba(38,20,92,0.66)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(var(--accent-rgb),0.7)] focus-visible:ring-offset-2 focus-visible:ring-offset-[#100922] disabled:cursor-not-allowed disabled:opacity-50"
            style={{ borderColor: "rgba(var(--accent-rgb),0.24)", background: "linear-gradient(120deg, rgba(var(--accent-rgb),0.46), rgba(107,127,255,0.4))" }}
          >
            <AnimatePresence initial={false}>
              <motion.span
                key={isExecuting ? "executing" : "idle"}
                initial={{ opacity: 0, y: 5 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -5 }}
                transition={sectionTransition}
              >
                {isExecuting ? "Executing…" : executeLabel}
              </motion.span>
            </AnimatePresence>
          </motion.button>
        </div>
      </div>
    </motion.aside>
  );
}
