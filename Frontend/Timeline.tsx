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

  return (
    <section className={`relative w-full overflow-hidden rounded-[1.4rem] px-1 py-2 ${className ?? ""}`}>
      <div className="pointer-events-none absolute inset-x-0 top-7 h-px bg-gradient-to-r from-transparent via-violet-300/25 to-transparent" />
      <div className="pointer-events-none absolute left-1/3 top-0 h-24 w-24 rounded-full bg-violet-300/14 blur-3xl" />
      <div className="pointer-events-none absolute right-1/4 top-3 h-24 w-24 rounded-full bg-indigo-300/14 blur-3xl" />

      <header className="mb-2.5 px-3">
        <p className="text-[0.54rem] uppercase tracking-[0.26em] text-white/44">Cinematic Replay</p>
        <h2 className="mt-1 bg-gradient-to-r from-white to-white/72 bg-clip-text text-[1rem] font-medium tracking-tight text-transparent">
          Mission Playback
        </h2>
      </header>

      <ol className="relative grid grid-cols-2 gap-2.5 px-2 pb-1 pt-1 sm:grid-cols-3 lg:grid-cols-5">
        <AnimatePresence initial={false}>
          {events.map((event, index) => {
            const glow = getColor(event, index);
            const active = event.id === activeEventId;

            return (
              <motion.li
                key={event.id}
                initial={{ opacity: 0, y: 8, filter: "blur(5px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                exit={{ opacity: 0, y: -8, filter: "blur(4px)" }}
                transition={{ duration: 0.38, ease: [0.2, 0.8, 0.2, 1] }}
                className="relative"
              >
                <div className="relative rounded-xl border border-white/8 bg-[linear-gradient(180deg,rgba(255,255,255,0.05),rgba(255,255,255,0.018))] px-3 py-2.5 backdrop-blur-xl">
                  <motion.span
                    className="absolute left-2 top-0 h-[2px] w-[66%]"
                    style={{ background: `linear-gradient(90deg, ${glow}, rgba(255,255,255,0))` }}
                    animate={{ opacity: active ? [0.55, 1, 0.55] : [0.25, 0.62, 0.25] }}
                    transition={{ duration: active ? 1.5 : 2.9, repeat: Infinity, ease: "easeInOut" }}
                  />

                  <div className="flex items-center gap-2">
                    <motion.span
                      className="relative h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: glow, boxShadow: `0 0 14px ${glow}` }}
                      animate={{ scale: active ? [1, 1.2, 1] : [1, 1.06, 1] }}
                      transition={{ duration: active ? 1.4 : 2.5, repeat: Infinity, ease: "easeInOut" }}
                    />
                    <p className="text-[0.78rem] font-medium text-white/84">{event.title}</p>
                  </div>

                  <p className="mt-1 text-[0.54rem] uppercase tracking-[0.14em] text-white/48">{event.timestamp ?? "--:--"}</p>
                </div>

                {index < events.length - 1 ? (
                  <motion.span
                    className="pointer-events-none absolute -right-2 top-1/2 hidden h-px w-4 -translate-y-1/2 bg-gradient-to-r from-white/28 to-transparent lg:block"
                    animate={{ opacity: [0.24, 0.78, 0.24] }}
                    transition={{ duration: 2.5, repeat: Infinity, ease: "easeInOut" }}
                  />
                ) : null}
              </motion.li>
            );
          })}
        </AnimatePresence>
      </ol>

      {activeEvent ? (
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={activeEvent.id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.34, ease: [0.2, 0.8, 0.2, 1] }}
            className="mt-2 px-3"
          >
            <p className="text-[0.58rem] uppercase tracking-[0.2em] text-white/44">Now Playing</p>
            <p className="mt-1 text-[0.76rem] text-white/72">{activeEvent.description ?? activeEvent.title}</p>
          </motion.div>
        </AnimatePresence>
      ) : null}
    </section>
  );
}
