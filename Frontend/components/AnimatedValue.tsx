import { AnimatePresence, motion, useMotionValue, useSpring, useTransform } from "framer-motion";
import { useEffect } from "react";

type ParsedValue = { value: number | null; suffix: string; raw: string };

function parseValue(value: string | number): ParsedValue {
  if (typeof value === "number") return { value, suffix: "", raw: String(value) };

  const match = value.trim().match(/^([-+]?\d+(?:\.\d+)?)(.*)$/);
  if (!match) return { value: null, suffix: "", raw: value };

  return { value: Number(match[1]), suffix: match[2].trimStart(), raw: value };
}

function CountingNumber({ value, suffix }: { value: number; suffix: string }) {
  const motionValue = useMotionValue(value);
  const spring = useSpring(motionValue, { stiffness: 100, damping: 20, mass: 1 });
  const rounded = useTransform(spring, (latest) => {
    const abs = Math.abs(value);
    const precision = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
    return latest.toFixed(precision).replace(/\.0+$|(?<=\.[0-9])0+$/, "");
  });

  useEffect(() => {
    motionValue.set(value);
  }, [motionValue, value]);

  return (
    <>
      <motion.span>{rounded}</motion.span>
      {suffix ? suffix : null}
    </>
  );
}

/**
 * Renders a value that changes over time (a metric, a label, a recommendation)
 * without ever snapping instantly. Numeric strings ("91", "84%", "13/100") count
 * smoothly via a spring; non-numeric text (risk labels, recommendations) crossfades.
 */
export default function AnimatedValue({ value, className }: { value: string | number; className?: string }) {
  const parsed = parseValue(value);

  if (parsed.value != null) {
    return (
      <span className={className}>
        <CountingNumber value={parsed.value} suffix={parsed.suffix} />
      </span>
    );
  }

  return (
    <AnimatePresence initial={false}>
      <motion.span
        key={parsed.raw}
        initial={{ opacity: 0, y: 4, filter: "blur(3px)" }}
        animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
        exit={{ opacity: 0, y: -4, filter: "blur(2px)" }}
        transition={{ duration: 0.28, ease: [0.2, 0.8, 0.2, 1] }}
        className={className}
      >
        {parsed.raw}
      </motion.span>
    </AnimatePresence>
  );
}
