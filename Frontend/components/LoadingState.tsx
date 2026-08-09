/** A minimal, on-brand "waiting for the backend's first telemetry frame"
 * placeholder — shown instead of a workspace that would otherwise crash or
 * render fabricated zeros before /ws/telemetry has delivered anything.
 * Reuses the same glass-panel language every workspace already uses
 * rather than introducing a new visual pattern.
 */
export default function LoadingState({ label = "Connecting to NeuroCool backend…" }: { label?: string }) {
  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 items-center justify-center px-3 pb-36 pt-3 sm:px-5 lg:px-8">
      <div className="flex flex-col items-center gap-3 rounded-[1.6rem] border border-white/8 bg-white/[0.03] px-8 py-10 text-center">
        <span className="relative flex h-3 w-3">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full" style={{ background: "rgba(var(--accent-rgb),0.6)" }} />
          <span className="relative inline-flex h-3 w-3 rounded-full" style={{ background: "rgba(var(--accent-rgb),1)" }} />
        </span>
        <p className="text-[0.72rem] uppercase tracking-[0.2em] text-white/56">{label}</p>
      </div>
    </div>
  );
}
