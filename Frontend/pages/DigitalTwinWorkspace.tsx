import { AnimatePresence, motion, useMotionValue } from "framer-motion";
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import AnimatedValue from "../components/AnimatedValue";
import LoadingState from "../components/LoadingState";
import { nearestForecastPoint, SCENARIOS, mostAffectedRackId, useScenarioEngine, type ScenarioRack } from "../scenario/ScenarioEngine";

type TwinRack = ScenarioRack;

type HealthState = "healthy" | "warning" | "critical";
type Tone = { ring: string; glow: string; aura: string; heat: string; core: string; label: string };
type Camera = { scale: number; x: number; y: number };
type LiveDrag = { id: string; x: number; y: number };
type Position = { x: number; y: number };

const DEFAULT_CAMERA: Camera = { scale: 1, x: 0, y: 0 };

/** Real racks have no backend notion of physical adjacency (unlike the
 * original hardcoded r1/r2/r3/r4 link table, which no longer matches real
 * rack ids) — nearest-neighbor-by-position is a reasonable, stable stand-
 * in, computed once per rack-id-set rather than invented telemetry.
 */
function computeDefaultLinks(racks: TwinRack[]): Array<[string, string]> {
  const dedupe = new Set<string>();
  const links: Array<[string, string]> = [];
  racks.forEach((rack) => {
    const nearest = racks
      .filter((candidate) => candidate.id !== rack.id)
      .map((candidate) => ({ id: candidate.id, distance: Math.hypot(rack.x - candidate.x, rack.y - candidate.y) }))
      .sort((a, b) => a.distance - b.distance)
      .slice(0, Math.min(2, Math.max(1, racks.length - 1)));
    nearest.forEach(({ id }) => {
      const key = [rack.id, id].sort().join(":");
      if (dedupe.has(key)) return;
      dedupe.add(key);
      links.push([rack.id, id]);
    });
  });
  return links;
}

const HEALTH_TONE: Record<HealthState, Tone> = {
  healthy: {
    ring: "rgba(163,126,255,0.94)",
    glow: "rgba(149,114,255,0.32)",
    aura: "rgba(146,108,255,0.2)",
    heat: "rgba(130,150,255,0.22)",
    core: "rgba(198,175,255,0.92)",
    label: "Healthy",
  },
  warning: {
    ring: "rgba(255,190,102,0.95)",
    glow: "rgba(255,180,90,0.36)",
    aura: "rgba(255,190,110,0.22)",
    heat: "rgba(255,176,90,0.3)",
    core: "rgba(255,214,164,0.94)",
    label: "Watch",
  },
  critical: {
    ring: "rgba(255,110,148,0.96)",
    glow: "rgba(255,110,148,0.4)",
    aura: "rgba(255,120,155,0.26)",
    heat: "rgba(255,104,140,0.4)",
    core: "rgba(255,175,192,0.95)",
    label: "Critical",
  },
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

// Every stat below reads a real backend field directly (health_score,
// cooling_efficiency) or the nearest real forecast point (predicted_risk,
// confidence) — see ScenarioEngine's mapping — rather than re-deriving a
// synthetic value from temperature the way the original mock did.

function healthState(rack: TwinRack): HealthState {
  return rack.healthState;
}

function predictionConfidence(rack: TwinRack): number {
  const point = nearestForecastPoint(rack.forecast);
  return point ? Math.round(point.confidence) : Math.round(rack.healthScore);
}

function thermalHealth(rack: TwinRack): number {
  return clamp(Math.round(rack.healthScore), 0, 100);
}

function coolingEfficiency(rack: TwinRack): number {
  return clamp(Math.round(rack.coolingEfficiency), 0, 100);
}

function estimatedRisk(rack: TwinRack): number {
  const point = nearestForecastPoint(rack.forecast);
  if (point) return clamp(Math.round(point.predicted_risk), 0, 100);
  // No forecast yet (e.g. the very first tick) — fall back to the same
  // three-tier bucket the backend itself uses for prediction_state.
  if (rack.healthState === "critical") return 85;
  if (rack.healthState === "warning") return 50;
  return 12;
}

function riskLabel(risk: number): string {
  if (risk > 65) return "High";
  if (risk > 32) return "Moderate";
  return "Low";
}

type TwinNodeProps = {
  rack: TwinRack;
  focused: boolean;
  cameraScale: number;
  containerRef: React.RefObject<HTMLDivElement | null>;
  onSelect: (id: string) => void;
  onDragLive: (id: string, x: number, y: number) => void;
  onDragCommit: (id: string, x: number, y: number) => void;
};

const TwinNode = memo(function TwinNode({ rack, focused, cameraScale, containerRef, onSelect, onDragLive, onDragCommit }: TwinNodeProps) {
  const tone = HEALTH_TONE[healthState(rack)];
  const gpu = clamp(rack.gpu, 0, 100);
  const dragX = useMotionValue(0);
  const dragY = useMotionValue(0);
  const dragOrigin = useRef<{ x: number; y: number } | null>(null);
  const dragDistance = useRef(0);
  const btnRef = useRef<HTMLButtonElement>(null);
  const [magnet, setMagnet] = useState({ x: 0, y: 0, rx: 0, ry: 0, sheenX: 50, sheenY: 38 });
  const [hovering, setHovering] = useState(false);
  const [rippleKey, setRippleKey] = useState(0);
  const pointerFrame = useRef<number | null>(null);
  const latestPointer = useRef({ x: 0, y: 0 });

  useLayoutEffect(() => {
    dragX.set(0);
    dragY.set(0);
  }, [rack.x, rack.y, dragX, dragY]);

  useEffect(() => {
    return () => {
      if (pointerFrame.current != null) cancelAnimationFrame(pointerFrame.current);
    };
  }, []);

  function computePercent(offsetX: number, offsetY: number) {
    const bounds = containerRef.current?.getBoundingClientRect();
    const origin = dragOrigin.current;
    if (!bounds || !origin) return null;
    const localOffsetX = offsetX / cameraScale;
    const localOffsetY = offsetY / cameraScale;
    const nextX = ((origin.x / 100) * bounds.width + localOffsetX) / bounds.width;
    const nextY = ((origin.y / 100) * bounds.height + localOffsetY) / bounds.height;
    return { x: clamp(nextX * 100, 8, 92), y: clamp(nextY * 100, 10, 90) };
  }

  const computePercentRef = useRef(computePercent);
  computePercentRef.current = computePercent;

  // Drives the connecting-edge preview during both the active drag gesture and the
  // momentum/inertia coast that follows release, so lines never desync from the node.
  useEffect(() => {
    const unsubX = dragX.on("change", () => {
      if (!dragOrigin.current) return;
      const next = computePercentRef.current(dragX.get(), dragY.get());
      if (next) onDragLive(rack.id, next.x, next.y);
    });
    return () => unsubX();
  }, [dragX, dragY, onDragLive, rack.id]);

  function handleMouseMove(event: React.MouseEvent<HTMLButtonElement>) {
    latestPointer.current = { x: event.clientX, y: event.clientY };
    if (pointerFrame.current != null) return;
    pointerFrame.current = requestAnimationFrame(() => {
      pointerFrame.current = null;
      const rect = btnRef.current?.getBoundingClientRect();
      if (!rect) return;
      const relX = (latestPointer.current.x - rect.left) / rect.width - 0.5;
      const relY = (latestPointer.current.y - rect.top) / rect.height - 0.5;
      setMagnet({
        x: relX * 7,
        y: relY * 7,
        rx: clamp(-relY * 16, -10, 10),
        ry: clamp(relX * 16, -10, 10),
        sheenX: (relX + 0.5) * 100,
        sheenY: (relY + 0.5) * 100,
      });
    });
  }

  return (
    <div
      style={{ left: `${rack.x}%`, top: `${rack.y}%` }}
      className={`absolute -translate-x-1/2 -translate-y-1/2 ${focused ? "z-30" : "z-10"}`}
    >
      <motion.div
        drag
        dragMomentum
        dragTransition={{ power: 0.2, timeConstant: 180, restDelta: 0.4 }}
        dragElastic={0.05}
        style={{ x: dragX, y: dragY, perspective: 700 }}
        onDragStart={() => {
          dragOrigin.current = { x: rack.x, y: rack.y };
          dragDistance.current = 0;
        }}
        onDrag={(_, info) => {
          dragDistance.current = Math.hypot(info.offset.x, info.offset.y);
        }}
        onDragTransitionEnd={() => {
          const next = computePercentRef.current(dragX.get(), dragY.get());
          dragOrigin.current = null;
          if (next) onDragCommit(rack.id, next.x, next.y);
        }}
        className="relative"
      >
        <motion.button
        ref={btnRef}
        type="button"
        onClick={() => {
          if (dragDistance.current > 4) {
            dragDistance.current = 0;
            return;
          }
          onSelect(rack.id);
        }}
        onMouseMove={handleMouseMove}
        onMouseEnter={() => {
          setHovering(true);
          setRippleKey((key) => key + 1);
        }}
        onMouseLeave={() => {
          setHovering(false);
          setMagnet((current) => ({ ...current, x: 0, y: 0, rx: 0, ry: 0 }));
        }}
        animate={{
          x: magnet.x,
          y: magnet.y,
          rotateX: hovering ? magnet.rx : 0,
          rotateY: hovering ? magnet.ry : 0,
          scale: focused ? 1.08 : hovering ? 1.07 : 1,
          filter: hovering ? "brightness(1.1)" : "brightness(1)",
        }}
        whileTap={{ scale: focused ? 1.05 : 1.02 }}
        transition={{ type: "spring", stiffness: 200, damping: 28, mass: 1 }}
        style={{
          boxShadow: focused
            ? `0 0 0 1px rgba(255,255,255,0.3), 0 0 58px ${tone.glow}`
            : `0 0 0 1px rgba(255,255,255,0.1), 0 0 24px ${tone.glow}`,
          transformStyle: "preserve-3d",
        }}
        className="group relative h-28 w-28 cursor-pointer rounded-full border border-white/14 bg-[radial-gradient(circle_at_30%_22%,rgba(255,255,255,0.26),rgba(255,255,255,0.04)_38%,rgba(14,10,32,0.96)_70%)] backdrop-blur-xl outline-none focus-visible:ring-2 focus-visible:ring-violet-300/70"
        aria-pressed={focused}
        aria-label={`${rack.name}, ${rack.temperature.toFixed(1)} degrees, GPU ${gpu}%, ${tone.label}`}
      >
        <motion.span
          className="pointer-events-none absolute inset-0 rounded-full"
          style={{
            background: `radial-gradient(circle at ${magnet.sheenX}% ${magnet.sheenY}%, rgba(255,255,255,0.5), rgba(255,255,255,0) 46%)`,
            mixBlendMode: "screen",
          }}
          animate={{ opacity: hovering ? 0.9 : 0 }}
          transition={{ duration: 0.25, ease: "easeOut" }}
        />

        <span
          className="pointer-events-none absolute -inset-2 rounded-full blur-xl"
          style={{ background: `radial-gradient(circle, ${tone.aura} 0%, rgba(0,0,0,0) 72%)` }}
        />

        {healthState(rack) !== "healthy" ? (
          <span
            className="pointer-events-none absolute -inset-4 rounded-full blur-lg"
            style={{
              background: `radial-gradient(circle, ${tone.aura} 0%, rgba(0,0,0,0) 70%)`,
              animation: "twin-heat-halo 2.8s ease-in-out infinite",
            }}
          />
        ) : null}

        <AnimatePresence>
          {hovering ? (
            <motion.span
              key={rippleKey}
              className="pointer-events-none absolute inset-0 rounded-full"
              style={{ border: `1px solid ${tone.ring}` }}
              initial={{ scale: 0.86, opacity: 0.5 }}
              animate={{ scale: 1.85, opacity: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.85, ease: "easeOut" }}
            />
          ) : null}
        </AnimatePresence>

        <span className="absolute inset-[4px] rounded-full" style={{ border: `1px solid ${tone.ring}` }} />

        <span
          className="absolute inset-[8px] rounded-full"
          style={{ border: `1px dashed ${tone.ring}`, opacity: 0.85, animation: "twin-ring-spin 9s linear infinite" }}
        />

        <span
          className="absolute inset-[12px] rounded-full"
          style={{
            background: `conic-gradient(from -90deg, rgba(112,206,255,0.95) 0%, rgba(112,206,255,0.95) ${gpu}%, rgba(255,255,255,0.08) ${gpu}%, rgba(255,255,255,0.08) 100%)`,
            WebkitMask: "radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 3px))",
            mask: "radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 3px))",
          }}
        />

        <span
          className="absolute inset-[19px] rounded-full"
          style={{ background: `radial-gradient(circle, ${tone.core} 0%, rgba(124,96,255,0.12) 52%, rgba(124,96,255,0) 78%)` }}
        />

        <span
          className="pointer-events-none absolute inset-[24px] rounded-full"
          style={{
            background: `radial-gradient(circle, ${tone.heat} 0%, rgba(0,0,0,0) 74%)`,
            animation: rack.temperature > 82 ? "twin-breathe 1.4s ease-in-out infinite" : "twin-breathe 3.6s ease-in-out infinite",
          }}
        />

        <div className="relative z-10 flex flex-col items-center">
          <span className="text-[0.5rem] uppercase tracking-[0.18em] text-white/72">{rack.name}</span>
          <span className="mt-0.5 bg-gradient-to-b from-white to-white/72 bg-clip-text text-[1.26rem] font-semibold leading-none tracking-tight text-transparent">
            {rack.temperature.toFixed(1)}°
          </span>
          <span className="mt-1 text-[0.48rem] tracking-[0.12em] text-white/58">GPU {gpu}%</span>
        </div>

        <span
          className="absolute right-4 top-4 h-2 w-2 rounded-full"
          style={{ background: tone.ring, boxShadow: `0 0 10px ${tone.ring}` }}
        />

        {focused ? (
          <span className="absolute -bottom-10 left-1/2 min-w-[7rem] -translate-x-1/2 rounded-lg border border-white/14 bg-[#0d0920]/92 px-2.5 py-1.5 text-center backdrop-blur-xl">
            <span className="block text-[0.52rem] uppercase tracking-[0.14em]" style={{ color: tone.ring }}>
              {tone.label}
            </span>
            <span className="block text-[0.5rem] text-white/62">{rack.prediction}</span>
          </span>
        ) : null}
        </motion.button>

        {focused ? (
          <span
            className="pointer-events-none absolute -inset-5 rounded-full"
            style={{
              background: `radial-gradient(circle, ${tone.aura} 0%, rgba(0,0,0,0) 70%)`,
              filter: "blur(10px)",
              animation: "twin-halo-pulse 2.4s ease-in-out infinite",
            }}
          />
        ) : null}
      </motion.div>
    </div>
  );
});

function MeterRow({ label, value, progress, tone }: { label: string; value: string; progress: number; tone: string }) {
  return (
    <div className="rounded-lg bg-white/[0.028] p-2.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[0.46rem] uppercase tracking-[0.13em] text-white/42">{label}</p>
        <p className="text-[0.66rem] font-medium text-white/78">
          <AnimatedValue value={value} />
        </p>
      </div>
      <div className="mt-1.5 h-1 rounded-full bg-white/[0.08]">
        <motion.div
          className="h-full rounded-full"
          style={{ background: tone }}
          initial={false}
          animate={{ width: `${clamp(progress, 0, 100)}%` }}
          transition={{ type: "spring", stiffness: 120, damping: 22 }}
        />
      </div>
    </div>
  );
}

function HeroStat({
  label,
  value,
  caption,
  progress,
  tone,
  valueColor,
}: {
  label: string;
  value: string;
  caption?: string;
  progress: number;
  tone: string;
  valueColor?: string;
}) {
  return (
    <div className="rounded-xl bg-white/[0.045] p-3.5 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.05)]">
      <p className="text-[0.52rem] uppercase tracking-[0.16em] text-white/46">{label}</p>
      <div className="mt-2 flex items-end gap-1.5">
        <span className="text-[1.65rem] font-semibold leading-none tracking-tight" style={{ color: valueColor ?? "#ffffff" }}>
          <AnimatedValue value={value} />
        </span>
        {caption ? (
          <span className="pb-0.5 text-[0.52rem] uppercase tracking-[0.12em] text-white/44">
            <AnimatedValue value={caption} />
          </span>
        ) : null}
      </div>
      <div className="mt-2.5 h-1.5 rounded-full bg-white/10">
        <motion.div
          className="h-full rounded-full"
          style={{ background: tone }}
          initial={false}
          animate={{ width: `${clamp(progress, 0, 100)}%` }}
          transition={{ type: "spring", stiffness: 120, damping: 22 }}
        />
      </div>
    </div>
  );
}

function riskColor(risk: number): string {
  if (risk > 65) return "rgba(255,120,150,0.95)";
  if (risk > 32) return "rgba(255,190,102,0.95)";
  return "rgba(140,220,190,0.95)";
}

export default function DigitalTwinWorkspace() {
  const { isLoading } = useScenarioEngine();
  // Hooks below assume at least one rack — split into a child component
  // (mounted only once real telemetry has arrived) rather than an early
  // return mid-function, which would otherwise call a different number of
  // hooks between the loading and loaded renders.
  if (isLoading) return <LoadingState />;
  return <DigitalTwinCanvas />;
}

function DigitalTwinCanvas() {
  const { racks: engineRacks, scenario, pulseKey, resetToken } = useScenarioEngine();
  const containerRef = useRef<HTMLDivElement>(null);
  const [dragOverrides, setDragOverrides] = useState<Record<string, Position>>({});
  const [focusedRackId, setFocusedRackId] = useState<string | null>(null);
  const [liveDrag, setLiveDrag] = useState<LiveDrag | null>(null);
  const [camera, setCamera] = useState<Camera>(DEFAULT_CAMERA);
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });

  const racks = useMemo(
    () => engineRacks.map((rack) => (dragOverrides[rack.id] ? { ...rack, ...dragOverrides[rack.id] } : rack)),
    [engineRacks, dragOverrides],
  );

  useEffect(() => {
    if (!resetToken) return;
    setDragOverrides({});
    setCamera(DEFAULT_CAMERA);
    setFocusedRackId(null);
  }, [resetToken]);

  useEffect(() => {
    if (!pulseKey) return;
    if (scenario !== "thermal_spike" && scenario !== "cooling_failure") return;
    setFocusedRackId(mostAffectedRackId(engineRacks));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pulseKey]);

  const links = useMemo(() => computeDefaultLinks(racks), [racks]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) setContainerSize({ width: rect.width, height: rect.height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    function handleWheel(event: WheelEvent) {
      event.preventDefault();
      setCamera((cam) => ({ ...cam, scale: clamp(cam.scale - event.deltaY * 0.0016, 0.7, 2.3) }));
    }
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, []);

  useEffect(() => {
    if (!focusedRackId || !containerSize.width || !containerSize.height) return;
    const rack = racks.find((candidate) => candidate.id === focusedRackId);
    if (!rack) return;
    const targetScale = 1.18;
    const dx = ((50 - rack.x) / 100) * containerSize.width * targetScale;
    const dy = ((50 - rack.y) / 100) * containerSize.height * targetScale;
    setCamera({ scale: targetScale, x: dx, y: dy });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedRackId, containerSize.width, containerSize.height]);

  const focusedRack = racks.find((rack) => rack.id === focusedRackId) ?? racks[0];
  const focusedTone = HEALTH_TONE[healthState(focusedRack)];
  const risk = estimatedRisk(focusedRack);
  const rackMap = useMemo(() => new Map(racks.map((rack) => [rack.id, rack])), [racks]);

  const positionFor = useCallback(
    (id: string) => {
      if (liveDrag && liveDrag.id === id) return { x: liveDrag.x, y: liveDrag.y };
      const rack = rackMap.get(id);
      return rack ? { x: rack.x, y: rack.y } : null;
    },
    [liveDrag, rackMap],
  );

  const handleSelect = useCallback((id: string) => {
    setFocusedRackId(id);
  }, []);

  const handleDragLive = useCallback((id: string, x: number, y: number) => {
    setLiveDrag({ id, x, y });
  }, []);

  const handleDragCommit = useCallback((id: string, x: number, y: number) => {
    setLiveDrag(null);
    setDragOverrides((current) => ({ ...current, [id]: { x, y } }));
  }, []);

  const handleResetView = useCallback(() => {
    setCamera(DEFAULT_CAMERA);
    setFocusedRackId(null);
  }, []);

  const handlePanStart = useCallback(
    (event: React.PointerEvent) => {
      const startX = event.clientX;
      const startY = event.clientY;
      const originX = camera.x;
      const originY = camera.y;
      let frame: number | null = null;
      let latestX = startX;
      let latestY = startY;

      function handleMove(moveEvent: PointerEvent) {
        latestX = moveEvent.clientX;
        latestY = moveEvent.clientY;
        if (frame != null) return;
        frame = requestAnimationFrame(() => {
          frame = null;
          setCamera((cam) => ({ ...cam, x: originX + (latestX - startX), y: originY + (latestY - startY) }));
        });
      }

      function handleUp() {
        if (frame != null) cancelAnimationFrame(frame);
        window.removeEventListener("pointermove", handleMove);
        window.removeEventListener("pointerup", handleUp);
      }

      window.addEventListener("pointermove", handleMove);
      window.addEventListener("pointerup", handleUp);
    },
    [camera.x, camera.y],
  );

  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-36 pt-3 sm:px-5 lg:px-8">
      <style>{`
        @keyframes twin-energy-flow {
          from { stroke-dashoffset: 220; }
          to { stroke-dashoffset: 0; }
        }

        @keyframes twin-link-pulse {
          0%, 100% { opacity: 0.2; }
          50% { opacity: 0.58; }
        }

        @keyframes twin-ring-spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }

        @keyframes twin-breathe {
          0%, 100% { transform: scale(0.96); opacity: 0.45; }
          50% { transform: scale(1.06); opacity: 0.82; }
        }

        @keyframes twin-fog {
          0% { transform: translate3d(-3%, -1%, 0) scale(1); opacity: 0.22; }
          50% { transform: translate3d(2%, 2%, 0) scale(1.05); opacity: 0.36; }
          100% { transform: translate3d(-1%, 3%, 0) scale(1.02); opacity: 0.24; }
        }

        @keyframes twin-particle {
          0% { transform: translate3d(0, 0, 0) scale(0.9); opacity: 0.1; }
          50% { transform: translate3d(4px, -12px, 0) scale(1.06); opacity: 0.4; }
          100% { transform: translate3d(-3px, -22px, 0) scale(0.94); opacity: 0.12; }
        }

        @keyframes twin-drift {
          0% { transform: translate3d(-2%, -1%, 0) scale(1); }
          100% { transform: translate3d(3%, 2%, 0) scale(1.08); }
        }

        @keyframes twin-glow-pulse {
          0%, 100% { opacity: 0; }
          50% { opacity: 1; }
        }

        @keyframes twin-halo-pulse {
          0%, 100% { opacity: 0.5; transform: scale(0.97); }
          50% { opacity: 0.88; transform: scale(1.04); }
        }

        @keyframes twin-heat-halo {
          0%, 100% { opacity: 0.4; transform: scale(0.98); }
          50% { opacity: 0.72; transform: scale(1.05); }
        }
      `}</style>

      <div className="relative w-full overflow-hidden rounded-[2.2rem] bg-[linear-gradient(170deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012))] p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07),0_28px_90px_rgba(0,0,0,0.58)] backdrop-blur-[16px] sm:p-6 lg:p-7">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <p className="text-[0.56rem] uppercase tracking-[0.24em] text-white/44">Digital Twin</p>
            <h1 className="mt-1 bg-gradient-to-r from-white to-white/72 bg-clip-text text-[1.3rem] font-medium tracking-tight text-transparent">Interactive Cluster Field</h1>
          </div>
          <p className="hidden text-[0.52rem] uppercase tracking-[0.14em] text-white/34 sm:block">Scroll to zoom · Drag canvas to pan · Double-click to reset</p>
        </div>

        <div className="grid min-h-[max(24rem,calc(100dvh_-_30rem))] gap-4 xl:grid-cols-[1.7fr_1fr]">
          <div
            ref={containerRef}
            onDoubleClick={handleResetView}
            className="relative touch-none select-none overflow-hidden rounded-[1.6rem] bg-[radial-gradient(circle_at_20%_10%,rgba(146,93,255,0.28),transparent_40%),radial-gradient(circle_at_84%_18%,rgba(104,141,255,0.22),transparent_42%),linear-gradient(165deg,#070511,#0e0922_46%,#150d30)]"
          >
            {/* Dotted workspace grid — signals "manipulable simulation surface" and is
                what visually sets the Digital Twin apart from Mission Control's monitoring feed. */}
            <div
              className="pointer-events-none absolute inset-0 opacity-40 [mask-image:radial-gradient(circle_at_center,black,transparent_85%)]"
              style={{
                backgroundImage: "radial-gradient(rgba(196,178,255,0.5) 1px, transparent 1px)",
                backgroundSize: "26px 26px",
              }}
            />

            <div className="pointer-events-none absolute inset-0">
              <div className="absolute -left-24 -top-20 h-80 w-80 rounded-full bg-[radial-gradient(circle,rgba(167,126,255,0.22)_0%,rgba(167,126,255,0)_72%)] blur-3xl" style={{ animation: "twin-fog 20s ease-in-out infinite alternate" }} />
              <div className="absolute bottom-[-5rem] right-[-4rem] h-72 w-72 rounded-full bg-[radial-gradient(circle,rgba(116,153,255,0.2)_0%,rgba(116,153,255,0)_72%)] blur-3xl" style={{ animation: "twin-fog 26s ease-in-out infinite alternate-reverse" }} />
              <div className="absolute inset-0 opacity-70" style={{ background: "radial-gradient(circle at 30% 20%, rgba(150,100,255,0.14), transparent 55%)", animation: "twin-drift 32s ease-in-out infinite alternate" }} />
              <div
                className="absolute inset-0"
                style={{
                  background: `radial-gradient(circle at 52% 40%, ${SCENARIOS[scenario].tone.glow}, transparent 64%)`,
                  opacity: scenario === "normal" ? 0.3 : 0.5,
                  transition: "background 1.4s ease, opacity 1.4s ease",
                }}
              />
              {Array.from({ length: 38 }).map((_, index) => (
                <span
                  key={`twin-p-${index}`}
                  className="absolute rounded-full bg-violet-200/60 blur-[0.4px]"
                  style={{
                    left: `${((index * 19) % 97) + 1}%`,
                    top: `${((index * 23) % 97) + 1}%`,
                    width: `${1 + ((index * 7) % 3)}px`,
                    height: `${1 + ((index * 7) % 3)}px`,
                    animation: `twin-particle ${8 + (index % 8) * 1.4}s ease-in-out ${index * -0.6}s infinite`,
                  }}
                />
              ))}
            </div>

            <div
              className="absolute inset-0 cursor-grab active:cursor-grabbing"
              onPointerDown={handlePanStart}
            />

            <motion.div
              className="absolute inset-0"
              style={{ transformOrigin: "50% 50%" }}
              animate={{ scale: camera.scale, x: camera.x, y: camera.y }}
              transition={{ type: "spring", stiffness: 140, damping: 22, mass: 0.8 }}
            >
              <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
                <defs>
                  <linearGradient id="twin-link" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="rgba(170,141,255,0.08)" />
                    <stop offset="48%" stopColor="rgba(209,192,255,0.72)" />
                    <stop offset="100%" stopColor="rgba(120,180,255,0.08)" />
                  </linearGradient>
                </defs>

                {links.map(([fromId, toId], index) => {
                  const from = positionFor(fromId);
                  const to = positionFor(toId);
                  if (!from || !to) return null;

                  const mx = (from.x + to.x) / 2;
                  const my = (from.y + to.y) / 2;
                  const bend = (index % 2 === 0 ? 1 : -1) * 3.8;
                  const d = `M ${from.x} ${from.y} Q ${mx + bend} ${my - bend} ${to.x} ${to.y}`;
                  const linkedToFocused = fromId === focusedRackId || toId === focusedRackId;

                  return (
                    <g key={`${fromId}-${toId}`} style={{ filter: linkedToFocused ? "drop-shadow(0 0 3.5px rgba(191,170,255,0.55))" : undefined }}>
                      <path
                        d={d}
                        fill="none"
                        stroke="url(#twin-link)"
                        strokeWidth={linkedToFocused ? "0.34" : "0.24"}
                        opacity={linkedToFocused ? "0.52" : "0.28"}
                        style={{ animation: `twin-link-pulse ${2.5 + (index % 4) * 0.4}s ease-in-out ${index * -0.25}s infinite` }}
                      />
                      <path
                        d={d}
                        fill="none"
                        stroke="rgba(220,208,255,0.94)"
                        strokeWidth={linkedToFocused ? "0.13" : "0.09"}
                        strokeDasharray="2.6 6.1"
                        style={{ animation: `twin-energy-flow ${1.8 + (index % 4) * 0.45}s linear ${index * -0.3}s infinite` }}
                      />
                    </g>
                  );
                })}
              </svg>

              {racks.map((rack) => (
                <TwinNode
                  key={rack.id}
                  rack={rack}
                  focused={focusedRackId === rack.id}
                  cameraScale={camera.scale}
                  containerRef={containerRef}
                  onSelect={handleSelect}
                  onDragLive={handleDragLive}
                  onDragCommit={handleDragCommit}
                />
              ))}
            </motion.div>
          </div>

          <motion.aside
            key={focusedRack.id}
            initial={{ opacity: 0, x: 14 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.34, ease: [0.2, 0.8, 0.2, 1] }}
            className="flex flex-col rounded-[1.4rem] border border-white/10 bg-[linear-gradient(180deg,rgba(30,18,67,0.72),rgba(14,9,34,0.9))] p-4 backdrop-blur-xl"
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-[0.56rem] uppercase tracking-[0.2em] text-white/46">Rack Inspector</p>
                <h3 className="mt-1 text-[1.18rem] font-medium text-white">{focusedRack.name}</h3>
              </div>
              <span
                className="rounded-full border px-2.5 py-1 text-[0.5rem] uppercase tracking-[0.14em]"
                style={{ borderColor: focusedTone.ring, color: focusedTone.ring, background: focusedTone.aura }}
              >
                <AnimatedValue value={focusedTone.label} />
              </span>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2.5">
              <HeroStat
                label="Prediction Confidence"
                value={`${predictionConfidence(focusedRack)}`}
                caption="/100"
                progress={predictionConfidence(focusedRack)}
                tone="linear-gradient(90deg, rgba(173,140,255,0.95), rgba(132,173,255,0.9))"
              />
              <HeroStat
                label="Estimated Risk"
                value={riskLabel(risk)}
                caption={`${risk}/100`}
                progress={risk}
                tone={`linear-gradient(90deg, ${riskColor(risk)}, rgba(255,255,255,0.2))`}
                valueColor={riskColor(risk)}
              />
            </div>

            <div className="mt-5 flex items-center gap-2">
              <p className="text-[0.48rem] uppercase tracking-[0.22em] text-white/34">System Metrics</p>
              <span className="h-px flex-1 bg-white/[0.08]" />
            </div>

            <div className="mt-2.5 grid grid-cols-2 gap-2">
              <MeterRow
                label="Thermal Health"
                value={`${thermalHealth(focusedRack)}%`}
                progress={thermalHealth(focusedRack)}
                tone="linear-gradient(90deg, rgba(120,170,255,0.9), rgba(160,140,255,0.85))"
              />
              <MeterRow
                label="GPU Utilization"
                value={`${focusedRack.gpu}%`}
                progress={focusedRack.gpu}
                tone="linear-gradient(90deg, rgba(112,206,255,0.9), rgba(150,190,255,0.85))"
              />
              <MeterRow
                label="Cooling Efficiency"
                value={`${coolingEfficiency(focusedRack)}%`}
                progress={coolingEfficiency(focusedRack)}
                tone="linear-gradient(90deg, rgba(140,220,190,0.9), rgba(120,190,255,0.85))"
              />
              <MeterRow
                label="Active Jobs"
                value={`${focusedRack.jobs}`}
                progress={clamp(focusedRack.jobs * 2.4, 0, 100)}
                tone="linear-gradient(90deg, rgba(196,170,255,0.9), rgba(150,130,255,0.85))"
              />
            </div>

            <div
              className="relative mt-5 overflow-hidden rounded-2xl border p-4"
              style={{ borderColor: focusedTone.ring, background: `linear-gradient(160deg, ${focusedTone.aura}, rgba(10,7,24,0.62))` }}
            >
              <span
                className="pointer-events-none absolute inset-0 rounded-2xl"
                style={{ boxShadow: `0 0 32px ${focusedTone.glow}`, animation: "twin-glow-pulse 3.4s ease-in-out infinite" }}
              />
              <div className="flex items-center gap-2">
                <span
                  className="flex h-6 w-6 items-center justify-center rounded-full text-[0.56rem] font-semibold"
                  style={{ background: focusedTone.ring, color: "#0a0618" }}
                >
                  AI
                </span>
                <p className="text-[0.58rem] uppercase tracking-[0.18em] text-white/72">Recommendation</p>
              </div>
              <p className="mt-2.5 text-[0.94rem] font-medium leading-relaxed text-white">
                <AnimatedValue value={focusedRack.recommendation} />
              </p>
            </div>
          </motion.aside>
        </div>
      </div>
    </div>
  );
}
