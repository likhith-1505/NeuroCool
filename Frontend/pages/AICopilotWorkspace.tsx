import { motion } from "framer-motion";

type AICopilotWorkspaceProps = {
  lastActionLabel: string;
};

export default function AICopilotWorkspace({ lastActionLabel }: AICopilotWorkspaceProps) {
  const history = [
    "Analyzed thermal vectors for zone C.",
    "Generated migration strategy for high-entropy jobs.",
    "Validated cooling corridor headroom.",
    lastActionLabel ? `Executed: ${lastActionLabel}.` : "Awaiting operator action.",
  ];

  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-28 pt-3 sm:px-5 lg:px-8">
      <div className="relative w-full overflow-hidden rounded-[2.2rem] bg-[linear-gradient(170deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012))] p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07),0_28px_90px_rgba(0,0,0,0.58)] backdrop-blur-[16px] sm:p-6 lg:p-7">
        <div className="grid min-h-[70vh] gap-5 xl:grid-cols-[1.6fr_1fr]">
          <div className="rounded-[1.5rem] bg-[linear-gradient(170deg,rgba(23,14,52,0.8),rgba(10,8,26,0.9))] p-5">
            <p className="text-[0.56rem] uppercase tracking-[0.24em] text-white/46">AI Copilot</p>
            <h1 className="mt-1 text-[1.4rem] font-medium text-white">Reasoning Console</h1>

            <div className="mt-5 space-y-4">
              {[
                "Current Situation: Zone C thermal pressure remains elevated.",
                "Reasoning: Forecast model predicts 11% throttling probability in 3 cycles.",
                "Prediction: Migration to A1/D4 lowers thermal variance by 18%.",
              ].map((line) => (
                <motion.div
                  key={line}
                  whileHover={{ y: -1 }}
                  className="rounded-xl border border-white/8 bg-white/[0.03] px-4 py-3 text-[0.86rem] text-white/82"
                >
                  {line}
                </motion.div>
              ))}
            </div>

            <div className="mt-5 flex flex-wrap gap-2">
              {[
                "Execute Rebalance",
                "Run Simulation",
                "Queue Cooling Boost",
                "Move Job",
                "Stabilize Zone",
              ].map((action) => (
                <button
                  key={action}
                  type="button"
                  className="rounded-full bg-violet-300/[0.14] px-3 py-1.5 text-[0.58rem] uppercase tracking-[0.14em] text-violet-100/84 transition duration-300 hover:-translate-y-[1px] hover:bg-violet-300/[0.22]"
                >
                  {action}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-4">
            <div className="rounded-[1.3rem] border border-white/10 bg-white/[0.03] p-4">
              <p className="text-[0.56rem] uppercase tracking-[0.2em] text-white/44">Prediction Confidence</p>
              <div className="mt-2 flex items-end gap-2">
                <span className="text-4xl font-semibold text-white">92</span>
                <span className="pb-1 text-[0.62rem] uppercase tracking-[0.18em] text-white/54">/100</span>
              </div>
              <div className="mt-3 h-2 rounded-full bg-white/10">
                <motion.div
                  className="h-full rounded-full bg-[linear-gradient(90deg,rgba(164,131,255,0.95),rgba(126,173,255,0.9))]"
                  initial={{ width: 0 }}
                  animate={{ width: "92%" }}
                  transition={{ duration: 0.9, ease: [0.2, 0.8, 0.2, 1] }}
                />
              </div>
            </div>

            <div className="rounded-[1.3rem] border border-white/10 bg-white/[0.03] p-4">
              <p className="text-[0.56rem] uppercase tracking-[0.2em] text-white/44">Execution History</p>
              <div className="mt-2 space-y-2">
                {history.map((item) => (
                  <div key={item} className="rounded-lg bg-white/[0.03] px-3 py-2 text-[0.76rem] text-white/75">
                    {item}
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-[1.3rem] border border-white/10 bg-white/[0.03] p-4">
              <p className="text-[0.56rem] uppercase tracking-[0.2em] text-white/44">Suggested Automations</p>
              <div className="mt-2 space-y-1.5 text-[0.76rem] text-white/72">
                <p>• Auto-balance inference spikes above 82% GPU.</p>
                <p>• Trigger cooling assist if rack exceeds 80°C.</p>
                <p>• Snapshot state before every migration batch.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
