import { useState } from "react";

const providers = ["Lukstack Native", "OpenAI", "Anthropic"];
const accents = ["Electric Purple", "Soft Indigo", "Cyan Pulse"];

export default function SettingsWorkspace() {
  const [motion, setMotion] = useState(true);
  const [predictionInterval, setPredictionInterval] = useState("15s");
  const [simulationMode, setSimulationMode] = useState("Adaptive");
  const [provider, setProvider] = useState(providers[0]);
  const [accent, setAccent] = useState(accents[0]);

  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-28 pt-3 sm:px-5 lg:px-8">
      <div className="relative w-full overflow-hidden rounded-[2.2rem] bg-[linear-gradient(170deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012))] p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07),0_28px_90px_rgba(0,0,0,0.58)] backdrop-blur-[16px] sm:p-6 lg:p-7">
        <div className="mb-4">
          <p className="text-[0.56rem] uppercase tracking-[0.24em] text-white/46">Settings</p>
          <h1 className="mt-1 text-[1.3rem] font-medium text-white">System Preferences</h1>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-[1.4rem] border border-white/10 bg-white/[0.03] p-4">
            <p className="text-[0.58rem] uppercase tracking-[0.2em] text-white/44">Appearance</p>
            <div className="mt-3 space-y-2">
              <button type="button" className="w-full rounded-lg bg-white/[0.04] px-3 py-2 text-left text-sm text-white/82">Theme: Dark Prism</button>
              <select value={accent} onChange={(event) => setAccent(event.target.value)} className="w-full rounded-lg bg-white/[0.04] px-3 py-2 text-sm text-white/82">
                {accents.map((value) => (
                  <option key={value} className="bg-[#130c2d]">{value}</option>
                ))}
              </select>
            </div>
          </section>

          <section className="rounded-[1.4rem] border border-white/10 bg-white/[0.03] p-4">
            <p className="text-[0.58rem] uppercase tracking-[0.2em] text-white/44">Motion</p>
            <div className="mt-3 flex items-center justify-between rounded-lg bg-white/[0.04] px-3 py-2">
              <span className="text-sm text-white/82">Enable Ambient Motion</span>
              <button
                type="button"
                onClick={() => setMotion((value) => !value)}
                className={`h-6 w-11 rounded-full transition ${motion ? "bg-violet-400/70" : "bg-white/20"}`}
              >
                <span className={`block h-5 w-5 rounded-full bg-white transition ${motion ? "translate-x-5" : "translate-x-0.5"}`} />
              </button>
            </div>
          </section>

          <section className="rounded-[1.4rem] border border-white/10 bg-white/[0.03] p-4">
            <p className="text-[0.58rem] uppercase tracking-[0.2em] text-white/44">Prediction Interval</p>
            <input
              value={predictionInterval}
              onChange={(event) => setPredictionInterval(event.target.value)}
              className="mt-3 w-full rounded-lg bg-white/[0.04] px-3 py-2 text-sm text-white/82"
            />
          </section>

          <section className="rounded-[1.4rem] border border-white/10 bg-white/[0.03] p-4">
            <p className="text-[0.58rem] uppercase tracking-[0.2em] text-white/44">Simulation Mode</p>
            <input
              value={simulationMode}
              onChange={(event) => setSimulationMode(event.target.value)}
              className="mt-3 w-full rounded-lg bg-white/[0.04] px-3 py-2 text-sm text-white/82"
            />
          </section>

          <section className="rounded-[1.4rem] border border-white/10 bg-white/[0.03] p-4">
            <p className="text-[0.58rem] uppercase tracking-[0.2em] text-white/44">AI Provider</p>
            <select value={provider} onChange={(event) => setProvider(event.target.value)} className="mt-3 w-full rounded-lg bg-white/[0.04] px-3 py-2 text-sm text-white/82">
              {providers.map((value) => (
                <option key={value} className="bg-[#130c2d]">{value}</option>
              ))}
            </select>
          </section>

          <section className="rounded-[1.4rem] border border-white/10 bg-white/[0.03] p-4">
            <p className="text-[0.58rem] uppercase tracking-[0.2em] text-white/44">Keyboard Shortcuts</p>
            <div className="mt-3 space-y-2 text-sm text-white/78">
              <p>Ctrl+K — Command Palette</p>
              <p>G M — Mission Control</p>
              <p>G D — Digital Twin</p>
              <p>G A — Analytics</p>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
