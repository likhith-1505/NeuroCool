/**
 * Drives the Execute Recommendation button through the real, existing
 * confirmation-gated action flow (see the integration objective's
 * execution-flow requirement):
 *
 *   decision -> POST /api/ai/chat (ask NeuroCore to execute it)
 *            -> a PendingAction comes back if NeuroCore proposed one
 *            -> user confirms
 *            -> POST /api/ai/actions/{id}/confirm
 *            -> ExecutionService actually runs; telemetry changes over
 *               /ws/telemetry; this hook reflects the real, final status
 *               PendingActionService.confirm already returns synchronously
 *               — never an optimistic "success".
 *
 * There is no backend endpoint to create a PendingAction directly — write
 * tools only exist behind NeuroCore's own tool-calling loop (see
 * backend/app/neurocore/tools/write_tools.py) — so asking in natural
 * language via the existing chat endpoint *is* the minimum integration-
 * compatible way to reach it without inventing a new backend endpoint.
 * If no LLM provider is configured (or it declines), that is surfaced
 * honestly as an error state, never papered over.
 */
import { useCallback, useRef, useState } from "react";
import { apiClient } from "./apiClient";
import type { DecisionRead, PendingActionRead } from "./types";

export type ExecutionFlowState =
  | { phase: "idle" }
  | { phase: "proposing" }
  | { phase: "awaiting_confirmation"; action: PendingActionRead }
  | { phase: "confirming"; action: PendingActionRead }
  | { phase: "completed"; action: PendingActionRead }
  | { phase: "error"; message: string };

export function useExecuteRecommendation() {
  const [state, setState] = useState<ExecutionFlowState>({ phase: "idle" });
  const conversationIdRef = useRef<string | undefined>(undefined);

  const propose = useCallback(async (decision: DecisionRead) => {
    setState({ phase: "proposing" });
    try {
      const response = await apiClient.chat({
        message: `Execute the recommended action for decision ${decision.id}: ${decision.recommended_action}`,
        conversation_id: conversationIdRef.current,
      });
      conversationIdRef.current = response.conversation_id;
      if (response.pending_action) {
        setState({ phase: "awaiting_confirmation", action: response.pending_action });
      } else {
        setState({
          phase: "error",
          message: response.response || "NeuroCore did not propose an executable action for this decision.",
        });
      }
    } catch (error) {
      setState({ phase: "error", message: error instanceof Error ? error.message : "Request to NeuroCore failed." });
    }
  }, []);

  const confirm = useCallback(async (actionId: string) => {
    setState((current) => (current.phase === "awaiting_confirmation" ? { phase: "confirming", action: current.action } : current));
    try {
      const action = await apiClient.confirmAction(actionId);
      if (action.status === "completed") {
        setState({ phase: "completed", action });
      } else {
        // PendingActionService.confirm always returns the real, final
        // outcome (COMPLETED or FAILED) rather than raising for a business
        // failure — see backend/app/neurocore/actions.py. Surfaced as-is.
        setState({ phase: "error", message: action.error_message ?? `Execution ended in status: ${action.status}.` });
      }
    } catch (error) {
      setState({ phase: "error", message: error instanceof Error ? error.message : "Confirmation request failed." });
    }
  }, []);

  const reset = useCallback(() => setState({ phase: "idle" }), []);

  return { state, propose, confirm, reset };
}

/** Maps flow phase -> AIPanel's existing executeLabel/executeDisabled/
 * isExecuting props, so every consumer renders the same honest state
 * (never an optimistic "success") without duplicating this switch.
 */
export function executeButtonProps(state: ExecutionFlowState): { label: string; disabled: boolean; executing: boolean } {
  switch (state.phase) {
    case "idle":
      return { label: "Execute Recommendation", disabled: false, executing: false };
    case "proposing":
      return { label: "Proposing…", disabled: true, executing: true };
    case "awaiting_confirmation":
      return { label: "Confirm Execution", disabled: false, executing: false };
    case "confirming":
      return { label: "Executing…", disabled: true, executing: true };
    case "completed":
      return { label: "Execution Complete", disabled: true, executing: false };
    case "error":
      return { label: "Retry Execution", disabled: false, executing: false };
  }
}

export function handleExecuteClick(
  state: ExecutionFlowState,
  decision: DecisionRead | null,
  propose: (decision: DecisionRead) => void,
  confirm: (actionId: string) => void,
): void {
  if (state.phase === "awaiting_confirmation") {
    confirm(state.action.id);
    return;
  }
  if (!decision) return;
  if (state.phase === "idle" || state.phase === "error" || state.phase === "completed") {
    propose(decision);
  }
}
