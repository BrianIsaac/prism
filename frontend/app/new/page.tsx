export default function NewRoutePage() {
  return (
    <main className="h-full w-full flex items-center justify-center px-6">
      <section className="max-w-xl rounded-lg border border-white/10 bg-black/60 backdrop-blur p-8 animate-fade-in-up">
        <p className="text-xs uppercase tracking-[0.2em] text-grab-green/80">
          New Route
        </p>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight text-white">
          Structured race form
        </h1>
        <p className="mt-4 text-sm leading-relaxed text-white/70">
          Type a natural-language query and the spec parser converts it into a
          budget + duration + transport-mode + dietary override. Three agents race
          on the same structured spec.
        </p>
        <p className="mt-6 text-xs text-white/40">
          Form + agent race panel arrive in Phase 5.
        </p>
      </section>
    </main>
  );
}
