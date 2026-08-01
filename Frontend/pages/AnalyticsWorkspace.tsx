import { motion } from "framer-motion";

const series = [52, 57, 61, 67, 64, 71, 68, 73, 70, 66, 62, 58];

export default function AnalyticsWorkspace() {
  return (
    <div className="relative mx-auto flex w-full max-w-[1700px] flex-1 px-3 pb-28 pt-3 sm:px-5 lg:px-8">
      <div className="relative w-full overflow-hidden rounded-[2.2rem] bg-[linear-gradient(170deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012))] p-4 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07),0_28px_90px_rgba(0,0,0,0.58)] backdrop-blur-[16px] sm:p-6 lg:p-7">
        <div className="mb-4">
          <p className="text-[0.56rem] uppercase tracking-[0.24em] text-white/46">Analytics</p>
          <h1 className="mt-1 text-[1.3rem] font-medium text-white">Operational Health Story</h1>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-[1.4rem] bg-[linear-gradient(165deg,rgba(22,14,52,0.82),rgba(10,8,28,0.86))] p-4">
            <p className="text-[0.56rem] uppercase tracking-[0.2em] text-white/44">Thermal Trend</p>
            <div className="mt-4 flex h-48 items-end gap-1.5">
              {series.map((value, index) => (
                <motion.div
                  key={`${value}-${index}`}
                  className="w-full rounded-t-md bg-[linear-gradient(180deg,rgba(179,149,255,0.92),rgba(115,141,255,0.58))]"
                  initial={{ height: 0 }}
                  animate={{ height: `${value}%` }}
                  transition={{ delay: index * 0.03, duration: 0.5, ease: [0.2, 0.8, 0.2, 1] }}
                />
              ))}
            </div>
          </section>

          <section className="rounded-[1.4rem] bg-[linear-gradient(165deg,rgba(22,14,52,0.82),rgba(10,8,28,0.86))] p-4">
            <p className="text-[0.56rem] uppercase tracking-[0.2em] text-white/44">Power & Cooling</p>
            <div className="mt-4 space-y-3">
              {[
                { label: "Power", value: "4.82 MW", width: "78%" },
                { label: "Cooling Efficiency", value: "91%", width: "91%" },
                { label: "Energy Savings", value: "18.7%", width: "62%" },
                { label: "Prediction Accuracy", value: "93%", width: "93%" },
              ].map((item) => (
                <div key={item.label}>
                  <div className="mb-1 flex justify-between text-[0.7rem] text-white/68">
                    <span>{item.label}</span>
                    <span>{item.value}</span>
                  </div>
                  <div className="h-2 rounded-full bg-white/10">
                    <motion.div
                      className="h-full rounded-full bg-[linear-gradient(90deg,rgba(164,131,255,0.94),rgba(126,173,255,0.92))]"
                      initial={{ width: 0 }}
                      animate={{ width: item.width }}
                      transition={{ duration: 0.65, ease: [0.2, 0.8, 0.2, 1] }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-[1.4rem] bg-[linear-gradient(165deg,rgba(22,14,52,0.82),rgba(10,8,28,0.86))] p-4">
            <p className="text-[0.56rem] uppercase tracking-[0.2em] text-white/44">Thermal</p>
            <p className="mt-2 text-3xl font-semibold text-white">73.6°C</p>
          </section>

          <section className="rounded-[1.4rem] bg-[linear-gradient(165deg,rgba(22,14,52,0.82),rgba(10,8,28,0.86))] p-4">
            <p className="text-[0.56rem] uppercase tracking-[0.2em] text-white/44">Predictions</p>
            <p className="mt-2 text-3xl font-semibold text-white">11 alerts prevented</p>
          </section>
        </div>
      </div>
    </div>
  );
}
