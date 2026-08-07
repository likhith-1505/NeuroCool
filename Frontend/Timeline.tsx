import { AnimatePresence, motion } from "framer-motion";

type TimelineEvent = {
  id: string;
  title: string;
  timestamp?: string;
  color?: string;
  description?: string;
};

type TimelineProps = {
  events: TimelineEvent[];
  activeEventId?: string;
  className?: string;
};

const defaultGlow = [
  "rgba(171,140,255,0.95)",
  "rgba(125,167,255,0.95)",
  "rgba(194,160,255,0.95)",
  "rgba(137,186,255,0.95)",
  "rgba(164,136,255,0.95)",
];

function getColor(event: TimelineEvent, index: number): string {
  return event.color ?? defaultGlow[index % defaultGlow.length];
}

function activeSummary(events: TimelineEvent[], activeEventId?: string): TimelineEvent | undefined {
  if (!events.length) return undefined;
  return events.find((event) => event.id === activeEventId) ?? events[events.length - 1];
}

export default function Timeline({ events, activeEventId, className }: TimelineProps) {
  const activeEvent = activeSummary(events, activeEventId);
  const activeIndex = activeEvent ? events.findIndex((event) => event.id === activeEvent.id) : -1;
  const activeGlow = activeEvent ? getColor(activeEvent, Math.max(activeIndex, 0)) : defaultGlow[0];

  return (
    <section className={`relative w-full overflow-hidden rounded-[1.4rem] px-1 py-1.5 ${className ?? ""}`}>
      <div className="pointer-events-none absolute inset-x-0 top-7 h-px bg-gradient-to-r from-transparent via-violet-300/25 to-transparent" />
      <div className="pointer-events-none absolute left-1/3 top-0 h-24 w-24 rounded-full bg-violet-300/14 blur-3xl" />
      <div className="pointer-events-none absolute right-1/4 top-3 h-24 w-24 rounded-full bg-indigo-300/14 blur-3xl" />

      <header className="mb-2 flex items-center justify-between px-3">
        <div>
          <p className="text-[0.54rem] uppercase tracking-[0.26em] text-white/44">Cinematic Replay</p>
          <h2 className="mt-1 bg-gradient-to-r from-white to-white/72 bg-clip-text text-[1rem] font-medium tracking-tight text-transparent">
            Mission Playback
          </h2>
        </div>
        <p className="text-[0.5rem] uppercase tracking-[0.16em] text-white/34">
          {events.length} event{events.length === 1 ? "" : "s"} logged
        </p>
      </header>

      <div className="grid gap-3 px-2 pb-1 lg:grid-cols-[1.3fr_1fr]">
        {/* Now Playing — the cinematic focal point */}
        <div
          className="relative overflow-hidden rounded-2xl border border-white/10 bg-[linear-gradient(160deg,rgba(255,255,255,0.06),rgba(255,255,255,0.015))] px-3.5 py-2.5"
          style={{ boxShadow: `inset 0 0 0 1px rgba(255,255,255,0.03), 0 0 40px -18px ${activeGlow}` }}
        >
          <div className="pointer-events-none absolute inset-x-0 top-0 h-[2px] overflow-hidden">
            <span
              className="absolute inset-y-0 left-0 w-1/3"
              style={{
                background: `linear-gradient(90deg, transparent, ${activeGlow}, transparent)`,
                animation: "timeline-scan 2.6s linear infinite",
              }}
            />
          </div>

          <AnimatePresence initial={false}>
            {activeEvent ? (
              <motion.div
                key={activeEvent.id}
                initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                exit={{ opacity: 0, y: -8, filter: "blur(3px)" }}
                transition={{ duration: 0.36, ease: [0.2, 0.8, 0.2, 1] }}
              >
                <div className="flex items-center gap-2">
                  <span
                    className="relative h-2 w-2 rounded-full"
                    style={{ backgroundColor: activeGlow, boxShadow: `0 0 14px ${activeGlow}`, animation: "timeline-dot-active 1.4s ease-in-out infinite" }}
                  />
                  <p className="text-[0.52rem] uppercase tracking-[0.24em] text-white/48">
                    Now Playing {activeIndex >= 0 ? `· Step ${activeIndex + 1} of ${events.length}` : null}
                  </p>
                </div>
                <h3 className="mt-1 text-[0.98rem] font-medium leading-snug text-white">{activeEvent.title}</h3>
                <p className="mt-0.5 text-[0.74rem] leading-snug text-white/68">{activeEvent.description ?? activeEvent.title}</p>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>

        {/* Chapter strip — compact, secondary */}
        <ol className="relative flex max-h-[6.5rem] flex-col gap-1 overflow-y-auto pr-1">
          <AnimatePresence initial={false}>
            {[...events].reverse().map((event, reversedIndex) => {
              const index = events.length - 1 - reversedIndex;
              const glow = getColor(event, index);
              const active = event.id === activeEventId;

              return (
                <motion.li
                  key={event.id}
                  layout
                  initial={{ opacity: 0, x: 10 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -10 }}
                  transition={{ duration: 0.3, ease: [0.2, 0.8, 0.2, 1] }}
                  className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 transition-colors duration-300 ${
                    active ? "border-white/14 bg-white/[0.05]" : "border-white/6 bg-white/[0.015]"
                  }`}
                >
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{
                      backgroundColor: glow,
                      boxShadow: active ? `0 0 8px ${glow}` : "none",
                      opacity: active ? 1 : 0.55,
                    }}
                  />
                  <p className={`flex-1 truncate text-[0.68rem] ${active ? "text-white/90" : "text-white/56"}`}>{event.title}</p>
                  <span className="shrink-0 text-[0.48rem] uppercase tracking-[0.1em] text-white/34">{event.timestamp ?? "--:--"}</span>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ol>
      </div>
    </section>
  );
}
