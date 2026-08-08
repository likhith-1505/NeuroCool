"""NeuroCore — the read-only AI reasoning and operator-assistance layer.

NeuroCore sits *above* the deterministic backend (simulation, forecasting,
optimization, decision engine, execution engine) and explains it — it never
replaces or duplicates any of those systems. Concretely:

- app.neurocore.providers: a swappable LLMProvider Protocol (see
  app.neurocore.providers.base) plus Anthropic/OpenAI/mock adapters,
  selected purely from configuration (app.config.settings.AI_PROVIDER). No
  other module in this package imports either vendor SDK.
- app.neurocore.context: builds a single structured NeuroCoreContext from
  real backend state (SimulationService's live properties plus recent
  Events) — never invented, never recomputed.
- app.neurocore.grounding: deterministic, rule-based retrieval over that
  context (which rack, which plan, which decision, which execution, which
  events are actually relevant to a question) — this is plain Python, not
  an LLM call, per "NeuroCore must never replace decision scoring/
  optimization/forecasting". The LLM's only job is turning already-
  grounded facts into a readable explanation.
- app.neurocore.service: NeuroCoreService, independent of FastAPI —
  orchestrates context + grounding + provider + conversation persistence.
  app.api.ai is a thin route wrapper around it, the same relationship
  every other engine in this backend has with its REST routes.

This phase is read-only by design: NeuroCore cannot call ExecutionService,
mutate a scenario, or touch the simulation. It can only describe what the
deterministic backend has already computed and, where relevant, name the
already-existing recommended action — never act on it.
"""
