import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import AIPanel from "../AIPanel";
import AnimatedValue from "../components/AnimatedValue";
import { SCENARIOS, useScenarioEngine, type ScenarioAi, type ScenarioId, type ScenarioRack } from "../scenario/ScenarioEngine";

const PIPELINE_STAGES = [
  "Analyzing telemetry",
  "Thermal prediction",
  "GPU scheduling",
  "Cooling optimization",
  "Migration plan",
  "Execution ready",
];

const SUGGESTED_PROMPTS = ["Why is Rack C hot?", "Simulate thermal spike.", "Explain recommendation.", "Optimize cooling."];

type ChatMessage = { id: string; role: "user" | "assistant"; content: string };

type ChatContext = {
  scenario: ScenarioId;
  racks: ScenarioRack[];
  ai: ScenarioAi;
  selectScenario: (id: ScenarioId) => void;
};

let seq = 0;
function nextId(prefix: string): string {
  seq += 1;
  return `${prefix}-${seq}`;
}

function makeMessage(role: ChatMessage["role"], content: string): ChatMessage {
  return { id: nextId("m"), role, content };
}

function buildReply(promptRaw: string, ctx: ChatContext): { text: string; action?: () => void } {
  const prompt = promptRaw.toLowerCase();
  const rackC = ctx.racks.find((rack) => rack.id === "r3") ?? ctx.racks[0];

  if (/rack\s*c/.test(prompt) && /(hot|warm|temp|heat)/.test(prompt)) {
    if (rackC.prediction === "Critical Risk" || ctx.scenario === "thermal-spike") {
      return {
        text: `Rack C1 is running at ${rackC.temperature.toFixed(1)}°C with GPU load at ${rackC.gpu}% — a rapid thermal excursion was detected as compute density outpaced cooling. ${ctx.ai.recommendation}`,
      };
    }
    return {
      text: `Rack C1 is currently stable at ${rackC.temperature.toFixed(1)}°C with GPU load at ${rackC.gpu}%, well within its safe thermal envelope. No excursion detected right now.`,
    };
  }

  if (/simulate/.test(prompt) && /thermal/.test(prompt)) {
    return {
      text: "Initiating a rapid thermal excursion on Rack C1 — watch temperatures climb across Mission Control and the Digital Twin in real time.",
      action: () => ctx.selectScenario("thermal-spike"),
    };
  }

  if (/explain/.test(prompt) && /recommend/.test(prompt)) {
    return {
      text: `${ctx.ai.recommendation} ${ctx.ai.impact} This is based on a ${ctx.ai.confidence}/100 confidence read on the current telemetry window.`,
    };
  }

  if (/cool/.test(prompt) && /(optim|improve|boost)/.test(prompt)) {
    return {
      text: `Cooling on the hottest zone (Rack C1) is currently ${rackC.cooling.toLowerCase()}. Recommended action: increase airflow to that rack and stagger burst jobs to avoid concentrated heat. ${
        ctx.scenario !== "normal" ? "Executing the current recommendation will help stabilize this now." : "No immediate action required — cooling is already efficient."
      }`,
    };
  }

  return {
    text: `Current cluster status: ${SCENARIOS[ctx.scenario].label}. ${ctx.ai.situation} Try asking about a specific rack, or "Explain recommendation" for more detail.`,
  };
}

function stageState(index: number, activeStage: number, complete: boolean): "idle" | "pending" | "active" | "done" {
  if (activeStage === -1) return "idle";
  if (complete) return "done";
  if (index < activeStage) return "done";
  if (index === activeStage) return "active";
  return "pending";
}

export default function AICopilotWorkspace() {
  const { scenario, ai, racks, selectScenario } = useScenarioEngine();

  const [messages, setMessages] = useState<ChatMessage[]>([
    makeMessage("assistant", "I'm watching telemetry across all four racks. Ask me anything, or try a suggestion below."),
    makeMessage("user", "Why is Rack C hot?"),
    makeMessage(
      "assistant",
      "Rack C1 is currently stable, well within its safe thermal envelope. No excursion detected right now — I'll flag it immediately if that changes.",
    ),
    makeMessage("user", "What should I watch for next?"),
    makeMessage(
      "assistant",
      "Keep an eye on GPU utilization during burst windows — that's the leading indicator before any thermal drift shows up here.",
    ),
  ]);
  const [draft, setDraft] = useState("");
  const [isThinking, setIsThinking] = useState(false);
  const [activeStage, setActiveStage] = useState(-1);
  const [pipelineComplete, setPipelineComplete] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);
  const pipelineGuard = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, isThinking]);

  function runPipeline(onDone: () => void) {
    const token = ++pipelineGuard.current;
    setPipelineComplete(false);
    setActiveStage(0);

    let i = 0;
    function step() {
      if (pipelineGuard.current !== token) return;
      if (i >= PIPELINE_STAGES.length - 1) {
        setPipelineComplete(true);
        onDone();
        return;
      }
      i += 1;
      setActiveStage(i);
      window.setTimeout(step, 480);
    }
    window.setTimeout(step, 480);
  }

  function handleExecute() {
    if (isExecuting) return;
    setIsExecuting(true);
    const wasIdle = scenario === "normal";
    runPipeline(() => {
      if (!wasIdle) selectScenario("normal");
      setIsExecuting(false);
      setMessages((current) =>
        [
          ...current,
          makeMessage(
            "assistant",
            wasIdle
              ? "Execution complete. Workload distribution confirmed optimal — no changes needed."
              : "Execution complete. Cluster restored to nominal operating range.",
          ),
        ].slice(-30),
      );
    });
  }

  function sendPrompt(text: string) {
    const trimmed = text.trim();
    if (!trimmed || isThinking) return;

    setMessages((current) => [...current, makeMessage("user", trimmed)].slice(-30));
    setDraft("");
    setIsThinking(true);

    const { text: reply, action } = buildReply(trimmed, { scenario, racks, ai, selectScenario });

    window.setTimeout(() => {
      setIsThinking(false);
      setMessages((current) => [...current, makeMessage("assistant", reply)].slice(-30));
      action?.();
    }, 700);
  }

  const focusedRack = racks.reduce((hottest, rack) => (rack.temperature > hottest.temperature ? rack : hottest), racks[0]);

  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-10 pt-3 sm:px-5 lg:px-8">
      <style>{`
        @keyframes chat-typing-bounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
          30% { transform: translateY(-3px); opacity: 1; }
        }
        @keyframes pipeline-connector-flow {
          0% { background-position: 0% 0%; }
          100% { background-position: 200% 0%; }
        }
      `}</style>

      <div className="relative w-full overflow-hidden rounded-[2.2rem] bg-[linear-gradient(170deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012))] p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07),0_28px_90px_rgba(0,0,0,0.58)] backdrop-blur-[16px] sm:p-6 lg:p-7">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <p className="text-[0.56rem] uppercase tracking-[0.24em] text-white/44">AI Copilot</p>
            <h1 className="mt-1 bg-gradient-to-r from-white to-white/72 bg-clip-text text-[1.3rem] font-medium tracking-tight text-transparent">Reasoning Console</h1>
          </div>
          <div className="hidden items-center gap-2 rounded-full border px-3 py-1.5 sm:inline-flex" style={{ borderColor: "rgba(var(--accent-rgb),0.18)", background: "rgba(var(--accent-rgb),0.08)" }}>
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full" style={{ background: "rgba(var(--accent-rgb),0.65)" }} />
              <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: "rgba(var(--accent-rgb),1)", boxShadow: "0 0 12px rgba(var(--accent-rgb),0.88)" }} />
            </span>
            <span className="text-[0.56rem] uppercase tracking-[0.16em] text-white/66">
              Scenario: <AnimatedValue value={SCENARIOS[scenario].label} />
            </span>
          </div>
        </div>

        <div className="grid h-[max(38rem,calc(100dvh_-_16rem))] gap-5 xl:grid-cols-[1.7fr_1fr]">
          {/* Main column: reasoning pipeline + conversation */}
          <div className="flex min-h-0 flex-col gap-4">
            {/* Reasoning pipeline */}
            <div className="shrink-0 rounded-[1.4rem] border border-white/8 bg-white/[0.025] p-3.5">
              <div className="mb-2.5 flex items-center justify-between px-0.5">
                <p className="text-[0.5rem] uppercase tracking-[0.22em] text-white/44">Reasoning Pipeline</p>
                <p className="text-[0.5rem] uppercase tracking-[0.14em] text-white/34">
                  {isExecuting ? "Running…" : pipelineComplete ? "Last run complete" : "Idle"}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                {PIPELINE_STAGES.map((label, index) => {
                  const state = stageState(index, activeStage, pipelineComplete);
                  return (
                    <div
                      key={label}
                      className={`relative overflow-hidden rounded-xl border px-2.5 py-2 transition-colors duration-300 ${
                        state === "done"
                          ? "border-emerald-300/25 bg-emerald-300/[0.08]"
                          : state === "active"
                            ? "border-violet-300/40 bg-violet-300/[0.14]"
                            : "border-white/8 bg-white/[0.02]"
                      }`}
                    >
                      {state === "active" ? (
                        <span
                          className="pointer-events-none absolute inset-0"
                          style={{ boxShadow: "0 0 20px rgba(167,129,255,0.55)", animation: "dock-glow-pulse 1.3s ease-in-out infinite" }}
                        />
                      ) : null}
                      <div className="relative z-10 flex items-start gap-1.5">
                        <span
                          className={`mt-px flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full text-[0.5rem] font-semibold ${
                            state === "done" ? "bg-emerald-300 text-[#0a1a12]" : state === "active" ? "bg-violet-300 text-[#0a0618]" : "bg-white/12 text-white/50"
                          }`}
                        >
                          {state === "done" ? "✓" : index + 1}
                        </span>
                        <p className={`min-w-0 text-[0.58rem] font-medium leading-tight ${state === "pending" || state === "idle" ? "text-white/40" : "text-white/88"}`}>
                          {label}
                        </p>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Conversation */}
            <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-[1.4rem] border border-white/10 bg-[linear-gradient(170deg,rgba(23,14,52,0.8),rgba(10,8,26,0.9))]">
              <div className="flex shrink-0 items-center justify-between border-b border-white/8 px-4 py-3">
                <div>
                  <p className="text-[0.5rem] uppercase tracking-[0.22em] text-white/44">Conversation</p>
                  <p className="mt-0.5 text-[0.86rem] font-medium text-white">NeuroCore Copilot</p>
                </div>
                <span className="flex h-7 w-7 items-center justify-center rounded-full text-[0.62rem] font-semibold" style={{ background: SCENARIOS[scenario].tone.ring, color: "#0a0618" }}>
                  AI
                </span>
              </div>

              <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
                <AnimatePresence initial={false}>
                  {messages.map((message) => (
                    <motion.div
                      key={message.id}
                      initial={{ opacity: 0, y: 10, filter: "blur(4px)" }}
                      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                      transition={{ duration: 0.32, ease: [0.2, 0.8, 0.2, 1] }}
                      className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
                    >
                      {message.role === "assistant" ? (
                        <div className="flex max-w-[85%] items-start gap-2">
                          <span
                            className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[0.44rem] font-semibold"
                            style={{ background: SCENARIOS[scenario].tone.ring, color: "#0a0618" }}
                          >
                            AI
                          </span>
                          <p className="rounded-2xl rounded-tl-sm border border-white/8 bg-white/[0.035] px-3.5 py-2.5 text-[0.82rem] leading-relaxed text-white/86">
                            {message.content}
                          </p>
                        </div>
                      ) : (
                        <p className="max-w-[85%] rounded-2xl rounded-tr-sm bg-[linear-gradient(120deg,rgba(137,104,255,0.4),rgba(107,127,255,0.34))] px-3.5 py-2.5 text-[0.82rem] leading-relaxed text-white">
                          {message.content}
                        </p>
                      )}
                    </motion.div>
                  ))}
                </AnimatePresence>

                {isThinking ? (
                  <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-2">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[0.44rem] font-semibold" style={{ background: SCENARIOS[scenario].tone.ring, color: "#0a0618" }}>
                      AI
                    </span>
                    <div className="flex items-center gap-1 rounded-2xl rounded-tl-sm border border-white/8 bg-white/[0.035] px-3.5 py-3">
                      {[0, 1, 2].map((dot) => (
                        <span
                          key={dot}
                          className="h-1.5 w-1.5 rounded-full bg-white/60"
                          style={{ animation: `chat-typing-bounce 1.1s ease-in-out ${dot * 0.15}s infinite` }}
                        />
                      ))}
                    </div>
                  </motion.div>
                ) : null}
              </div>

              <div className="shrink-0 border-t border-white/8 px-4 py-3">
                <div className="flex flex-wrap gap-1.5">
                  {SUGGESTED_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => {
                        setDraft(prompt);
                        inputRef.current?.focus();
                      }}
                      disabled={isThinking}
                      className="rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-1 text-[0.62rem] text-white/68 transition duration-300 hover:-translate-y-[1px] hover:border-[rgba(var(--accent-rgb),0.3)] hover:bg-[rgba(var(--accent-rgb),0.12)] hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>

                <form
                  className="mt-2.5 flex items-center gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    sendPrompt(draft);
                  }}
                >
                  <input
                    ref={inputRef}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder="Ask about a rack, a scenario, or a recommendation…"
                    className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/[0.03] px-3.5 py-2.5 text-[0.82rem] text-white placeholder:text-white/34 outline-none transition duration-300 focus:border-[rgba(var(--accent-rgb),0.5)] focus:bg-white/[0.05] focus:shadow-[0_0_0_3px_rgba(var(--accent-rgb),0.14)]"
                  />
                  <motion.button
                    type="submit"
                    disabled={!draft.trim() || isThinking}
                    whileHover={{ scale: 1.04 }}
                    whileTap={{ scale: 0.96 }}
                    style={{ borderColor: "rgba(var(--accent-rgb),0.24)", background: "linear-gradient(120deg, rgba(var(--accent-rgb),0.46), rgba(107,127,255,0.4))" }}
                    className="inline-flex h-[2.6rem] w-[2.6rem] shrink-0 items-center justify-center rounded-xl border text-white shadow-[0_10px_24px_rgba(18,8,44,0.5)] transition duration-300 disabled:cursor-not-allowed disabled:opacity-40"
                    aria-label="Send message"
                  >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M5 12h14M13 6l6 6-6 6" />
                    </svg>
                  </motion.button>
                </form>
              </div>
            </div>
          </div>

          {/* Side column: AI panel (primary action) + automations */}
          <div className="flex min-h-0 flex-col gap-4">
            <AIPanel
              currentSituation={`${focusedRack.name} at ${focusedRack.temperature.toFixed(1)}°C · GPU ${focusedRack.gpu}%`}
              reasoning={ai.reasoning}
              recommendation={ai.recommendation}
              expectedImpact={ai.impact}
              onExecute={handleExecute}
              isExecuting={isExecuting}
              executeLabel="Execute Recommendation"
              className="flex-1"
            />

            <div className="shrink-0 rounded-[1.3rem] border border-white/10 bg-white/[0.03] p-3.5">
              <p className="text-[0.54rem] uppercase tracking-[0.18em] text-white/44">Suggested Automations</p>
              <div className="mt-1.5 space-y-1 text-[0.72rem] leading-snug text-white/72">
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
