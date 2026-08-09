/**
 * Centralized backend connection config — the only place that reads
 * import.meta.env, so no component ever hardcodes a host/port. See
 * .env.example (VITE_API_URL / VITE_WS_URL).
 */

function stripTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

function deriveWsUrl(apiUrl: string): string {
  if (apiUrl.startsWith("https://")) return `wss://${apiUrl.slice("https://".length)}`;
  if (apiUrl.startsWith("http://")) return `ws://${apiUrl.slice("http://".length)}`;
  return apiUrl;
}

export const API_BASE_URL = stripTrailingSlash(import.meta.env.VITE_API_URL ?? "http://localhost:8000");

export const WS_BASE_URL = stripTrailingSlash(
  import.meta.env.VITE_WS_URL ?? deriveWsUrl(API_BASE_URL),
);
