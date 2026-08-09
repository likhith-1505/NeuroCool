import { AnimatePresence, motion, MotionConfig } from "framer-motion";
import { useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import AppLayout from "./AppLayout";
import CommandPalette, { type CommandItem } from "./components/CommandPalette";
import SimulationDock from "./components/SimulationDock";
import WorkspaceNav from "./components/WorkspaceNav";
import MissionControlPage from "./MissionControlPage";
import AICopilotWorkspace from "./pages/AICopilotWorkspace";
import AnalyticsWorkspace from "./pages/AnalyticsWorkspace";
import DigitalTwinWorkspace from "./pages/DigitalTwinWorkspace";
import SettingsWorkspace from "./pages/SettingsWorkspace";
import { SCENARIOS, useScenarioEngine, type ScenarioId } from "./scenario/ScenarioEngine";
import { useSettings } from "./settings/SettingsContext";
import { useTelemetry } from "./state/TelemetryContext";

const commands: CommandItem[] = [
  { id: "open-mission", label: "Open Mission Control", hint: "Navigation" },
  { id: "open-digital-twin", label: "Open Digital Twin", hint: "Navigation" },
  { id: "open-analytics", label: "Open Analytics", hint: "Navigation" },
  { id: "open-ai-copilot", label: "Open AI Copilot", hint: "Navigation" },
  { id: "open-settings", label: "Open Settings", hint: "Navigation" },
  { id: "scenario-normal", label: "Scenario: Normal", hint: "Scenario" },
  { id: "scenario-training-burst", label: "Scenario: Training Burst", hint: "Scenario" },
  { id: "scenario-thermal-spike", label: "Scenario: Thermal Spike", hint: "Scenario" },
  { id: "scenario-cooling-failure", label: "Scenario: Cooling Failure", hint: "Scenario" },
  { id: "scenario-power-surge", label: "Scenario: Power Surge", hint: "Scenario" },
  { id: "replay", label: "Replay Scenario Sequence", hint: "Scenario" },
  { id: "reset", label: "Reset Cluster", hint: "Scenario" },
  { id: "search-rack", label: "Search Rack", hint: "Search" },
  { id: "search-job", label: "Search Job", hint: "Search" },
];

function routeToPath(commandId: string): string | null {
  if (commandId === "open-mission") return "/mission-control";
  if (commandId === "open-digital-twin") return "/digital-twin";
  if (commandId === "open-analytics") return "/analytics";
  if (commandId === "open-ai-copilot") return "/ai-copilot";
  if (commandId === "open-settings") return "/settings";
  return null;
}

function commandToScenario(commandId: string): ScenarioId | null {
  if (commandId === "scenario-normal") return "normal";
  if (commandId === "scenario-training-burst") return "training_burst";
  if (commandId === "scenario-thermal-spike") return "thermal_spike";
  if (commandId === "scenario-cooling-failure") return "cooling_failure";
  if (commandId === "scenario-power-surge") return "power_surge";
  return null;
}

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const { scenario, selectScenario, triggerReplay, resetScenario } = useScenarioEngine();
  const { theme, setTheme, motion: motionMode } = useSettings();
  const { status: connectionStatus } = useTelemetry();

  const [paletteOpen, setPaletteOpen] = useState(false);
  const [lastSearchLabel, setLastSearchLabel] = useState("");

  useEffect(() => {
    function handleKeydown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    }

    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, []);

  const clusterStatus = useMemo(() => {
    if (lastSearchLabel) return lastSearchLabel;
    return `Scenario: ${SCENARIOS[scenario].label}`;
  }, [lastSearchLabel, scenario]);

  function handleCommand(commandId: string) {
    const path = routeToPath(commandId);
    if (path) {
      navigate(path);
      setPaletteOpen(false);
      return;
    }

    const scenarioId = commandToScenario(commandId);
    if (scenarioId) {
      setLastSearchLabel("");
      selectScenario(scenarioId);
      setPaletteOpen(false);
      return;
    }

    if (commandId === "replay") {
      setLastSearchLabel("");
      triggerReplay();
      setPaletteOpen(false);
      return;
    }

    if (commandId === "reset") {
      setLastSearchLabel("");
      resetScenario();
      setPaletteOpen(false);
      return;
    }

    if (commandId === "search-rack") {
      navigate("/digital-twin");
      setLastSearchLabel("Search: Rack");
      setPaletteOpen(false);
      return;
    }

    if (commandId === "search-job") {
      navigate("/ai-copilot");
      setLastSearchLabel("Search: Job");
      setPaletteOpen(false);
    }
  }

  const showDock = location.pathname === "/mission-control" || location.pathname === "/digital-twin";

  return (
    <MotionConfig reducedMotion={motionMode === "reduced" ? "always" : "never"}>
      <AppLayout>
        <WorkspaceNav
          clusterStatus={clusterStatus}
          theme={theme}
          connectionStatus={connectionStatus}
          onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
          onOpenPalette={() => setPaletteOpen(true)}
        />

        <AnimatePresence initial={false}>
          <motion.div
            key={location.pathname}
            initial={{ opacity: 0, y: 12, filter: "blur(8px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -10, filter: "blur(6px)" }}
            transition={{ duration: 0.34, ease: [0.2, 0.8, 0.2, 1] }}
            className="min-h-[calc(100dvh_-_5rem)]"
          >
            <Routes location={location}>
              <Route path="/" element={<Navigate to="/mission-control" replace />} />
              <Route path="/mission-control" element={<MissionControlPage />} />
              <Route path="/digital-twin" element={<DigitalTwinWorkspace />} />
              <Route path="/ai-copilot" element={<AICopilotWorkspace />} />
              <Route path="/analytics" element={<AnalyticsWorkspace />} />
              <Route path="/settings" element={<SettingsWorkspace />} />
            </Routes>
          </motion.div>
        </AnimatePresence>

        {showDock ? <SimulationDock /> : null}

        <CommandPalette
          open={paletteOpen}
          commands={commands}
          onClose={() => setPaletteOpen(false)}
          onSelect={handleCommand}
        />
      </AppLayout>
    </MotionConfig>
  );
}
