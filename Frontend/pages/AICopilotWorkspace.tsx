import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import AIPanel from "../AIPanel";
import AnimatedValue from "../components/AnimatedValue";
import LoadingState from "../components/LoadingState";
import { apiClient } from "../lib/apiClient";
import { streamChat, StreamConnectionError } from "../lib/sseClient";
import type { ActionConfirmationRequiredStreamEvent } from "../lib/types";
import { executeButtonProps, handleExecuteClick, useExecuteRecommendation } from "../lib/useExecuteRecommendation";
import { SCENARIOS, useScenarioEngine } from "../scenario/ScenarioEngine";

const SUGGESTED_PROMPTS = ["Why is Rack C1 at risk?", "Explain the current recommendation.", "What changed recently?", "Summarize cluster health."];

type ChatMessage = { id: string; role: "user" | "assistant" | "error"; content: string };

let seq = 0;
function nextId(prefix: string): string {
  seq += 1;
  return `${prefix}-${seq}`;
}

function makeMessage(role: ChatMessage["role"], content: string): ChatMessage {
  return { id: nextId("m"), role, content };
}

export default function AICopilotWorkspace() {
  const { scenario, ai, racks, isLoading } = useScenarioEngine();
  const executionFlow = useExecuteRecommendation();

  const [messages, setMessages] = useState<ChatMessage[]>([
    makeMessage("assistant", "Ask me about a rack, the active scenario, or the current recommendation — I read real cluster telemetry, not a script."),
  ]);
  const [draft, setDraft] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  // High-level operational status for the current turn only (see
  // app/schemas/ai_stream.py's ThinkingEvent/ToolStartedEvent/
  // ToolCompletedEvent) — reset every turn, never the model's private
  // reasoning, only the fixed vocabulary the backend itself emits.
  const [activity, setActivity] = useState<string[]>([]);
  const [pendingConfirmation, setPendingConfirmation] = useState<ActionConfirmationRequiredStreamEvent | null>(null);
  const [confirmationBusy, setConfirmationBusy] = useState(false);
  const conversationIdRef = useRef<string | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, isStreaming, pendingConfirmation]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  function sendPrompt(text: string) {
    const trimmed = text.trim();
    if (!trimmed || isStreaming) return;

    // A new turn always supersedes any in-flight one — never leaves an
    // orphaned stream running in the background (see the objective's
    // cancellation requirements).
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setMessages((current) => [...current, makeMessage("user", trimmed)]);
    setDraft("");
    setIsStreaming(true);
    setActivity([]);
    setPendingConfirmation(null);

    const assistantId = nextId("m");
    let assistantText = "";
    let sawText = false;
    let sawConfirmation = false;

    setMessages((current) => [...current, { id: assistantId, role: "assistant", content: "" }]);

    streamChat(
      { message: trimmed, conversation_id: conversationIdRef.current },
      {
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === "thinking") {
            setActivity((current) => [...current, event.message]);
          } else if (event.type === "tool_started") {
            setActivity((current) => [...current, `Running ${event.tool}…`]);
          } else if (event.type === "tool_completed") {
            setActivity((current) => [...current, event.ok ? `${event.tool} complete.` : `${event.tool} failed.`]);
          } else if (event.type === "text_delta") {
            sawText = true;
            assistantText += event.text;
            const snapshot = assistantText;
            setMessages((current) => current.map((m) => (m.id === assistantId ? { ...m, content: snapshot } : m)));
          } else if (event.type === "action_confirmation_required") {
            sawConfirmation = true;
            setPendingConfirmation(event);
          } else if (event.type === "completed") {
            conversationIdRef.current = event.conversation_id;
          } else if (event.type === "error") {
            const message = event.message;
            setMessages((current) => current.map((m) => (m.id === assistantId ? { ...m, role: "error", content: message } : m)));
          }
        },
      },
    )
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message =
          error instanceof StreamConnectionError || error instanceof Error ? error.message : "The AI stream failed unexpectedly.";
        setMessages((current) => current.map((m) => (m.id === assistantId ? { ...m, role: "error", content: message } : m)));
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        // A tool-only turn that ended in an action proposal never produced
        // narration text — drop the empty placeholder bubble rather than
        // show a blank assistant message.
        if (!sawText && sawConfirmation) {
          setMessages((current) => current.filter((m) => m.id !== assistantId));
        }
        setIsStreaming(false);
        if (abortRef.current === controller) abortRef.current = null;
      });
  }

  async function confirmPendingAction() {
    if (!pendingConfirmation) return;
    const { action_id } = pendingConfirmation;
    setConfirmationBusy(true);
    try {
      const action = await apiClient.confirmAction(action_id);
      setMessages((current) => [
        ...current,
        action.status === "completed"
          ? makeMessage("assistant", `Execution completed.${action.execution_id ? ` (execution ${action.execution_id})` : ""}`)
          : makeMessage("error", action.error_message ?? `Execution ended in status: ${action.status}.`),
      ]);
    } catch (error) {
      setMessages((current) => [...current, makeMessage("error", error instanceof Error ? error.message : "Confirmation request failed.")]);
    } finally {
      setConfirmationBusy(false);
      setPendingConfirmation(null);
    }
  }

  async function cancelPendingAction() {
    if (!pendingConfirmation) return;
    const { action_id } = pendingConfirmation;
    setConfirmationBusy(true);
    try {
      await apiClient.cancelAction(action_id);
      setMessages((current) => [...current, makeMessage("assistant", "Action cancelled — nothing was executed.")]);
    } catch (error) {
      setMessages((current) => [...current, makeMessage("error", error instanceof Error ? error.message : "Cancellation request failed.")]);
    } finally {
      setConfirmationBusy(false);
      setPendingConfirmation(null);
    }
  }

  if (isLoading) return <LoadingState />;

  const focusedRack = racks.reduce((hottest, rack) => (rack.temperature > hottest.temperature ? rack : hottest), racks[0]);
  const executeProps = executeButtonProps(executionFlow.state);
  const pipelineStatus = isStreaming ? "Running…" : activity.length > 0 ? "Last run complete" : "Idle";

  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-10 pt-3 sm:px-5 lg:px-8">
      <style>{`
        @keyframes chat-typing-bounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
          30% { transform: translateY(-3px); opacity: 1; }
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
              Scenario: <AnimatedValue value={SCENARIOS[scenario]?.label ?? scenario} />
            </span>
          </div>
        </div>

        <div className="grid h-[max(38rem,calc(100dvh_-_16rem))] gap-5 xl:grid-cols-[1.7fr_1fr]">
          {/* Main column: reasoning pipeline + conversation */}
          <div className="flex min-h-0 flex-col gap-4">
            {/* Reasoning activity — the real, high-level ThinkingEvent/tool
                trail for the current turn (never the model's hidden
                chain-of-thought — see backend/app/schemas/ai_stream.py). */}
            <div className="shrink-0 rounded-[1.4rem] border border-white/8 bg-white/[0.025] p-3.5">
              <div className="mb-2.5 flex items-center justify-between px-0.5">
                <p className="text-[0.5rem] uppercase tracking-[0.22em] text-white/44">NeuroCore Activity</p>
                <p className="text-[0.5rem] uppercase tracking-[0.14em] text-white/34">{pipelineStatus}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {activity.length === 0 ? (
                  <span className="rounded-xl border border-white/8 bg-white/[0.02] px-2.5 py-2 text-[0.58rem] text-white/40">
                    No activity yet — ask a question to begin.
                  </span>
                ) : (
                  activity.map((label, index) => {
                    const isLast = index === activity.length - 1;
                    const active = isLast && isStreaming;
                    return (
                      <span
                        key={`${index}-${label}`}
                        className={`relative overflow-hidden rounded-xl border px-2.5 py-2 text-[0.58rem] font-medium transition-colors duration-300 ${
                          active ? "border-violet-300/40 bg-violet-300/[0.14] text-white/90" : "border-emerald-300/20 bg-emerald-300/[0.06] text-white/72"
                        }`}
                      >
                        {active ? (
                          <span
                            className="pointer-events-none absolute inset-0"
                            style={{ boxShadow: "0 0 20px rgba(167,129,255,0.55)", animation: "dock-glow-pulse 1.3s ease-in-out infinite" }}
                          />
                        ) : null}
                        <span className="relative z-10">{label}</span>
                      </span>
                    );
                  })
                )}
              </div>
            </div>

            {/* Conversation */}
            <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-[1.4rem] border border-white/10 bg-[linear-gradient(170deg,rgba(23,14,52,0.8),rgba(10,8,26,0.9))]">
              <div className="flex shrink-0 items-center justify-between border-b border-white/8 px-4 py-3">
                <div>
                  <p className="text-[0.5rem] uppercase tracking-[0.22em] text-white/44">Conversation</p>
                  <p className="mt-0.5 text-[0.86rem] font-medium text-white">NeuroCore Copilot</p>
                </div>
                <span className="flex h-7 w-7 items-center justify-center rounded-full text-[0.62rem] font-semibold" style={{ background: SCENARIOS[scenario]?.tone.ring, color: "#0a0618" }}>
                  AI
                </span>
              </div>

              <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
                <AnimatePresence initial={false}>
                  {messages
                    .filter((message) => message.content.length > 0 || message.role === "user")
                    .map((message) => (
                      <motion.div
                        key={message.id}
                        initial={{ opacity: 0, y: 10, filter: "blur(4px)" }}
                        animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                        transition={{ duration: 0.32, ease: [0.2, 0.8, 0.2, 1] }}
                        className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
                      >
                        {message.role === "assistant" || message.role === "error" ? (
                          <div className="flex max-w-[85%] items-start gap-2">
                            <span
                              className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[0.44rem] font-semibold"
                              style={{ background: message.role === "error" ? "rgba(255,110,148,0.9)" : SCENARIOS[scenario]?.tone.ring, color: "#0a0618" }}
                            >
                              AI
                            </span>
                            <p
                              className={`rounded-2xl rounded-tl-sm border px-3.5 py-2.5 text-[0.82rem] leading-relaxed ${
                                message.role === "error"
                                  ? "border-rose-300/25 bg-rose-300/[0.08] text-rose-100/90"
                                  : "border-white/8 bg-white/[0.035] text-white/86"
                              }`}
                            >
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

                {isStreaming ? (
                  <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-2">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[0.44rem] font-semibold" style={{ background: SCENARIOS[scenario]?.tone.ring, color: "#0a0618" }}>
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

                {pendingConfirmation ? (
                  <motion.div
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="rounded-2xl border border-amber-300/25 bg-amber-300/[0.08] px-3.5 py-3"
                  >
                    <p className="text-[0.5rem] uppercase tracking-[0.2em] text-amber-100/70">Confirmation Required</p>
                    <p className="mt-1 text-[0.8rem] leading-relaxed text-white/88">{pendingConfirmation.summary}</p>
                    <div className="mt-2.5 flex gap-2">
                      <button
                        type="button"
                        disabled={confirmationBusy}
                        onClick={confirmPendingAction}
                        className="rounded-lg border border-emerald-300/30 bg-emerald-300/[0.12] px-3 py-1.5 text-[0.62rem] uppercase tracking-[0.1em] text-emerald-100/90 transition hover:bg-emerald-300/[0.2] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {confirmationBusy ? "Working…" : "Confirm"}
                      </button>
                      <button
                        type="button"
                        disabled={confirmationBusy}
                        onClick={cancelPendingAction}
                        className="rounded-lg border border-white/12 bg-white/[0.04] px-3 py-1.5 text-[0.62rem] uppercase tracking-[0.1em] text-white/70 transition hover:bg-white/[0.08] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        Cancel
                      </button>
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
                      disabled={isStreaming}
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
                    disabled={!draft.trim() || isStreaming}
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
              onExecute={() => handleExecuteClick(executionFlow.state, ai.decision, executionFlow.propose, executionFlow.confirm)}
              isExecuting={executeProps.executing}
              executeDisabled={executeProps.disabled || !ai.decision}
              executeLabel={executeProps.label}
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
