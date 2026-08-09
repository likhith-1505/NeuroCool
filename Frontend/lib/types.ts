/**
 * TypeScript mirrors of the backend's Pydantic response schemas (see
 * backend/app/schemas/*.py). Kept field-for-field faithful — including
 * value casing for enums, which Pydantic serializes as the enum's
 * lowercase `.value` (e.g. RackStatus.HEALTHY -> "healthy") — rather than
 * guessed, so a shape mismatch is a compile error, not a runtime one.
 */

// --- enums (backend/app/models/enums.py) -----------------------------------

export type RackStatus = "healthy" | "warning" | "critical" | "offline";
export type EventSeverity = "info" | "warning" | "critical";
export type DecisionStatus = "pending" | "accepted" | "rejected" | "executed" | "expired";
export type ExecutionActionType =
  | "workload_migration"
  | "cooling_adjustment"
  | "job_delay"
  | "cluster_rebalance"
  | "fan_override"
  | "no_action";
export type ExecutionStatus = "running" | "completed" | "failed";
export type OptimizationPlanStatus = "completed" | "failed";
export type PendingActionType = "execute_decision" | "replay_simulation";
export type PendingActionStatus =
  | "pending"
  | "confirmed"
  | "cancelled"
  | "expired"
  | "executing"
  | "completed"
  | "failed";

// backend/app/simulation/physics.py's three-tier classification —
// RackTelemetry.prediction_state.
export type PredictionState = "stable" | "watch" | "at_risk";

// --- simulation lifecycle (app/simulation/state.py, app/schemas/simulation.py) ---
// Whether the tick loop itself is running — distinct from ScenarioStatus
// below, which is *what* a running simulation is doing. The app always
// boots into "idle"; a human explicitly starts it (see GET/POST
// /api/simulation*).
export type SimulationStatusValue = "idle" | "running" | "paused" | "completed" | "error";

export type SimulationStatusRead = {
  status: SimulationStatusValue;
  tick: number;
  started_at: string | null;
  paused_at: string | null;
};

// --- cluster / rack telemetry (app/schemas/cluster.py, rack.py) -----------

export type ClusterTelemetry = {
  id: string;
  name: string;
  overall_health: number;
  average_temperature: number;
  total_power: number;
  cooling_efficiency: number;
  energy_savings: number;
  prediction_confidence: number;
};

export type RackTelemetry = {
  id: string;
  name: string;
  temperature: number;
  gpu_utilization: number;
  cpu_utilization: number;
  power_draw: number;
  cooling_efficiency: number;
  fan_speed: number;
  health_score: number;
  prediction_state: PredictionState | string;
  running_jobs: number;
  status: RackStatus;
};

// --- events (app/schemas/event.py) -----------------------------------------

export type EventRead = {
  id: string;
  cluster_id: string | null;
  rack_id: string | null;
  scenario_id: string | null;
  severity: EventSeverity;
  title: string;
  message: string | null;
  occurred_at: string;
};

// --- scenarios (app/schemas/scenario.py) ------------------------------------

export type ScenarioScope = "cluster" | "single_rack";

export type ScenarioDefinitionRead = {
  key: string;
  name: string;
  description: string;
  scope: ScenarioScope;
  ramp_seconds: number;
  duration_seconds: number | null;
};

export type ScenarioStatus = {
  key: string;
  name: string;
  transition_state: "transitioning" | "steady";
  target_rack_id: string | null;
  activated_at: string;
  // Whether POST /api/scenario/replay would currently succeed — lets the
  // UI disable/hide Replay instead of triggering a guaranteed 400 on a
  // fresh cluster (see backend ScenarioManager.can_replay).
  can_replay: boolean;
};

// --- forecast (app/schemas/forecast.py) -------------------------------------

export type ForecastPoint = {
  horizon_seconds: number;
  timestamp: string;
  predicted_temperature: number;
  predicted_gpu_utilization: number;
  predicted_power: number;
  predicted_health: number;
  predicted_cooling: number;
  predicted_risk: number;
  confidence: number;
};

export type ClusterForecastRead = {
  predictions: ForecastPoint[];
};

export type RackForecastRead = {
  rack_id: string;
  rack_name: string;
  predictions: ForecastPoint[];
};

// --- optimization (app/schemas/optimization.py) -----------------------------

export type CandidateScoreRead = {
  temperature_reduction_c: number;
  power_impact_kw: number;
  cooling_improvement_pct: number;
  execution_cost: number;
  operational_disruption: number;
  risk_reduction: number;
  estimated_recovery_seconds: number;
  confidence: number;
  overall_score: number;
};

export type OptimizationCandidateRead = {
  action_type: ExecutionActionType;
  description: string;
  affected_racks: string[];
  redistribute_racks: string[];
  projected_temperature: number;
  projected_cooling: number;
  projected_power: number;
  score: CandidateScoreRead;
  rejection_reason: string | null;
};

export type OptimizationPlanRead = {
  id: string;
  cluster_id: string;
  scenario_id: string | null;
  trigger_rack_id: string | null;
  trigger_key: string;
  trigger_reason: string;
  status: OptimizationPlanStatus;
  error_message: string | null;
  candidates: OptimizationCandidateRead[];
  winner: OptimizationCandidateRead;
  alternatives: OptimizationCandidateRead[];
  created_at: string;
  completed_at: string | null;
};

// --- decisions / executions (app/schemas/decision.py, execution.py) --------

export type DecisionRead = {
  id: string;
  timestamp: string;
  severity: EventSeverity;
  title: string;
  reasoning: string;
  recommended_action: string;
  expected_temperature_reduction: number | null;
  expected_power_saving: number | null;
  confidence: number;
  affected_racks: string[];
  affected_jobs: unknown[];
  plan_id: string | null;
  alternative_actions: unknown[];
  status: DecisionStatus;
};

export type ExecutionRead = {
  id: string;
  decision_id: string;
  action_type: ExecutionActionType | null;
  status: ExecutionStatus;
  affected_racks: string[];
  summary: string;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
};

// --- pending actions / AI chat (app/schemas/pending_action.py, ai.py) ------

export type PendingActionRead = {
  id: string;
  conversation_id: string;
  plan_id: string | null;
  decision_id: string | null;
  action_type: PendingActionType;
  target: string;
  status: PendingActionStatus;
  summary: string;
  error_message: string | null;
  execution_id: string | null;
  created_at: string;
  expires_at: string;
  confirmed_at: string | null;
  completed_at: string | null;
};

export type ChatRequest = {
  message: string;
  rack_id?: string | null;
  conversation_id?: string | null;
};

export type ChatResponse = {
  conversation_id: string;
  response: string;
  confidence: number;
  sources: string[];
  pending_action: PendingActionRead | null;
};

// --- health (app/schemas/health.py) -----------------------------------------

export type HealthResponse = {
  status: "healthy" | "unhealthy";
  database: "connected" | "disconnected";
  redis: "connected" | "disconnected";
};

// --- WebSocket telemetry snapshot (app/schemas/telemetry.py) ---------------

export type TelemetrySnapshot = {
  timestamp: string;
  cluster: ClusterTelemetry;
  racks: RackTelemetry[];
  scenario: ScenarioStatus;
  simulation: SimulationStatusRead;
  decisions: DecisionRead[];
  forecast: ClusterForecastRead;
  rack_forecasts: RackForecastRead[];
  plans: OptimizationPlanRead[];
  // Only present on ticks that produced new events — see
  // SimulationService._broadcast in the backend.
  events?: EventRead[];
};

// AI action lifecycle broadcasts (app/neurocore/actions.py's _broadcast) —
// distinguished from a TelemetrySnapshot by the `type` discriminator, which
// a regular tick payload never has.
export type AiActionEventType =
  | "AI_ACTION_PENDING"
  | "AI_ACTION_CONFIRMED"
  | "AI_ACTION_EXECUTING"
  | "AI_ACTION_COMPLETED"
  | "AI_ACTION_FAILED"
  | "AI_ACTION_CANCELLED"
  | "AI_ACTION_EXPIRED";

export type AiActionEvent = {
  type: AiActionEventType;
  action: PendingActionRead;
};

// Simulation lifecycle broadcasts (SimulationService._broadcast_simulation_
// event) — fired on start/pause/resume/reset independent of the regular
// per-tick broadcast, since IDLE/PAUSED periods produce no ticks at all.
// Distinguished from a TelemetrySnapshot the same way AiActionEvent is: by
// the `type` discriminator a regular tick payload never has.
export type SimulationLifecycleEventType =
  | "SIMULATION_STARTED"
  | "SIMULATION_PAUSED"
  | "SIMULATION_RESUMED"
  | "SIMULATION_RESET";

export type SimulationLifecycleEvent = {
  type: SimulationLifecycleEventType;
  simulation: SimulationStatusRead;
};

export type WsMessage = TelemetrySnapshot | AiActionEvent | SimulationLifecycleEvent;

export function isAiActionEvent(message: WsMessage): message is AiActionEvent {
  return typeof (message as AiActionEvent).type === "string" && (message as AiActionEvent).type.startsWith("AI_ACTION_");
}

export function isSimulationLifecycleEvent(message: WsMessage): message is SimulationLifecycleEvent {
  return typeof (message as SimulationLifecycleEvent).type === "string" && (message as SimulationLifecycleEvent).type.startsWith("SIMULATION_");
}

export function isTelemetrySnapshot(message: WsMessage): message is TelemetrySnapshot {
  return !isAiActionEvent(message) && !isSimulationLifecycleEvent(message) && "cluster" in message && "racks" in message;
}

// --- AI streaming events (app/schemas/ai_stream.py) -------------------------

export type ThinkingStreamEvent = { type: "thinking"; message: string };
export type ToolStartedStreamEvent = { type: "tool_started"; tool: string };
export type ToolCompletedStreamEvent = { type: "tool_completed"; tool: string; ok: boolean };
export type TextDeltaStreamEvent = { type: "text_delta"; text: string };
export type ActionConfirmationRequiredStreamEvent = {
  type: "action_confirmation_required";
  action_id: string;
  action_type: string;
  summary: string;
  expires_at: string;
};
export type CompletedStreamEvent = { type: "completed"; conversation_id: string; message_id: string };
export type ErrorStreamEvent = { type: "error"; code: string; message: string };

export type ChatStreamEvent =
  | ThinkingStreamEvent
  | ToolStartedStreamEvent
  | ToolCompletedStreamEvent
  | TextDeltaStreamEvent
  | ActionConfirmationRequiredStreamEvent
  | CompletedStreamEvent
  | ErrorStreamEvent;
