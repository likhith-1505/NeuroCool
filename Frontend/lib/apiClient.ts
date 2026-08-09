/**
 * Centralized, typed REST client for the NeuroCool backend. Every request
 * this app makes goes through `request()` below — no component calls
 * fetch() directly — so the base URL, error shape, and JSON handling live
 * in exactly one place (see the objective's "do not scatter fetch() calls"
 * requirement and Frontend/lib/env.ts for the base URL).
 */

import { API_BASE_URL } from "./env";
import type {
  ChatRequest,
  ChatResponse,
  ClusterTelemetry,
  DecisionRead,
  EventRead,
  ExecutionRead,
  ClusterForecastRead,
  HealthResponse,
  OptimizationPlanRead,
  PendingActionRead,
  PendingActionStatus,
  RackForecastRead,
  RackTelemetry,
  ScenarioDefinitionRead,
  ScenarioStatus,
} from "./types";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(`Backend request failed (${status}): ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Thrown when the backend can't be reached at all (network/DNS/CORS
 * failure) — distinct from ApiError, which means the backend *did*
 * respond, just with a non-2xx status. Callers use this split to show
 * "backend unavailable" vs. a specific 404/422/etc. message.
 */
export class NetworkError extends Error {
  cause?: unknown;

  constructor(cause: unknown) {
    super("Could not reach the NeuroCool backend.");
    this.name = "NetworkError";
    this.cause = cause;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (cause) {
    throw new NetworkError(cause);
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? body);
    } catch {
      // Non-JSON error body — fall back to statusText already set above.
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json(body: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(body) };
}

export const apiClient = {
  // --- health --------------------------------------------------------------
  health: () => request<HealthResponse>("/health"),

  // --- cluster / racks -------------------------------------------------
  getCluster: () => request<ClusterTelemetry>("/api/cluster"),
  listRacks: () => request<RackTelemetry[]>("/api/racks"),
  getRack: (rackId: string) => request<RackTelemetry>(`/api/racks/${rackId}`),

  // --- events ----------------------------------------------------------
  listEvents: (limit = 50) => request<EventRead[]>(`/api/events?limit=${limit}`),

  // --- scenarios ---------------------------------------------------------
  listScenarios: () => request<ScenarioDefinitionRead[]>("/api/scenarios"),
  getActiveScenario: () => request<ScenarioStatus>("/api/scenario"),
  activateScenario: (scenario: string) => request<ScenarioStatus>("/api/scenario", json({ scenario })),
  resetScenario: () => request<ScenarioStatus>("/api/scenario/reset", { method: "POST" }),
  replayScenario: () => request<ScenarioStatus>("/api/scenario/replay", { method: "POST" }),

  // --- decisions ---------------------------------------------------------
  listDecisions: () => request<DecisionRead[]>("/api/decisions"),
  getDecision: (decisionId: string) => request<DecisionRead>(`/api/decisions/${decisionId}`),
  acceptDecision: (decisionId: string) => request<DecisionRead>(`/api/decisions/${decisionId}/accept`, { method: "POST" }),
  rejectDecision: (decisionId: string) => request<DecisionRead>(`/api/decisions/${decisionId}/reject`, { method: "POST" }),

  // --- executions (read-only; triggered via the AI action-confirmation
  // flow below, per the objective's execution flow) -----------------------
  listExecutions: () => request<ExecutionRead[]>("/api/executions"),
  getExecution: (executionId: string) => request<ExecutionRead>(`/api/executions/${executionId}`),

  // --- forecast ------------------------------------------------------------
  getForecast: () => request<ClusterForecastRead>("/api/forecast"),
  listRackForecasts: () => request<RackForecastRead[]>("/api/forecast/racks"),
  getRackForecast: (rackId: string) => request<RackForecastRead>(`/api/forecast/racks/${rackId}`),

  // --- optimization plans --------------------------------------------------
  listPlans: () => request<OptimizationPlanRead[]>("/api/plans"),
  getLatestPlan: () => request<OptimizationPlanRead>("/api/plans/latest"),
  getPlan: (planId: string) => request<OptimizationPlanRead>(`/api/plans/${planId}`),

  // --- NeuroCore chat (non-streaming) + pending-action confirmation -------
  chat: (body: ChatRequest) => request<ChatResponse>("/api/ai/chat", json(body)),
  confirmAction: (actionId: string) => request<PendingActionRead>(`/api/ai/actions/${actionId}/confirm`, { method: "POST" }),
  cancelAction: (actionId: string) => request<PendingActionRead>(`/api/ai/actions/${actionId}/cancel`, { method: "POST" }),
  getAction: (actionId: string) => request<PendingActionRead>(`/api/ai/actions/${actionId}`),
  listActions: (params?: { conversationId?: string; status?: PendingActionStatus; limit?: number }) => {
    const search = new URLSearchParams();
    if (params?.conversationId) search.set("conversation_id", params.conversationId);
    if (params?.status) search.set("status", params.status);
    if (params?.limit) search.set("limit", String(params.limit));
    const query = search.toString();
    return request<PendingActionRead[]>(`/api/ai/actions${query ? `?${query}` : ""}`);
  },
};

export type ApiClient = typeof apiClient;
