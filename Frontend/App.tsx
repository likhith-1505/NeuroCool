import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import AppLayout from "./AppLayout";
import CommandPalette, { type CommandItem } from "./components/CommandPalette";
import SimulationDock, { type SimulationAction } from "./components/SimulationDock";
import WorkspaceNav from "./components/WorkspaceNav";
import MissionControlPage from "./MissionControlPage";
import AICopilotWorkspace from "./pages/AICopilotWorkspace";
import AnalyticsWorkspace from "./pages/AnalyticsWorkspace";
import DigitalTwinWorkspace from "./pages/DigitalTwinWorkspace";
import SettingsWorkspace from "./pages/SettingsWorkspace";

const commands: CommandItem[] = [
  { id: "open-mission", label: "Open Mission Control", hint: "Navigation" },
  { id: "open-digital-twin", label: "Open Digital Twin", hint: "Navigation" },
  { id: "open-analytics", label: "Open Analytics", hint: "Navigation" },
  { id: "open-ai-copilot", label: "Open AI Copilot", hint: "Navigation" },
  { id: "open-settings", label: "Open Settings", hint: "Navigation" },
  { id: "inject-training-job", label: "Inject Training Job", hint: "Simulation" },
  { id: "inject-thermal-spike", label: "Inject Thermal Spike", hint: "Simulation" },
  { id: "inject-cooling-failure", label: "Inject Cooling Failure", hint: "Simulation" },
  { id: "inject-power-surge", label: "Inject Power Surge", hint: "Simulation" },
  { id: "replay", label: "Replay Simulation", hint: "Simulation" },
  { id: "reset", label: "Reset Cluster", hint: "Simulation" },
  { id: "run-ai", label: "Run AI", hint: "Simulation" },
  { id: "search-rack", label: "Search Rack", hint: "Search" },
  { id: "search-job", label: "Search Job", hint: "Search" },
];

const actionLabels: Record<SimulationAction, string> = {
  "training-job": "Training Job",
  "inference-spike": "Inference Spike",
  "thermal-spike": "Thermal Spike",
  "cooling-failure": "Cooling Failure",
  "power-surge": "Power Surge",
  "run-ai": "Run AI",
  replay: "Replay",
  reset: "Reset",
};

function routeToPath(commandId: string): string | null {
  if (commandId === "open-mission") return "/mission-control";
  if (commandId === "open-digital-twin") return "/digital-twin";
  if (commandId === "open-analytics") return "/analytics";
  if (commandId === "open-ai-copilot") return "/ai-copilot";
  if (commandId === "open-settings") return "/settings";
  return null;
}

function commandToSimulationAction(commandId: string): SimulationAction | null {
  if (commandId === "inject-training-job") return "training-job";
  if (commandId === "inject-thermal-spike") return "thermal-spike";
  if (commandId === "inject-cooling-failure") return "cooling-failure";
  if (commandId === "inject-power-surge") return "power-surge";
  if (commandId === "run-ai") return "run-ai";
  if (commandId === "replay") return "replay";
  if (commandId === "reset") return "reset";
  return null;
}

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();

  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [simulationPulse, setSimulationPulse] = useState(0);
  const [lastActionLabel, setLastActionLabel] = useState("");

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

  useEffect(() => {
    document.documentElement.classList.toggle("theme-light", theme === "light");
  }, [theme]);

  const clusterStatus = useMemo(() => {
    if (!lastActionLabel) return "Cluster Ready";
    return `Last Action: ${lastActionLabel}`;
  }, [lastActionLabel]);

  function triggerSimulation(action: SimulationAction) {
    setSimulationPulse((count) => count + 1);
    setLastActionLabel(actionLabels[action]);
  }

  function handleCommand(commandId: string) {
    const path = routeToPath(commandId);
    if (path) {
      navigate(path);
      setPaletteOpen(false);
      return;
    }

    const action = commandToSimulationAction(commandId);
    if (action) {
      triggerSimulation(action);
      setPaletteOpen(false);
      return;
    }

    if (commandId === "search-rack") {
      navigate("/digital-twin");
      setLastActionLabel("Search Rack");
      setPaletteOpen(false);
      return;
    }

    if (commandId === "search-job") {
      navigate("/ai-copilot");
      setLastActionLabel("Search Job");
      setPaletteOpen(false);
    }
  }

  const showDock = location.pathname === "/mission-control" || location.pathname === "/digital-twin";

  return (
    <AppLayout>
      <WorkspaceNav
        clusterStatus={clusterStatus}
        theme={theme}
        onToggleTheme={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
        onOpenPalette={() => setPaletteOpen(true)}
      />

      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={location.pathname}
          initial={{ opacity: 0, y: 12, filter: "blur(8px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={{ opacity: 0, y: -10, filter: "blur(6px)" }}
          transition={{ duration: 0.34, ease: [0.2, 0.8, 0.2, 1] }}
          className="min-h-[calc(100dvh-5rem)]"
        >
          <Routes location={location}>
            <Route path="/" element={<Navigate to="/mission-control" replace />} />
            <Route path="/mission-control" element={<MissionControlPage simulationPulse={simulationPulse} lastActionLabel={lastActionLabel} />} />
            <Route path="/digital-twin" element={<DigitalTwinWorkspace simulationPulse={simulationPulse} lastActionLabel={lastActionLabel} />} />
            <Route path="/ai-copilot" element={<AICopilotWorkspace lastActionLabel={lastActionLabel} />} />
            <Route path="/analytics" element={<AnalyticsWorkspace />} />
            <Route path="/settings" element={<SettingsWorkspace />} />
          </Routes>
        </motion.div>
      </AnimatePresence>

      {showDock ? <SimulationDock onAction={triggerSimulation} /> : null}

      <CommandPalette
        open={paletteOpen}
        commands={commands}
        onClose={() => setPaletteOpen(false)}
        onSelect={handleCommand}
      />
    </AppLayout>
  );
}
