import { useScenarioEngine } from "../scenario/ScenarioEngine";

/**
 * The one control that starts/pauses/resumes/resets the simulation's own
 * tick loop — distinct from the scenario dock (SimulationDock.tsx), which
 * controls *what* a running simulation is doing, not *whether* it's
 * running at all. Lives in WorkspaceNav so it's visible on every page, not
 * just Mission Control/Digital Twin.
 *
 * Deliberately subtle (matches the nav's existing pill/badge styling) per
 * the objective's "clear but subtle" requirement — this is not a modal or
 * a full-screen gate, just one more control among the nav's existing
 * status indicators. Always calls the real backend endpoints
 * (apiClient.start/pause/resume/resetSimulation, via ScenarioEngine) —
 * never fakes a state transition locally.
 */
export default function SimulationControl() {
  const { simulationStatus, isSimulationBusy, startSimulation, pauseSimulation, resumeSimulation, resetSimulation } =
    useScenarioEngine();

  // Null until the first WebSocket message arrives — render nothing rather
  // than a misleading "Start" button before we actually know the state.
  if (!simulationStatus) return null;

  const status = simulationStatus.status;

  const buttonClass =
    "inline-flex h-8 items-center justify-center rounded-full px-3 text-[0.56rem] uppercase tracking-[0.12em] transition duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] hover:-translate-y-[1px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(var(--accent-rgb),0.7)] disabled:pointer-events-none disabled:opacity-50";

  return (
    <div className="hidden items-center gap-1.5 md:inline-flex">
      {status === "idle" || status === "completed" || status === "error" ? (
        <button
          type="button"
          onClick={startSimulation}
          disabled={isSimulationBusy}
          className={`${buttonClass} border border-[rgba(var(--accent-rgb),0.4)] text-white`}
          style={{ background: "rgba(var(--accent-rgb),0.22)" }}
        >
          Start Simulation
        </button>
      ) : null}

      {status === "running" ? (
        <button
          type="button"
          onClick={pauseSimulation}
          disabled={isSimulationBusy}
          className={`${buttonClass} bg-white/[0.06] text-white/72 hover:bg-white/[0.12] hover:text-white`}
        >
          Pause
        </button>
      ) : null}

      {status === "paused" ? (
        <>
          <button
            type="button"
            onClick={resumeSimulation}
            disabled={isSimulationBusy}
            className={`${buttonClass} border border-[rgba(var(--accent-rgb),0.4)] text-white`}
            style={{ background: "rgba(var(--accent-rgb),0.22)" }}
          >
            Resume
          </button>
          <button
            type="button"
            onClick={resetSimulation}
            disabled={isSimulationBusy}
            className={`${buttonClass} bg-white/[0.06] text-white/72 hover:bg-white/[0.12] hover:text-white`}
          >
            Reset
          </button>
        </>
      ) : null}
    </div>
  );
}
