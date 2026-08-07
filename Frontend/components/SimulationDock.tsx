import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { SCENARIOS, useScenarioEngine, type ScenarioId } from "../scenario/ScenarioEngine";

type Control =
  | { kind: "scenario"; id: ScenarioId; icon: string; hint: string }
  | { kind: "replay"; icon: string; hint: string }
  | { kind: "reset"; icon: string; hint: string };

const CONTROLS: Control[] = [
  { kind: "scenario", id: "normal", icon: "●", hint: "Return the cluster to a healthy baseline" },
  { kind: "scenario", id: "training-burst", icon: "◎", hint: "Simulate a distributed training burst" },
  { kind: "scenario", id: "thermal-spike", icon: "◉", hint: "Simulate a rapid thermal excursion" },
  { kind: "scenario", id: "cooling-failure", icon: "◌", hint: "Simulate a cooling subsystem fault" },
  { kind: "replay", icon: "↺", hint: "Replay the full incident sequence" },
  { kind: "reset", icon: "⟲", hint: "Reset the cluster to baseline" },
];

function controlKey(control: Control): string {
  return control.kind === "scenario" ? control.id : control.kind;
}

function controlLabel(control: Control): string {
  if (control.kind === "scenario") return SCENARIOS[control.id].label;
  return control.kind === "replay" ? "Replay" : "Reset";
}

function scaleFor(index: number, hoverIndex: number | null): number {
  if (hoverIndex == null) return 1;
  const distance = Math.abs(index - hoverIndex);
  if (distance === 0) return 1.34;
  if (distance === 1) return 1.16;
  if (distance === 2) return 1.06;
  return 1;
}

function liftFor(index: number, hoverIndex: number | null): number {
  if (hoverIndex == null) return 0;
  const distance = Math.abs(index - hoverIndex);
  if (distance === 0) return -10;
  if (distance === 1) return -4;
  return 0;
}

export default function SimulationDock() {
  const { scenario, isReplaying, pulseKey, selectScenario, triggerReplay, resetScenario } = useScenarioEngine();
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [flashKey, setFlashKey] = useState<string | null>(null);
  const lastTriggered = useRef<string | null>(null);

  useEffect(() => {
    if (!pulseKey || !lastTriggered.current) return;
    setFlashKey(lastTriggered.current);
    const timeout = window.setTimeout(() => setFlashKey(null), 1800);
    return () => window.clearTimeout(timeout);
  }, [pulseKey]);

  function handleClick(control: Control) {
    if (isReplaying) return;
    lastTriggered.current = controlKey(control);
    if (control.kind === "scenario") selectScenario(control.id);
    else if (control.kind === "replay") triggerReplay();
    else resetScenario();
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, x: "-50%" }}
      animate={{ opacity: 1, y: 0, x: "-50%" }}
      transition={{ duration: 0.35, ease: [0.2, 0.8, 0.2, 1] }}
      className="fixed bottom-5 left-1/2 z-40 w-[min(64rem,96vw)] rounded-[1.5rem] border border-white/12 bg-[linear-gradient(135deg,rgba(28,18,62,0.88),rgba(12,9,30,0.92))] p-2.5 shadow-[0_18px_54px_rgba(0,0,0,0.6)] backdrop-blur-2xl"
    >
      <div className="pointer-events-none absolute inset-x-8 top-0 h-px bg-gradient-to-r from-transparent via-violet-200/55 to-transparent" />
      <div className="pointer-events-none absolute -top-8 left-1/2 h-14 w-48 -translate-x-1/2 rounded-full bg-violet-300/20 blur-2xl" />

      <div className="flex flex-wrap items-end justify-center gap-2.5 pb-0.5 pt-1" onMouseLeave={() => setHoverIndex(null)}>
        {CONTROLS.map((control, index) => {
          const key = controlKey(control);
          const isActive = control.kind === "scenario" && control.id === scenario;
          const isGlowing = flashKey === key;
          const isHovered = hoverIndex === index;
          const label = controlLabel(control);

          return (
            <motion.button
              key={key}
              type="button"
              onClick={() => handleClick(control)}
              onMouseEnter={() => setHoverIndex(index)}
              onFocus={() => setHoverIndex(index)}
              animate={{ scale: scaleFor(index, hoverIndex), y: liftFor(index, hoverIndex) }}
              transition={{ type: "spring", stiffness: 380, damping: 22, mass: 0.6 }}
              className={`group relative flex min-w-[5.4rem] flex-col items-center gap-1.5 rounded-xl px-2.5 py-2.5 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.08)] transition-colors duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(var(--accent-rgb),0.7)] ${
                isActive ? "bg-[rgba(var(--accent-rgb),0.16)] text-white" : "bg-white/[0.045] text-white/75 hover:bg-[rgba(var(--accent-rgb),0.18)] hover:text-white"
              } ${isReplaying && control.kind !== "replay" ? "opacity-40" : ""}`}
              style={{ transformOrigin: "bottom center" }}
            >
              <span
                className="pointer-events-none absolute inset-0 rounded-xl"
                style={
                  isGlowing
                    ? { boxShadow: "0 0 34px rgba(var(--accent-rgb),0.85)", animation: "dock-glow-pulse 0.9s ease-in-out 2" }
                    : isActive
                      ? { boxShadow: "0 0 22px rgba(var(--accent-rgb),0.55)" }
                      : {
                          boxShadow: "0 0 28px rgba(var(--accent-rgb),0.45)",
                          opacity: isHovered ? 1 : 0,
                          transition: "opacity 0.3s ease",
                        }
                }
              />
              {isActive || isGlowing ? (
                <span
                  className="absolute -top-1 h-1.5 w-1.5 rounded-full"
                  style={{ background: "rgba(var(--accent-rgb),0.95)", boxShadow: "0 0 8px rgba(var(--accent-rgb),0.9)" }}
                />
              ) : null}

              <span className="relative z-10 text-[0.82rem] leading-none">{control.icon}</span>
              <span className="relative z-10 text-[0.5rem] uppercase tracking-[0.12em]">{label}</span>

              <AnimatePresence>
                {isHovered ? (
                  <motion.span
                    initial={{ opacity: 0, y: 4, scale: 0.96 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: 4, scale: 0.96 }}
                    transition={{ duration: 0.16, ease: [0.2, 0.8, 0.2, 1] }}
                    className="pointer-events-none absolute -top-12 left-1/2 z-30 w-max max-w-[11rem] -translate-x-1/2 rounded-lg border border-white/12 bg-[#0d0920]/95 px-2.5 py-1.5 text-center shadow-[0_10px_28px_rgba(0,0,0,0.5)] backdrop-blur-xl"
                  >
                    <p className="text-[0.56rem] font-medium uppercase tracking-[0.1em] text-white/88">{label}</p>
                    <p className="mt-0.5 text-[0.52rem] leading-snug text-white/56">{control.hint}</p>
                    <span className="absolute left-1/2 top-full h-2 w-2 -translate-x-1/2 -translate-y-1 rotate-45 border-b border-r border-white/12 bg-[#0d0920]/95" />
                  </motion.span>
                ) : null}
              </AnimatePresence>
            </motion.button>
          );
        })}
      </div>
    </motion.div>
  );
}
