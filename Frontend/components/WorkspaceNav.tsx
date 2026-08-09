import { motion } from "framer-motion";
import { NavLink } from "react-router-dom";
import type { ConnectionStatus } from "../lib/wsClient";
import ConnectionBadge from "./ConnectionBadge";

type WorkspaceNavProps = {
  clusterStatus: string;
  theme: "dark" | "light";
  connectionStatus: ConnectionStatus;
  onToggleTheme: () => void;
  onOpenPalette: () => void;
};

const links = [
  { label: "Mission Control", to: "/mission-control" },
  { label: "Digital Twin", to: "/digital-twin" },
  { label: "AI Copilot", to: "/ai-copilot" },
  { label: "Analytics", to: "/analytics" },
  { label: "Settings", to: "/settings" },
];

export default function WorkspaceNav({ clusterStatus, theme, connectionStatus, onToggleTheme, onOpenPalette }: WorkspaceNavProps) {
  return (
    <header className="sticky top-0 z-40 px-3 pt-3 sm:px-5 sm:pt-4 lg:px-8">
      <nav className="mx-auto grid h-14 w-full max-w-[1720px] grid-cols-[auto_1fr_auto] items-center gap-3 rounded-full bg-[linear-gradient(120deg,rgba(255,255,255,0.085),rgba(255,255,255,0.02))] px-3 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.08),0_18px_48px_rgba(0,0,0,0.45)] backdrop-blur-[24px] sm:px-4">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full" style={{ background: "rgba(var(--accent-rgb),1)", boxShadow: "0 0 12px rgba(var(--accent-rgb),0.9)" }} />
          <div className="leading-none">
            <p className="text-[0.64rem] font-medium tracking-[0.3em] text-white/86">LUKSTACK</p>
            <p className="mt-1 text-[0.56rem] tracking-[0.2em] text-white/45">NeuroCool</p>
          </div>
        </div>

        <div className="hidden min-w-0 items-center justify-center gap-1 overflow-x-auto px-2 md:flex">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              className={({ isActive }) =>
                `relative rounded-full px-3 py-1.5 text-[0.62rem] uppercase tracking-[0.14em] transition ${
                  isActive ? "text-white" : "text-white/55 hover:text-white/82"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <span className="relative z-10">{link.label}</span>
                  {isActive ? (
                    <motion.span
                      layoutId="workspace-nav-active"
                      className="absolute inset-0 rounded-full"
                      style={{ background: "rgba(var(--accent-rgb),0.14)" }}
                      transition={{ type: "spring", stiffness: 420, damping: 34 }}
                    />
                  ) : null}
                  <motion.span
                    className="pointer-events-none absolute bottom-[2px] left-1/2 h-[2px] w-0 -translate-x-1/2 rounded-full"
                    style={{ background: "linear-gradient(90deg, rgba(var(--accent-rgb),0.95), rgba(var(--accent-rgb),0.35))" }}
                    animate={{ width: isActive ? "68%" : "0%", opacity: isActive ? 1 : 0.25 }}
                    transition={{ duration: 0.28, ease: [0.2, 0.8, 0.2, 1] }}
                  />
                </>
              )}
            </NavLink>
          ))}
        </div>

        <div className="flex items-center justify-end gap-1.5">
          <ConnectionBadge status={connectionStatus} />

          <div
            className="hidden items-center gap-2 rounded-full border px-3 py-1 md:inline-flex"
            style={{ borderColor: "rgba(var(--accent-rgb),0.14)", background: "rgba(var(--accent-rgb),0.08)" }}
          >
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full" style={{ background: "rgba(var(--accent-rgb),0.6)" }} />
              <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: "rgba(var(--accent-rgb),1)" }} />
            </span>
            <span className="text-[0.54rem] uppercase tracking-[0.14em] text-white/66">{clusterStatus}</span>
          </div>

          <button
            type="button"
            onClick={onOpenPalette}
            aria-label="Command palette"
            className="inline-flex h-8 items-center justify-center rounded-full bg-white/[0.04] px-3 text-[0.56rem] uppercase tracking-[0.12em] text-white/62 transition duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] hover:-translate-y-[1px] hover:bg-white/[0.11] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(var(--accent-rgb),0.7)]"
          >
            Ctrl+K
          </button>

          <button
            type="button"
            onClick={onToggleTheme}
            aria-label="Theme"
            className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-white/[0.04] text-white/62 transition duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] hover:-translate-y-[1px] hover:bg-white/[0.11] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(var(--accent-rgb),0.7)]"
          >
            {theme === "dark" ? "◐" : "◑"}
          </button>

          <button
            type="button"
            aria-label="Profile"
            className="inline-flex h-8 items-center justify-center rounded-full border border-white/14 px-3 text-[0.56rem] uppercase tracking-[0.12em] text-white shadow-[0_8px_20px_rgba(36,16,88,0.45)]"
            style={{ background: "linear-gradient(140deg, rgba(var(--accent-rgb),0.35), rgba(111,133,255,0.35))" }}
          >
            Profile
          </button>
        </div>
      </nav>
    </header>
  );
}
