import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type ThemeMode = "dark" | "light";
export type MotionMode = "full" | "reduced";
export type AccentId = "purple" | "indigo" | "cyan";
export type SimulationMode = "manual" | "autonomous";
export type PredictionIntervalId = "fast" | "normal" | "slow";

export type Settings = {
  theme: ThemeMode;
  motion: MotionMode;
  accent: AccentId;
  simulationMode: SimulationMode;
  predictionInterval: PredictionIntervalId;
};

export const ACCENT_OPTIONS: Array<{ id: AccentId; label: string; rgb: string }> = [
  { id: "purple", label: "Electric Purple", rgb: "167,139,250" },
  { id: "indigo", label: "Soft Indigo", rgb: "129,140,248" },
  { id: "cyan", label: "Cyan Pulse", rgb: "34,211,238" },
];

export const PREDICTION_INTERVAL_OPTIONS: Array<{ id: PredictionIntervalId; label: string; ms: number }> = [
  { id: "fast", label: "Fast · 2s", ms: 2200 },
  { id: "normal", label: "Normal · 4s", ms: 3800 },
  { id: "slow", label: "Slow · 7s", ms: 6500 },
];

const STORAGE_KEY = "neurocool-settings";

const DEFAULT_SETTINGS: Settings = {
  theme: "dark",
  motion: "full",
  accent: "purple",
  simulationMode: "manual",
  predictionInterval: "normal",
};

function loadSettings(): Settings {
  if (typeof window === "undefined") return DEFAULT_SETTINGS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<Settings>;
    return { ...DEFAULT_SETTINGS, ...parsed };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

type SettingsContextValue = Settings & {
  accentRgb: string;
  predictionIntervalMs: number;
  setTheme: (theme: ThemeMode) => void;
  setMotion: (motion: MotionMode) => void;
  setAccent: (accent: AccentId) => void;
  setSimulationMode: (mode: SimulationMode) => void;
  setPredictionInterval: (interval: PredictionIntervalId) => void;
};

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(loadSettings);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  }, [settings]);

  useEffect(() => {
    document.documentElement.classList.toggle("theme-light", settings.theme === "light");
  }, [settings.theme]);

  useEffect(() => {
    const rgb = ACCENT_OPTIONS.find((option) => option.id === settings.accent)?.rgb ?? ACCENT_OPTIONS[0].rgb;
    document.documentElement.style.setProperty("--accent-rgb", rgb);
  }, [settings.accent]);

  const value = useMemo<SettingsContextValue>(
    () => ({
      ...settings,
      accentRgb: ACCENT_OPTIONS.find((option) => option.id === settings.accent)?.rgb ?? ACCENT_OPTIONS[0].rgb,
      predictionIntervalMs: PREDICTION_INTERVAL_OPTIONS.find((option) => option.id === settings.predictionInterval)?.ms ?? 3800,
      setTheme: (theme) => setSettings((current) => ({ ...current, theme })),
      setMotion: (motion) => setSettings((current) => ({ ...current, motion })),
      setAccent: (accent) => setSettings((current) => ({ ...current, accent })),
      setSimulationMode: (simulationMode) => setSettings((current) => ({ ...current, simulationMode })),
      setPredictionInterval: (predictionInterval) => setSettings((current) => ({ ...current, predictionInterval })),
    }),
    [settings],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings(): SettingsContextValue {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error("useSettings must be used within a SettingsProvider");
  return ctx;
}
