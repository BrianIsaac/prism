export default function LiveCanvasPage() {
  return (
    <main className="h-full w-full flex items-center justify-center px-6">
      <section className="max-w-xl rounded-lg border border-white/10 bg-black/60 backdrop-blur p-8 animate-fade-in-up">
        <p className="text-xs uppercase tracking-[0.2em] text-grab-green/80">
          Live Canvas
        </p>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight text-white">
          Prism
        </h1>
        <p className="mt-4 text-sm leading-relaxed text-white/70">
          Three LLM agents race across a live Singapore canvas, priced against real
          GrabMaps traffic and incident feeds. The frozen harness scores their
          plans; HITL-rated picks pin to the shared map for the next race to see.
        </p>
        <p className="mt-6 text-xs text-white/40">
          Live canvas arrives in Phase 4. Launch a race from New Route to seed the
          SSE stream.
        </p>
      </section>
    </main>
  );
}
