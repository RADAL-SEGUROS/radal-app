/**
 * Honest AI failure codes (fast reading, docs/v12/11 decision 7).
 *
 * A provider TIMEOUT is not "El proveedor de IA no está disponible". The
 * server tags a typed AI failure with a stable machine code on the
 * `X-Radal-AI-Error` header (and, for the technical read, in the body's
 * `error_code`). The header is not in the CORS `expose_headers` list, so a
 * cross-origin dev SPA cannot read it — the HTTP status is the fallback:
 * 504 is a timeout (the provider's or the CloudFront edge's), 502/503 is the
 * provider being down / unconfigured.
 *
 * `lost` is the case where the browser never got an answer it can trust — no
 * response at all (network error, the edge cut the connection) or a 504. The
 * server may still have finished the work, so callers REFETCH instead of
 * declaring the result lost.
 */
export type AiErrorCode = "ai_timeout" | "ai_unavailable" | "ai_parse";

interface ErrorShape {
  response?: {
    status?: number;
    headers?: Record<string, unknown>;
    data?: { detail?: unknown; error_code?: unknown; code?: unknown };
  };
  code?: string;
  isAxiosError?: boolean;
}

const KNOWN: Record<string, AiErrorCode> = {
  ai_timeout: "ai_timeout",
  ai_unavailable: "ai_unavailable",
  ai_provider: "ai_unavailable",
  ai_not_configured: "ai_unavailable",
  ai_parse: "ai_parse",
};

function normalise(code: unknown): AiErrorCode | null {
  return typeof code === "string" ? (KNOWN[code] ?? null) : null;
}

/** The AI failure behind an axios error, or `null` when it is not one. */
export function aiErrorCode(error: unknown): AiErrorCode | null {
  const err = error as ErrorShape | null | undefined;
  const res = err?.response;
  if (!res) return null;
  // The structured body (`detail: {code, message}`, `services/ai_error_copy`)
  // speaks the contract's vocabulary; the header is the finer legacy code.
  const detail = res.data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const fromDetail =
      normalise((detail as { code?: unknown }).code) ??
      normalise((detail as { error_code?: unknown }).error_code);
    if (fromDetail) return fromDetail;
  }
  const fromBody = normalise(res.data?.error_code) ?? normalise(res.data?.code);
  if (fromBody) return fromBody;
  const fromHeader = normalise(res.headers?.["x-radal-ai-error"]);
  if (fromHeader) return fromHeader;
  if (res.status === 504) return "ai_timeout";
  if (res.status === 502 || res.status === 503) return "ai_unavailable";
  return null;
}

/**
 * No trustworthy answer: a network error / dropped connection, or a 504 that
 * did NOT come from our API (the CloudFront edge's, at 60 s, while the Lambda
 * may keep working). A 504 carrying our structured `detail` or the
 * `X-Radal-AI-Error` header is the server's own, final timeout — not lost.
 */
export function isLostResponse(error: unknown): boolean {
  const err = error as ErrorShape | null | undefined;
  if (!err) return false;
  if (!err.response) return !!err.isAxiosError || err.code === "ERR_NETWORK";
  if (err.response.status !== 504) return false;
  const detail = err.response.data?.detail;
  const ours =
    (!!detail && typeof detail === "object" && !Array.isArray(detail)) ||
    typeof err.response.headers?.["x-radal-ai-error"] === "string";
  return !ours;
}

/**
 * The server's own Spanish copy when it sent the structured
 * `detail: {code, message}` — never the legacy plain-string `detail`, which is
 * the provider's English exception text.
 */
export function aiServerMessage(error: unknown): string | null {
  const detail = (error as ErrorShape | null | undefined)?.response?.data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  return null;
}
