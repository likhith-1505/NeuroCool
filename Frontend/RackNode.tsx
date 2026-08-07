import { motion, useReducedMotion } from "framer-motion";

type RackNodeProps = {
  temperature: number;
  health: number;
  prediction: string;
  selected?: boolean;
  onClick?: () => void;
  rackName?: string;
  gpuLoad?: number;
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function toneFromHealth(health: number): { ring: string; core: string; glow: string; ambient: string } {
  if (health < 45) {
    return {
      ring: "rgba(255,110,148,0.96)",
      core: "rgba(255,134,169,0.92)",
      glow: "rgba(255,110,148,0.3)",
      ambient: "rgba(255,122,157,0.16)",
    };
  }

  if (health < 72) {
    return {
      ring: "rgba(198,148,255,0.95)",
      core: "rgba(220,178,255,0.92)",
      glow: "rgba(178,126,255,0.28)",
      ambient: "rgba(182,133,255,0.15)",
    };
  }

  return {
    ring: "rgba(156,124,255,0.95)",
    core: "rgba(194,170,255,0.92)",
    glow: "rgba(149,115,255,0.26)",
    ambient: "rgba(148,115,255,0.14)",
  };
}

export default function RackNode({
  temperature,
  health,
  prediction,
  selected = false,
  onClick,
  rackName,
  gpuLoad,
}: RackNodeProps) {
  const reduceMotion = useReducedMotion();
  const normalizedHealth = clamp(health, 0, 100);
  const normalizedTemp = clamp(temperature, 0, 120);
  const normalizedGpu = typeof gpuLoad === "number" ? clamp(gpuLoad, 0, 100) : undefined;
  const isCritical = normalizedHealth < 45 || normalizedTemp >= 83;
  const tone = toneFromHealth(normalizedHealth);

  const restShadow = selected
    ? `0 0 0 1px rgba(255,255,255,0.28), 0 0 30px ${tone.glow}, 0 22px 46px rgba(5,3,12,0.6)`
    : `0 0 0 1px rgba(255,255,255,0.11), 0 0 15px ${tone.glow}, 0 14px 32px rgba(5,3,12,0.5)`;
  const peakShadow = selected
    ? `0 0 0 1px rgba(255,255,255,0.38), 0 0 50px ${tone.glow}, 0 28px 62px rgba(5,3,12,0.68)`
    : `0 0 0 1px rgba(255,255,255,0.17), 0 0 28px ${tone.glow}, 0 18px 42px rgba(5,3,12,0.58)`;

  return (
    <motion.button
      type="button"
      onClick={onClick}
      whileHover={
        reduceMotion
          ? undefined
          : {
              y: -6,
              scale: 1.045,
            }
      }
      whileTap={reduceMotion ? undefined : { scale: 0.992 }}
      style={{ boxShadow: restShadow }}
      className="group relative inline-flex h-[9.6rem] w-[9.6rem] items-center justify-center rounded-full border border-white/10 bg-[radial-gradient(circle_at_32%_24%,rgba(255,255,255,0.24),rgba(255,255,255,0.04)_38%,rgba(16,10,36,0.95)_70%)] backdrop-blur-2xl outline-none focus-visible:ring-2 focus-visible:ring-violet-300/65 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0a0618]"
      aria-pressed={selected}
      aria-label={`${rackName ?? "Rack node"} ${normalizedTemp.toFixed(1)} degrees Celsius`}
    >
      <span
        className="pointer-events-none absolute inset-0 rounded-full"
        style={{
          boxShadow: peakShadow,
          opacity: 0,
          animation: reduceMotion ? undefined : `rack-glow-pulse ${selected ? 2.2 : 3.4}s ease-in-out infinite`,
        }}
      />

      <span
        className="pointer-events-none absolute -inset-4 rounded-full"
        style={{
          background: `radial-gradient(circle, ${tone.ambient} 0%, rgba(142,109,255,0) 68%)`,
          filter: "blur(12px)",
          animation: reduceMotion ? undefined : "rack-aura-pulse 4.2s ease-in-out infinite",
        }}
      />

      <span
        className="absolute inset-[4px] rounded-full"
        style={{
          background: `conic-gradient(from -92deg, ${tone.ring} 0% ${normalizedHealth}%, rgba(255,255,255,0.11) ${normalizedHealth}% 100%)`,
          animation: reduceMotion ? undefined : `${selected ? "rack-ring-pulse-selected 2.2s" : "rack-ring-pulse-idle 4.8s"} ease-in-out infinite`,
        }}
      />

      <span className="absolute inset-[14px] rounded-full border border-white/10 bg-[radial-gradient(circle_at_52%_30%,rgba(255,255,255,0.2),rgba(255,255,255,0.04)_34%,rgba(8,6,22,0.95)_72%)]" />

      <span
        className="pointer-events-none absolute inset-[22px] rounded-full"
        style={{
          background: `radial-gradient(circle, ${tone.core} 0%, rgba(122,99,255,0.16) 44%, rgba(122,99,255,0) 78%)`,
          filter: "blur(0.5px)",
          animation: reduceMotion ? undefined : "rack-core-pulse 3s ease-in-out infinite",
        }}
      />

      {isCritical ? (
        <span
          className="pointer-events-none absolute -inset-2 rounded-full"
          style={{
            background: "radial-gradient(circle, rgba(255,126,160,0.18) 0%, rgba(255,126,160,0) 72%)",
            filter: "blur(8px)",
            animation: reduceMotion ? undefined : "rack-critical-pulse 1.7s ease-in-out infinite",
          }}
        />
      ) : null}

      <div className="relative z-10 flex flex-col items-center justify-center">
        <p className="bg-gradient-to-b from-white to-white/70 bg-clip-text text-[1.45rem] font-semibold leading-none tracking-tight text-transparent">
          {normalizedTemp.toFixed(1)}°
        </p>
        <p className="mt-1 text-[0.56rem] uppercase tracking-[0.24em] text-white/52">Temp</p>
      </div>

      <motion.div
        className="pointer-events-none absolute -bottom-9 left-1/2 z-20 w-max min-w-[7.2rem] rounded-lg border border-white/12 bg-[#0d0920]/90 px-2.5 py-1.5 text-left backdrop-blur-xl"
        initial={false}
        animate={{ opacity: selected ? 1 : 0, y: selected ? 0 : -4, x: "-50%" }}
        whileHover={{ opacity: 1, y: 0, x: "-50%" }}
        transition={{ duration: 0.3, ease: [0.2, 0.8, 0.2, 1] }}
      >
        <p className="text-[0.54rem] uppercase tracking-[0.2em] text-white/52">{rackName ?? "Rack"}</p>
        {typeof normalizedGpu === "number" ? (
          <p className="mt-0.5 text-[0.62rem] tracking-[0.08em] text-white/84">GPU {normalizedGpu.toFixed(0)}%</p>
        ) : null}
        <p className="text-[0.58rem] tracking-[0.06em] text-white/68">{prediction}</p>
      </motion.div>
    </motion.button>
  );
}
