export default function AdminPage() {
  return (
    <main className="h-full w-full flex items-center justify-center px-6">
      <section className="max-w-xl rounded-lg border border-white/10 bg-black/60 backdrop-blur p-8 animate-fade-in-up">
        <p className="text-xs uppercase tracking-[0.2em] text-grab-green/80">
          Admin
        </p>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight text-white">
          Weights, feedback pulse, live feed
        </h1>
        <p className="mt-4 text-sm leading-relaxed text-white/70">
          Frozen vs drifted harness weights, feedback-digest history, and the
          live API-call-per-category feed. Phase 6 ships the sparkline and the
          live-feed panel.
        </p>
        <p className="mt-6 text-xs text-white/40">
          Operator dashboard arrives in Phase 6.
        </p>
      </section>
    </main>
  );
}
