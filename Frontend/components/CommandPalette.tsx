import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";

export type CommandItem = {
  id: string;
  label: string;
  hint?: string;
};

type CommandPaletteProps = {
  open: boolean;
  commands: CommandItem[];
  onClose: () => void;
  onSelect: (commandId: string) => void;
};

export default function CommandPalette({ open, commands, onClose, onSelect }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setActiveIndex(0);
    }
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((command) => `${command.label} ${command.hint ?? ""}`.toLowerCase().includes(q));
  }, [commands, query]);

  useEffect(() => {
    function handleKeydown(event: KeyboardEvent) {
      if (!open) return;
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveIndex((index) => Math.min(filtered.length - 1, index + 1));
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveIndex((index) => Math.max(0, index - 1));
      }
      if (event.key === "Enter") {
        event.preventDefault();
        const selected = filtered[activeIndex];
        if (selected) onSelect(selected.id);
      }
    }

    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [activeIndex, filtered, onClose, onSelect, open]);

  return (
    <AnimatePresence>
      {open ? (
        <>
          <motion.div
            className="fixed inset-0 z-50 bg-[#070512]/75 backdrop-blur-md"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.div
            initial={{ opacity: 0, y: 16, scale: 0.98, x: "-50%" }}
            animate={{ opacity: 1, y: 0, scale: 1, x: "-50%" }}
            exit={{ opacity: 0, y: 10, scale: 0.99, x: "-50%" }}
            transition={{ duration: 0.24, ease: [0.2, 0.8, 0.2, 1] }}
            className="fixed left-1/2 top-[18%] z-50 w-[min(46rem,92vw)] overflow-hidden rounded-2xl border border-white/10 bg-[linear-gradient(180deg,rgba(22,14,48,0.92),rgba(12,8,28,0.96))] shadow-[0_30px_80px_rgba(0,0,0,0.65)]"
          >
            <div className="border-b border-white/10 px-4 py-3">
              <input
                autoFocus
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setActiveIndex(0);
                }}
                placeholder="Search commands, racks, jobs..."
                className="w-full bg-transparent text-sm text-white placeholder:text-white/42 focus:outline-none"
              />
            </div>

            <div className="max-h-[22rem] overflow-auto p-2">
              {filtered.length === 0 ? (
                <p className="px-2 py-8 text-center text-sm text-white/50">No commands found</p>
              ) : (
                filtered.map((command, index) => {
                  const active = index === activeIndex;
                  return (
                    <button
                      key={command.id}
                      type="button"
                      onClick={() => onSelect(command.id)}
                      className="relative mb-1 flex w-full items-center justify-between rounded-lg px-3 py-2 text-left"
                    >
                      {active ? (
                        <motion.span
                          layoutId="palette-active"
                          className="absolute inset-0 rounded-lg"
                          style={{ background: "rgba(var(--accent-rgb),0.16)" }}
                          transition={{ type: "spring", stiffness: 420, damping: 35 }}
                        />
                      ) : null}
                      <span className="relative z-10 text-sm text-white/90">{command.label}</span>
                      <span className="relative z-10 text-[0.62rem] uppercase tracking-[0.12em] text-white/42">{command.hint}</span>
                    </button>
                  );
                })
              )}
            </div>
          </motion.div>
        </>
      ) : null}
    </AnimatePresence>
  );
}
