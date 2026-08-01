import RackNode from "./RackNode";

type RackNodeProps = Parameters<typeof RackNode>[0];

type RackHealth = "healthy" | "warning" | "critical";

export type ClusterRack = RackNodeProps & {
  id: string;
  rackName: string;
  temperature: number;
  gpuLoad: number;
  healthScore: number;
  predictionIndicator: string;
  healthState?: RackHealth;
  connections?: string[];
  position?: {
    x: number;
    y: number;
  };
};

type ClusterCanvasProps = {
  racks: ClusterRack[];
  className?: string;
  particleCount?: number;
  focusedRackId?: string;
  onRackFocus?: (rack: ClusterRack) => void;
};

type Point = { x: number; y: number };

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function hashToUnit(input: string): number {
  let h = 2166136261;
  for (let i = 0; i < input.length; i += 1) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 10000) / 10000;
}

function deriveHealthState(rack: ClusterRack): RackHealth {
  if (rack.healthState) return rack.healthState;
  if (rack.healthScore < 45 || rack.temperature >= 83) return "critical";
  if (rack.healthScore < 72 || rack.temperature >= 76) return "warning";
  return "healthy";
}

function getOrganicPosition(rack: ClusterRack, index: number, total: number): Point {
  if (rack.position) {
    return {
      x: clamp(rack.position.x, 11, 89),
      y: clamp(rack.position.y, 13, 87),
    };
  }

  const seed = hashToUnit(rack.id);
  const t = (index + 0.8) / Math.max(total, 1);
  const angle = Math.PI * 2 * t * 1.72 + seed * Math.PI * 0.58;
  const radius = 12 + Math.sqrt(t) * 29 + (seed - 0.5) * 5;

  return {
    x: clamp(50 + Math.cos(angle) * radius * 1.08 + (seed - 0.5) * 8, 11, 89),
    y: clamp(52 + Math.sin(angle) * radius * 0.84 + (0.5 - seed) * 6, 13, 87),
  };
}

export default function ClusterCanvas({
  racks,
  className,
  particleCount,
  focusedRackId,
  onRackFocus,
}: ClusterCanvasProps) {
  const points = new Map<string, Point>();
  const rackById = new Map<string, ClusterRack>();

  racks.forEach((rack, index) => {
    points.set(rack.id, getOrganicPosition(rack, index, racks.length));
    rackById.set(rack.id, rack);
  });

  const dedupe = new Set<string>();
  const links: Array<{ from: string; to: string }> = [];

  racks.forEach((rack) => {
    const source = points.get(rack.id);
    if (!source) return;

    const targets =
      rack.connections && rack.connections.length > 0
        ? rack.connections
        : racks
            .filter((candidate) => candidate.id !== rack.id)
            .map((candidate) => {
              const target = points.get(candidate.id)!;
              const dx = source.x - target.x;
              const dy = source.y - target.y;
              return { id: candidate.id, distance: Math.hypot(dx, dy) };
            })
            .sort((a, b) => a.distance - b.distance)
            .slice(0, Math.min(2, Math.max(1, racks.length - 1)))
            .map((nearest) => nearest.id);

    targets.forEach((targetId) => {
      if (!rackById.has(targetId)) return;
      const key = [rack.id, targetId].sort().join(":");
      if (dedupe.has(key)) return;
      dedupe.add(key);
      links.push({ from: rack.id, to: targetId });
    });
  });

  const totalParticles = particleCount ?? clamp(racks.length * 5, 30, 96);

  return (
    <section
      className={`relative isolate h-full min-h-[40rem] w-full overflow-hidden rounded-[2rem] bg-[radial-gradient(circle_at_18%_10%,rgba(146,92,255,0.28),transparent_36%),radial-gradient(circle_at_85%_24%,rgba(102,86,246,0.24),transparent_42%),radial-gradient(circle_at_58%_90%,rgba(83,130,255,0.16),transparent_46%),linear-gradient(170deg,#05030A_0%,#0A0618_52%,#130A2E_100%)] ${className ?? ""}`}
    >
      <style>{`
        @keyframes neurocool-nebula {
          0% { transform: translate3d(-2%, -1%, 0) scale(1); opacity: 0.44; }
          50% { transform: translate3d(2%, 2%, 0) scale(1.06); opacity: 0.66; }
          100% { transform: translate3d(-1%, 3%, 0) scale(1.02); opacity: 0.5; }
        }

        @keyframes neurocool-particle {
          0% { transform: translate3d(0, 0, 0) scale(0.84); opacity: 0.14; }
          50% { transform: translate3d(6px, -16px, 0) scale(1.08); opacity: 0.46; }
          100% { transform: translate3d(-4px, -29px, 0) scale(0.9); opacity: 0.1; }
        }

        @keyframes neurocool-flow {
          from { stroke-dashoffset: 210; }
          to { stroke-dashoffset: 0; }
        }

        @keyframes neurocool-link-pulse {
          0%, 100% { opacity: 0.2; }
          50% { opacity: 0.52; }
        }
      `}</style>

      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -left-40 -top-40 h-[36rem] w-[36rem] rounded-full bg-[radial-gradient(circle,rgba(146,104,255,0.34)_0%,rgba(146,104,255,0)_70%)] blur-3xl" style={{ animation: "neurocool-nebula 20s ease-in-out infinite alternate" }} />
        <div className="absolute -bottom-48 right-[-11rem] h-[35rem] w-[35rem] rounded-full bg-[radial-gradient(circle,rgba(96,132,255,0.24)_0%,rgba(96,132,255,0)_72%)] blur-3xl" style={{ animation: "neurocool-nebula 25s ease-in-out infinite alternate-reverse" }} />
        <div className="absolute inset-0 [mask-image:radial-gradient(circle_at_center,black,transparent_92%)]">
          {Array.from({ length: totalParticles }).map((_, index) => {
            const ax = hashToUnit(`ax-${index}`);
            const ay = hashToUnit(`ay-${index}`);
            const as = hashToUnit(`as-${index}`);
            return (
              <span
                key={`particle-${index}`}
                className="absolute rounded-full bg-violet-200/55 blur-[0.4px]"
                style={{
                  left: `${ax * 100}%`,
                  top: `${ay * 100}%`,
                  width: `${1 + as * 2.2}px`,
                  height: `${1 + as * 2.2}px`,
                  animation: `neurocool-particle ${9 + ax * 15}s ease-in-out ${ay * -12}s infinite`,
                }}
              />
            );
          })}
        </div>
      </div>

      <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
        <defs>
          <linearGradient id="energy-path" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="rgba(164,136,255,0.08)" />
            <stop offset="46%" stopColor="rgba(191,167,255,0.68)" />
            <stop offset="100%" stopColor="rgba(118,176,255,0.08)" />
          </linearGradient>
        </defs>

        {links.map((link, index) => {
          const from = points.get(link.from);
          const to = points.get(link.to);
          if (!from || !to) return null;

          const rackA = rackById.get(link.from)!;
          const rackB = rackById.get(link.to)!;
          const intensity = clamp((rackA.gpuLoad + rackB.gpuLoad) / 200, 0.28, 1);

          const midX = (from.x + to.x) / 2;
          const midY = (from.y + to.y) / 2;
          const bend = ((index % 2 === 0 ? -1 : 1) * (4 + (index % 4))) * 0.82;
          const controlX = midX + bend;
          const controlY = midY - bend * 0.55;
          const path = `M ${from.x} ${from.y} Q ${controlX} ${controlY} ${to.x} ${to.y}`;

          return (
            <g key={`${link.from}-${link.to}`}>
              <path
                d={path}
                fill="none"
                stroke="url(#energy-path)"
                strokeWidth={0.24 + intensity * 0.12}
                strokeLinecap="round"
                opacity={0.2 + intensity * 0.1}
                style={{ animation: `neurocool-link-pulse ${2.8 + (index % 3) * 0.5}s ease-in-out ${index * -0.4}s infinite` }}
              />
              <path
                d={path}
                fill="none"
                stroke="rgba(212,198,255,0.9)"
                strokeWidth={0.08 + intensity * 0.08}
                strokeDasharray="2.8 6.2"
                strokeLinecap="round"
                style={{ animation: `neurocool-flow ${1.9 + (index % 5) * 0.45}s linear ${index * -0.32}s infinite` }}
              />
            </g>
          );
        })}
      </svg>

      <div className="absolute inset-0">
        {racks.map((rack) => {
          const point = points.get(rack.id);
          if (!point) return null;

          const healthState = deriveHealthState(rack);
          const isSelected = rack.id === focusedRackId || (focusedRackId == null && healthState === "critical");

          return (
            <div
              key={rack.id}
              className="absolute -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${point.x}%`, top: `${point.y}%` }}
              onMouseEnter={() => onRackFocus?.(rack)}
              onFocus={() => onRackFocus?.(rack)}
            >
              <RackNode
                temperature={rack.temperature}
                health={rack.health}
                prediction={rack.predictionIndicator}
                rackName={rack.rackName}
                gpuLoad={rack.gpuLoad}
                selected={isSelected}
                onClick={() => onRackFocus?.(rack)}
              />
            </div>
          );
        })}
      </div>
    </section>
  );
}
