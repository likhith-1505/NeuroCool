import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ScenarioEngineProvider } from "./scenario/ScenarioEngine";
import { SettingsProvider } from "./settings/SettingsContext";
import "./index.css";

const container = document.getElementById("root");

if (!container) {
  throw new Error("Root element with id 'root' was not found.");
}

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <SettingsProvider>
        <ScenarioEngineProvider>
          <App />
        </ScenarioEngineProvider>
      </SettingsProvider>
    </BrowserRouter>
  </StrictMode>,
);
