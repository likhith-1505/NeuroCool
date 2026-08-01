import { ReactNode } from "react";

type AppLayoutProps = {
  children: ReactNode;
};

export default function AppLayout({ children }: AppLayoutProps) {
  return (
    <div className="relative min-h-dvh w-full overflow-hidden bg-[#05030A] text-white antialiased">
      <style>{`
        @keyframes lukstack-ambient-drift {
          0% { transform: translate3d(-2%, -1%, 0) scale(1); opacity: 0.48; }
          50% { transform: translate3d(2%, 2%, 0) scale(1.05); opacity: 0.64; }
          100% { transform: translate3d(-1%, 3%, 0) scale(1.02); opacity: 0.52; }
        }

        @keyframes lukstack-ambient-glow {
          0%, 100% { opacity: 0.28; }
          50% { opacity: 0.56; }
        }

        @keyframes lukstack-fog {
          0% { transform: translate3d(-6%, 0, 0) scale(1.04); opacity: 0.2; }
          50% { transform: translate3d(4%, -2%, 0) scale(1.08); opacity: 0.32; }
          100% { transform: translate3d(-3%, 2%, 0) scale(1.05); opacity: 0.22; }
        }
      `}</style>

      <div className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_16%_14%,rgba(127,89,255,0.2),transparent_34%),radial-gradient(circle_at_84%_22%,rgba(97,83,255,0.24),transparent_38%),radial-gradient(circle_at_50%_112%,rgba(78,132,255,0.15),transparent_38%),linear-gradient(180deg,#05030A,#0A0618_54%,#130A2E)]" />
        <div className="absolute -left-48 -top-44 h-[36rem] w-[36rem] rounded-full bg-[radial-gradient(circle,rgba(146,107,255,0.27)_0%,rgba(146,107,255,0)_70%)] blur-3xl" style={{ animation: "lukstack-ambient-drift 24s ease-in-out infinite alternate" }} />
        <div className="absolute -right-52 top-8 h-[34rem] w-[34rem] rounded-full bg-[radial-gradient(circle,rgba(101,123,255,0.25)_0%,rgba(101,123,255,0)_72%)] blur-3xl" style={{ animation: "lukstack-ambient-drift 28s ease-in-out infinite alternate-reverse" }} />
        <div className="absolute -inset-[26%] bg-[conic-gradient(from_210deg,rgba(132,102,255,0.17),rgba(95,133,255,0.08),rgba(132,102,255,0.17))] blur-[120px]" style={{ animation: "lukstack-ambient-glow 17s ease-in-out infinite" }} />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_35%,rgba(196,166,255,0.12),rgba(196,166,255,0)_48%)]" style={{ animation: "lukstack-fog 36s ease-in-out infinite" }} />
      </div>

      <div className="relative min-h-screen">{children}</div>
    </div>
  );
}
