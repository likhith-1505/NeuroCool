import { motion } from "framer-motion";
import { useState } from "react";
import {
  ACCENT_OPTIONS,
  PREDICTION_INTERVAL_OPTIONS,
  useSettings,
  type MotionMode,
  type SimulationMode,
  type ThemeMode,
} from "../settings/SettingsContext";

const providers = ["Lukstack Native", "OpenAI", "Anthropic"];

function OptionPill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative rounded-lg px-3 py-2 text-left text-[0.78rem] transition-colors duration-300 ${
        active ? "text-white" : "text-white/62 hover:text-white/85"
      }`}
    >
      {active ? (
        <motion.span
          layoutId="settings-pill"
          className="absolute inset-0 rounded-lg"
          style={{ background: "rgba(var(--accent-rgb),0.16)", boxShadow: "inset 0 0 0 1px rgba(var(--accent-rgb),0.3)" }}
          transition={{ type: "spring", stiffness: 420, damping: 34 }}
        />
      ) : (
        <span className="absolute inset-0 rounded-lg bg-white/[0.03]" />
      )}
      <span className="relative z-10">{label}</span>
    </button>
  );
}

function SectionCard({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-[1.4rem] border border-white/10 bg-white/[0.03] p-5">
      <p className="text-[0.58rem] uppercase tracking-[0.2em] text-white/44">{title}</p>
      {hint ? <p className="mt-1 text-[0.66rem] leading-relaxed text-white/38">{hint}</p> : null}
      <div className="mt-4">{children}</div>
    </section>
  );
}

export default function SettingsWorkspace() {
  const {
    theme,
    setTheme,
    motion: motionMode,
    setMotion,
    accent,
    setAccent,
    simulationMode,
    setSimulationMode,
    predictionInterval,
    setPredictionInterval,
  } = useSettings();
  const [provider, setProvider] = useState(providers[0]);

  const themeOptions: Array<{ id: ThemeMode; label: string }> = [
    { id: "dark", label: "◐ Dark Prism" },
    { id: "light", label: "◑ Daylight" },
  ];

  const motionOptions: Array<{ id: MotionMode; label: string }> = [
    { id: "full", label: "Full Motion" },
    { id: "reduced", label: "Reduced Motion" },
  ];

  const simulationOptions: Array<{ id: SimulationMode; label: string }> = [
    { id: "manual", label: "Manual" },
    { id: "autonomous", label: "Autonomous" },
  ];

  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-10 pt-3 sm:px-5 lg:px-8">
      <div className="relative w-full overflow-hidden rounded-[2.2rem] bg-[linear-gradient(170deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012))] p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07),0_28px_90px_rgba(0,0,0,0.58)] backdrop-blur-[16px] sm:p-6 lg:p-7">
        <div className="mb-5">
          <p className="text-[0.56rem] uppercase tracking-[0.24em] text-white/46">Settings</p>
          <h1 className="mt-1 bg-gradient-to-r from-white to-white/72 bg-clip-text text-[1.3rem] font-medium tracking-tight text-transparent">System Preferences</h1>
          <p className="mt-1.5 text-[0.76rem] text-white/44">Every control here takes effect immediately, everywhere in the app.</p>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <SectionCard title="Appearance" hint="Theme and accent apply instantly across every page.">
            <p className="text-[0.52rem] uppercase tracking-[0.16em] text-white/38">Theme</p>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {themeOptions.map((option) => (
                <OptionPill key={option.id} label={option.label} active={theme === option.id} onClick={() => setTheme(option.id)} />
              ))}
            </div>

            <p className="mt-4 text-[0.52rem] uppercase tracking-[0.16em] text-white/38">Accent</p>
            <div className="mt-2 flex items-center gap-3">
              {ACCENT_OPTIONS.map((option) => {
                const isActive = accent === option.id;
                return (
                  <motion.button
                    key={option.id}
                    type="button"
                    onClick={() => setAccent(option.id)}
                    whileHover={{ scale: 1.08 }}
                    whileTap={{ scale: 0.94 }}
                    aria-label={option.label}
                    aria-pressed={isActive}
                    className="flex flex-col items-center gap-1.5"
                  >
                    <span
                      className="h-7 w-7 rounded-full transition-shadow duration-300"
                      style={{
                        background: `rgb(${option.rgb})`,
                        boxShadow: isActive ? `0 0 0 2px #0d0920, 0 0 0 4px rgb(${option.rgb}), 0 0 16px rgba(${option.rgb},0.7)` : "0 0 0 2px transparent",
                      }}
                    />
                    <span className={`text-[0.48rem] uppercase tracking-[0.08em] ${isActive ? "text-white/80" : "text-white/38"}`}>
                      {option.label.split(" ")[0]}
                    </span>
                  </motion.button>
                );
              })}
            </div>
          </SectionCard>

          <SectionCard title="Motion" hint="Reduced motion simplifies or removes animation across the entire interface.">
            <div className="grid grid-cols-2 gap-2">
              {motionOptions.map((option) => (
                <OptionPill key={option.id} label={option.label} active={motionMode === option.id} onClick={() => setMotion(option.id)} />
              ))}
            </div>
          </SectionCard>

          <SectionCard title="Prediction Interval" hint="Controls how often live telemetry (temperatures, GPU load, confidence) drifts.">
            <div className="grid grid-cols-3 gap-2">
              {PREDICTION_INTERVAL_OPTIONS.map((option) => (
                <OptionPill key={option.id} label={option.label} active={predictionInterval === option.id} onClick={() => setPredictionInterval(option.id)} />
              ))}
            </div>
          </SectionCard>

          <SectionCard
            title="Simulation Mode"
            hint={
              simulationMode === "autonomous"
                ? "The cluster is driving itself — scenarios will change on their own, cadence tied to the interval above."
                : "The cluster only changes when you trigger a scenario, from the dock, copilot, or command palette."
            }
          >
            <div className="grid grid-cols-2 gap-2">
              {simulationOptions.map((option) => (
                <OptionPill key={option.id} label={option.label} active={simulationMode === option.id} onClick={() => setSimulationMode(option.id)} />
              ))}
            </div>
          </SectionCard>

          <SectionCard title="AI Provider">
            <select
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
              className="w-full rounded-lg bg-white/[0.04] px-3 py-2 text-sm text-white/82 outline-none transition focus:bg-white/[0.07]"
            >
              {providers.map((value) => (
                <option key={value} className="bg-[#130c2d]">
                  {value}
                </option>
              ))}
            </select>
          </SectionCard>

          <SectionCard title="Keyboard Shortcuts">
            <div className="space-y-2 text-sm text-white/72">
              <div className="flex items-center justify-between">
                <span>Command Palette</span>
                <span className="rounded-md bg-white/[0.06] px-2 py-0.5 text-[0.68rem] text-white/60">Ctrl+K</span>
              </div>
              <div className="flex items-center justify-between">
                <span>Mission Control</span>
                <span className="rounded-md bg-white/[0.06] px-2 py-0.5 text-[0.68rem] text-white/60">G M</span>
              </div>
              <div className="flex items-center justify-between">
                <span>Digital Twin</span>
                <span className="rounded-md bg-white/[0.06] px-2 py-0.5 text-[0.68rem] text-white/60">G D</span>
              </div>
              <div className="flex items-center justify-between">
                <span>Analytics</span>
                <span className="rounded-md bg-white/[0.06] px-2 py-0.5 text-[0.68rem] text-white/60">G A</span>
              </div>
            </div>
          </SectionCard>
        </div>
      </div>
    </div>
  );
}
