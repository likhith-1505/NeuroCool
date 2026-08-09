import { AnimatePresence, motion } from "framer-motion";
import { useMemo, useRef, useState } from "react";
import AnimatedValue from "../components/AnimatedValue";
import LoadingState from "../components/LoadingState";
import { SCENARIOS, useScenarioEngine } from "../scenario/ScenarioEngine";

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** This backend keeps no historical time-series (see
 * backend/app/forecasting/service.py's module docstring — forecasts are
 * always the latest computed snapshot, recomputed every tick). The only
 * genuinely multi-point real series it exposes is *forward-looking*: the
 * cluster/rack forecast horizons. Rather than fabricate a trailing
 * "history", every chart on this page plots that real forecast curve,
 * anchored at the real current reading (the one point that isn't a
 * prediction) — see the integration objective's "adapt the existing UI to
 * show live data rather than pretending historical data exists".
 */
function forecastSeries(current: number, predictions: Array<{ horizon_seconds: number; value: number }>): number[] {
  const sorted = [...predictions].sort((a, b) => a.horizon_seconds - b.horizon_seconds);
  const series = [current, ...sorted.map((point) => point.value)];
  // buildSmoothPath needs at least two points to divide the width by.
  return series.length >= 2 ? series : [current, current];
}

function buildSmoothPath(values: number[], width: number, height: number, min: number, max: number) {
  const stepX = width / (values.length - 1);
  const range = Math.max(max - min, 0.001);
  const points = values.map((value, index) => ({
    x: index * stepX,
    y: height - ((value - min) / range) * height,
  }));

  let line = `M ${points[0].x},${points[0].y}`;
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[i];
    const p1 = points[i + 1];
    const midX = (p0.x + p1.x) / 2;
    const midY = (p0.y + p1.y) / 2;
    line += ` Q ${p0.x},${p0.y} ${midX},${midY}`;
  }
  const last = points[points.length - 1];
  line += ` L ${last.x},${last.y}`;

  const area = `${line} L ${last.x},${height} L ${points[0].x},${height} Z`;
  return { line, area, points };
}

function heatColor(score: number): string {
  if (score >= 78) return `rgba(120,235,190,${0.28 + (score / 100) * 0.5})`;
  if (score >= 55) return `rgba(255,196,110,${0.32 + ((score - 55) / 23) * 0.4})`;
  return `rgba(255,116,140,${0.4 + ((55 - score) / 55) * 0.42})`;
}

const RING_SIZE = 140;
const RING_STROKE = 9;
const RING_RADIUS = (RING_SIZE - RING_STROKE) / 2;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

function RingStat({ label, value, display, tone, detail }: { label: string; value: number; display: string; tone: string; detail: string }) {
  const offset = RING_CIRCUMFERENCE * (1 - clamp(value, 0, 100) / 100);
  const [hovering, setHovering] = useState(false);

  return (
    <motion.section
      whileHover={{ y: -3 }}
      transition={{ type: "spring", stiffness: 260, damping: 22 }}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      className="relative flex flex-col items-center rounded-[1.8rem] border border-white/8 bg-[linear-gradient(165deg,rgba(22,14,52,0.78),rgba(10,8,28,0.86))] px-6 py-7 shadow-[0_18px_48px_rgba(0,0,0,0.32)]"
    >
      <div className="relative" style={{ width: RING_SIZE, height: RING_SIZE }}>
        <svg width={RING_SIZE} height={RING_SIZE} className="-rotate-90">
          <circle cx={RING_SIZE / 2} cy={RING_SIZE / 2} r={RING_RADIUS} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth={RING_STROKE} />
          <motion.circle
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={RING_RADIUS}
            fill="none"
            stroke={tone}
            strokeWidth={RING_STROKE}
            strokeLinecap="round"
            strokeDasharray={RING_CIRCUMFERENCE}
            initial={false}
            animate={{ strokeDashoffset: offset }}
            transition={{ type: "spring", stiffness: 70, damping: 20 }}
            style={{ filter: `drop-shadow(0 0 8px ${tone})` }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-[2.05rem] font-semibold tracking-tight text-white">
            <AnimatedValue value={display} />
          </span>
        </div>
      </div>
      <p className="mt-4 text-center text-[0.72rem] uppercase tracking-[0.22em] text-white/54">{label}</p>

      <AnimatePresence>
        {hovering ? (
          <motion.div
            initial={{ opacity: 0, y: 6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.97 }}
            transition={{ duration: 0.2, ease: [0.2, 0.8, 0.2, 1] }}
            className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 w-max max-w-[13rem] -translate-x-1/2 rounded-lg border border-white/12 bg-[#0d0920]/95 px-3 py-2 text-center shadow-[0_14px_32px_rgba(0,0,0,0.5)] backdrop-blur-xl"
          >
            <p className="text-[0.6rem] leading-snug text-white/72">{detail}</p>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </motion.section>
  );
}

export default function AnalyticsWorkspace() {
  const { scenario, metrics, ai, racks, cluster, clusterForecast, isLoading } = useScenarioEngine();
  const chartRef = useRef<HTMLDivElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const trend = useMemo(
    () => forecastSeries(metrics.avgTemperature, clusterForecast.map((p) => ({ horizon_seconds: p.horizon_seconds, value: p.predicted_temperature }))),
    [metrics.avgTemperature, clusterForecast],
  );
  const trendPath = useMemo(() => {
    const min = Math.min(...trend) - 2;
    const max = Math.max(...trend) + 2;
    return buildSmoothPath(trend, 100, 40, min, max);
  }, [trend]);

  // Cluster Efficiency: composite of two real fields (overall health,
  // cooling efficiency) — no backend "efficiency" field exists directly.
  const efficiencyScore = cluster ? clamp(cluster.overall_health * 0.6 + cluster.cooling_efficiency * 0.4, 0, 100) : 0;
  // Real fields, passed through directly — not derived/fabricated.
  const energySavings = metrics.energySaved;
  const predictionAccuracy = cluster ? clamp(cluster.prediction_confidence, 0, 100) : 0;

  const heatmapRows = useMemo(
    () =>
      racks.map((rack) => {
        const history = forecastSeries(rack.healthScore, rack.forecast.map((p) => ({ horizon_seconds: p.horizon_seconds, value: p.predicted_health }))).map(
          (value) => clamp(value, 0, 100),
        );
        return { rack, history };
      }),
    [racks],
  );

  if (isLoading) return <LoadingState />;

  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-10 pt-3 sm:px-5 lg:px-8">
      <div className="relative w-full overflow-hidden rounded-[2.2rem] bg-[linear-gradient(170deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012))] p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07),0_28px_90px_rgba(0,0,0,0.58)] backdrop-blur-[16px] sm:p-6 lg:p-8">
        <div className="mb-6 flex items-center justify-between gap-3">
          <div>
            <p className="text-[0.56rem] uppercase tracking-[0.24em] text-white/44">Analytics</p>
            <h1 className="mt-1 bg-gradient-to-r from-white to-white/72 bg-clip-text text-[1.6rem] font-medium tracking-tight text-transparent">Operational Health Story</h1>
          </div>
          <div className="flex items-center gap-2">
            <span className="hidden rounded-full border border-white/12 bg-white/[0.04] px-3 py-1.5 text-[0.56rem] uppercase tracking-[0.16em] text-white/56 sm:inline-flex">
              <AnimatedValue value={SCENARIOS[scenario].label} />
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5" style={{ borderColor: "rgba(var(--accent-rgb),0.18)", background: "rgba(var(--accent-rgb),0.08)" }}>
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full" style={{ background: "rgba(var(--accent-rgb),0.65)" }} />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full" style={{ background: "rgba(var(--accent-rgb),1)", boxShadow: "0 0 10px rgba(var(--accent-rgb),0.85)" }} />
              </span>
              <span className="text-[0.54rem] font-medium uppercase tracking-[0.16em] text-white/70">Live</span>
            </span>
          </div>
        </div>

        <div className="flex flex-col gap-6">
          {/* Hero: Thermal trend */}
          <motion.section
            whileHover={{ y: -2 }}
            transition={{ type: "spring", stiffness: 260, damping: 24 }}
            className="rounded-[1.9rem] border border-white/8 bg-[linear-gradient(165deg,rgba(22,14,52,0.82),rgba(10,8,28,0.88))] p-6 shadow-[0_22px_60px_rgba(0,0,0,0.36)] sm:p-8"
          >
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <p className="text-[0.62rem] uppercase tracking-[0.24em] text-white/48">Thermal Forecast</p>
                <div className="mt-2 flex items-end gap-2">
                  <span className="text-[2.8rem] font-semibold leading-none tracking-tight text-white">
                    <AnimatedValue value={`${metrics.avgTemperature.toFixed(1)}°C`} />
                  </span>
                  <span className="pb-1.5 text-[0.78rem] text-white/48">avg across cluster, now</span>
                </div>
              </div>
              <p className="max-w-[20rem] text-right text-[0.76rem] leading-relaxed text-white/50">Real predicted temperature at each forecast horizon, from the current reading.</p>
            </div>

            <div
              ref={chartRef}
              className="relative mt-6 h-56 w-full sm:h-64"
              onMouseMove={(event) => {
                const rect = chartRef.current?.getBoundingClientRect();
                if (!rect) return;
                const relX = clamp((event.clientX - rect.left) / rect.width, 0, 1);
                setHoverIndex(Math.round(relX * (trend.length - 1)));
              }}
              onMouseLeave={() => setHoverIndex(null)}
            >
              <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-full w-full overflow-visible">
                <defs>
                  <linearGradient id="thermal-area" x1="0%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" stopColor="rgba(179,149,255,0.55)" />
                    <stop offset="100%" stopColor="rgba(179,149,255,0)" />
                  </linearGradient>
                  <linearGradient id="thermal-line" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="rgba(179,149,255,0.5)" />
                    <stop offset="100%" stopColor="rgba(126,173,255,0.95)" />
                  </linearGradient>
                </defs>
                <motion.path key={`${scenario}-area`} d={trendPath.area} fill="url(#thermal-area)" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.6, delay: 0.3 }} />
                <motion.path
                  key={`${scenario}-line`}
                  d={trendPath.line}
                  fill="none"
                  stroke="url(#thermal-line)"
                  strokeWidth={0.6}
                  strokeLinecap="round"
                  initial={{ pathLength: 0 }}
                  animate={{ pathLength: 1 }}
                  transition={{ duration: 0.9, ease: [0.2, 0.8, 0.2, 1] }}
                />
                <motion.circle
                  cx={trendPath.points[trendPath.points.length - 1].x}
                  cy={trendPath.points[trendPath.points.length - 1].y}
                  r={1.3}
                  fill="#fff"
                  initial={{ opacity: 0, scale: 0 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.9, type: "spring", stiffness: 300, damping: 18 }}
                  style={{ filter: "drop-shadow(0 0 4px rgba(255,255,255,0.9))" }}
                />
                {hoverIndex != null ? (
                  <>
                    <line
                      x1={trendPath.points[hoverIndex].x}
                      x2={trendPath.points[hoverIndex].x}
                      y1={0}
                      y2={40}
                      stroke="rgba(255,255,255,0.22)"
                      strokeWidth={0.3}
                    />
                    <circle
                      cx={trendPath.points[hoverIndex].x}
                      cy={trendPath.points[hoverIndex].y}
                      r={1.1}
                      fill="rgba(var(--accent-rgb),1)"
                      style={{ filter: "drop-shadow(0 0 5px rgba(var(--accent-rgb),0.9))" }}
                    />
                  </>
                ) : null}
              </svg>

              <AnimatePresence>
                {hoverIndex != null ? (
                  <motion.div
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: 4 }}
                    transition={{ duration: 0.16, ease: [0.2, 0.8, 0.2, 1] }}
                    className="pointer-events-none absolute z-20 -translate-x-1/2 -translate-y-full rounded-lg border border-white/12 bg-[#0d0920]/95 px-2.5 py-1.5 text-center shadow-[0_10px_28px_rgba(0,0,0,0.5)] backdrop-blur-xl"
                    style={{
                      left: `${trendPath.points[hoverIndex].x}%`,
                      top: `${clamp((trendPath.points[hoverIndex].y / 40) * 100 - 4, 0, 100)}%`,
                    }}
                  >
                    <p className="text-[0.62rem] font-medium tabular-nums text-white/90">{trend[hoverIndex].toFixed(1)}°C</p>
                  </motion.div>
                ) : null}
              </AnimatePresence>
            </div>
          </motion.section>

          {/* Rings row */}
          <div className="grid gap-6 md:grid-cols-3">
            <RingStat
              label="Cluster Efficiency"
              value={efficiencyScore}
              display={`${efficiencyScore.toFixed(0)}%`}
              tone="rgba(179,149,255,0.95)"
              detail="Composite of overall cluster health and cooling efficiency — how well cooling is matched to thermal load right now."
            />
            <RingStat
              label="Energy Savings"
              value={energySavings}
              display={`${energySavings.toFixed(1)}%`}
              tone="rgba(120,235,190,0.95)"
              detail="Power saved versus a static, always-on baseline configuration."
            />
            <RingStat
              label="Prediction Accuracy"
              value={predictionAccuracy}
              display={`${predictionAccuracy.toFixed(0)}%`}
              tone="rgba(126,173,255,0.95)"
              detail="The forecasting engine's own confidence in its current cluster-wide prediction."
            />
          </div>

          {/* Heatmap + insight */}
          <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
            <motion.section
              whileHover={{ y: -2 }}
              transition={{ type: "spring", stiffness: 260, damping: 24 }}
              className="rounded-[1.8rem] border border-white/8 bg-[linear-gradient(165deg,rgba(22,14,52,0.78),rgba(10,8,28,0.86))] p-6 shadow-[0_18px_48px_rgba(0,0,0,0.32)] sm:p-7"
            >
              <p className="text-[0.62rem] uppercase tracking-[0.24em] text-white/48">Rack Health Forecast</p>
              <p className="mt-1 text-[0.78rem] text-white/50">Real predicted health score per rack, now through the forecast horizon — greener is cooler and steadier.</p>

              <div className="mt-5 space-y-2.5">
                {heatmapRows.map(({ rack, history }) => (
                  <div key={rack.id} className="flex items-center gap-3">
                    <p className="w-14 shrink-0 text-[0.68rem] font-medium text-white/72">{rack.name}</p>
                    <div className="grid flex-1 gap-1" style={{ gridTemplateColumns: `repeat(${history.length}, minmax(0, 1fr))` }}>
                      {history.map((score, index) => {
                        const cellScore = index === 0 ? Math.round(rack.healthScore) : Math.round(score);
                        return (
                          <motion.div
                            key={index}
                            initial={{ opacity: 0, scale: 0.6 }}
                            animate={{ opacity: 1, scale: 1 }}
                            whileHover={{ scale: 1.25, transition: { delay: 0, duration: 0.15 } }}
                            transition={{ delay: index * 0.02, duration: 0.3 }}
                            className="aspect-square rounded-[4px]"
                            style={{ background: heatColor(cellScore) }}
                            title={`${rack.name} · health ${cellScore}`}
                          />
                        );
                      })}
                    </div>
                    <span className="w-9 shrink-0 text-right text-[0.68rem] tabular-nums text-white/56">
                      <AnimatedValue value={`${Math.round(rack.healthScore)}`} />
                    </span>
                  </div>
                ))}
              </div>
            </motion.section>

            <motion.section
              whileHover={{ y: -2 }}
              transition={{ type: "spring", stiffness: 260, damping: 24 }}
              className="relative overflow-hidden rounded-[1.8rem] border border-violet-300/20 bg-[linear-gradient(160deg,rgba(146,108,255,0.14),rgba(10,7,24,0.68))] p-6 shadow-[0_18px_48px_rgba(0,0,0,0.32)] sm:p-7"
            >
              <span
                className="pointer-events-none absolute inset-0 rounded-[1.8rem]"
                style={{ boxShadow: `0 0 44px ${SCENARIOS[scenario].tone.glow}`, animation: "ribbon-glow-pulse 4s ease-in-out infinite" }}
              />
              <div className="relative z-10">
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-full text-[0.6rem] font-semibold" style={{ background: SCENARIOS[scenario].tone.ring, color: "#0a0618" }}>
                    AI
                  </span>
                  <p className="text-[0.62rem] uppercase tracking-[0.24em] text-white/58">AI Insight</p>
                </div>
                <p className="mt-4 text-[1.05rem] font-medium leading-relaxed text-white">
                  <AnimatedValue value={ai.reasoning} />
                </p>
                <p className="mt-3 text-[0.84rem] leading-relaxed text-white/64">
                  <AnimatedValue value={ai.impact} />
                </p>
              </div>
            </motion.section>
          </div>
        </div>
      </div>
    </div>
  );
}
