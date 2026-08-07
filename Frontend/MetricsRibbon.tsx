import { motion, useMotionValue, useReducedMotion, useSpring, useTransform } from "framer-motion";
import { useEffect } from "react";

type MetricValue = string | number;

type MetricsRibbonProps = {
  clusterHealth: MetricValue;
  averageTemperature: MetricValue;
  power: MetricValue;
  pue: MetricValue;
  energySaved: MetricValue;
  avoidedThrottling: MetricValue;
  className?: string;
};

type ParsedMetric = {
  value: number | null;
  suffix: string;
  raw: string;
};

type MetricItemProps = {
  label: string;
  value: MetricValue;
  index: number;
};

function parseMetricValue(value: MetricValue): ParsedMetric {
  if (typeof value === "number") {
    return { value, suffix: "", raw: value.toString() };
  }

  const match = value.trim().match(/^([-+]?\d+(?:\.\d+)?)(.*)$/);
  if (!match) {
    return { value: null, suffix: "", raw: value };
  }

  return {
    value: Number(match[1]),
    suffix: match[2].trimStart(),
    raw: value,
  };
}

function AnimatedMetricValue({ value, suffix }: { value: number; suffix: string }) {
  const motionValue = useMotionValue(0);
  const spring = useSpring(motionValue, { stiffness: 54, damping: 18, mass: 1.1 });
  const rounded = useTransform(spring, (latest) => {
    const absValue = Math.abs(value);
    const precision = absValue >= 100 ? 0 : absValue >= 10 ? 1 : 2;
    return latest.toFixed(precision).replace(/\.0+$|(?<=\.[0-9])0+$/, "");
  });

  useEffect(() => {
    motionValue.set(value);
  }, [motionValue, value]);

  return (
    <>
      <motion.span>{rounded}</motion.span>
      {suffix ? <span>{` ${suffix}`}</span> : null}
    </>
  );
}

function MetricItem({ label, value, index }: MetricItemProps) {
  const reducedMotion = useReducedMotion();
  const parsed = parseMetricValue(value);

  return (
    <div
      className="relative flex min-w-[7.8rem] flex-1 flex-col px-3 py-1 sm:min-w-[8.7rem]"
      style={{
        animation: reducedMotion ? undefined : `metric-breathe ${4.2 + index * 0.22}s ease-in-out ${index * 0.06}s infinite`,
      }}
    >
      <p className="text-[0.5rem] uppercase tracking-[0.22em] text-white/45">{label}</p>
      <p className="mt-1 bg-gradient-to-b from-white to-white/72 bg-clip-text text-[1.08rem] font-semibold leading-none tracking-tight text-transparent sm:text-[1.2rem]">
        {parsed.value == null ? parsed.raw : <AnimatedMetricValue value={parsed.value} suffix={parsed.suffix} />}
      </p>
    </div>
  );
}

export default function MetricsRibbon({
  clusterHealth,
  averageTemperature,
  power,
  pue,
  energySaved,
  avoidedThrottling,
  className,
}: MetricsRibbonProps) {
  const reducedMotion = useReducedMotion();

  const metrics = [
    { label: "Cluster Health", value: clusterHealth },
    { label: "Avg Temperature", value: averageTemperature },
    { label: "Power", value: power },
    { label: "PUE", value: pue },
    { label: "Energy Saved", value: energySaved },
    { label: "Avoided Throttling", value: avoidedThrottling },
  ];

  return (
    <section
      className={`relative w-full overflow-hidden rounded-xl bg-[linear-gradient(120deg,rgba(154,116,255,0.16),rgba(108,130,255,0.08)_42%,rgba(11,8,27,0.48))] px-2 py-1.5 ${className ?? ""}`}
      style={{ boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.08), 0 10px 22px rgba(6,4,14,0.4)" }}
    >
      <span
        className="pointer-events-none absolute inset-0 rounded-xl"
        style={{
          boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.12), 0 16px 34px rgba(6,4,14,0.5)",
          animation: reducedMotion ? undefined : "ribbon-glow-pulse 6.2s ease-in-out infinite",
        }}
      />
      <div className="pointer-events-none absolute inset-0 rounded-xl border border-white/10" />
      <div
        className="pointer-events-none absolute -left-1/3 top-0 h-full w-1/2 rounded-full bg-[radial-gradient(circle,rgba(167,128,255,0.28)_0%,rgba(167,128,255,0)_72%)] blur-2xl"
        style={{ animation: reducedMotion ? undefined : "ribbon-streak 9.5s linear infinite" }}
      />

      <div className="relative z-10 flex flex-wrap items-center gap-y-0.5">
        {metrics.map((metric, index) => (
          <div key={metric.label} className="flex flex-1 items-center">
            <MetricItem label={metric.label} value={metric.value} index={index} />
            {index < metrics.length - 1 ? (
              <span className="hidden h-7 w-px bg-gradient-to-b from-transparent via-white/14 to-transparent lg:block" />
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}
