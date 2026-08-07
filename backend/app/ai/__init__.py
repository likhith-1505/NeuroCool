"""The AI decision engine: a deterministic reasoning layer over live
telemetry (see app.ai.base for the swappable DecisionEngine contract,
app.ai.rules for the current rule-based implementation, and app.ai.service
for lifecycle/persistence orchestration). Not a chatbot — no LLM calls
happen here yet, though the interface is designed so one could be plugged
in later without changing any API route.
"""
