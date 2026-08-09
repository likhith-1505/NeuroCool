/**
 * Consumes POST /api/ai/chat/stream — a Server-Sent-Events response to a
 * POST request, which the browser's native EventSource can't do (it only
 * supports GET), so this reads the fetch() response body as a stream and
 * frames it the same way the backend writes it
 * (backend/app/schemas/ai_stream.py's `encode_sse`): `event: <type>` then
 * `data: <json>`, blank-line terminated.
 *
 * Supports cancellation via AbortController — closing the reader and
 * aborting the underlying fetch — so navigating away from AI Copilot mid-
 * stream (or asking for a new turn) cleanly stops the previous one rather
 * than leaving it running in the background.
 */

import { API_BASE_URL } from "./env";
import type { ChatRequest, ChatStreamEvent } from "./types";

export type StreamChatOptions = {
  onEvent: (event: ChatStreamEvent) => void;
  signal?: AbortSignal;
};

/** Raised when the connection itself fails (network/CORS/non-2xx before
 * any SSE framing could even start) — distinct from a clean `error` stream
 * event, which is the backend's own, already-safe failure report.
 */
export class StreamConnectionError extends Error {
  cause?: unknown;

  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = "StreamConnectionError";
    this.cause = cause;
  }
}

function parseSseBlock(block: string): ChatStreamEvent | null {
  let data: string | null = null;
  for (const line of block.split("\n")) {
    if (line.startsWith("data:")) data = line.slice("data:".length).trim();
  }
  if (data == null) return null;
  try {
    return JSON.parse(data) as ChatStreamEvent;
  } catch {
    return null; // a malformed frame is skipped, never thrown into the UI
  }
}

export async function streamChat(body: ChatRequest, { onEvent, signal }: StreamChatOptions): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/ai/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (cause) {
    if (signal?.aborted) return; // cancellation, not a failure
    throw new StreamConnectionError("Could not reach the NeuroCool backend.", cause);
  }

  if (!response.ok || !response.body) {
    throw new StreamConnectionError(`AI stream request failed (${response.status}).`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = parseSseBlock(block);
        if (event) onEvent(event);
        boundary = buffer.indexOf("\n\n");
      }
    }
  } catch (cause) {
    if (signal?.aborted) return; // cancelled — not a stream failure
    throw new StreamConnectionError("The AI stream connection was interrupted.", cause);
  } finally {
    reader.releaseLock();
  }
}
