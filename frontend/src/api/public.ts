/**
 * The PUBLIC insured-decision surface — unauthenticated.
 *
 * The share token IS the credential, so these requests must never carry the
 * broker's app token. Rather than reuse the shared `api` instance (whose
 * interceptor attaches Authorization / X-Radal-Token whenever a broker happens
 * to be logged in), this module owns a dedicated axios instance that replicates
 * ONLY the OAC body-hash mechanism from `lib/api.ts` — never the auth headers.
 *
 * That duplication is deliberate and follows the `api/ai.ts` precedent: it keeps
 * CloudFront OAC signing working for the public POST without touching the frozen
 * `lib/api.ts`. Breaking the `x-amz-content-sha256` reassignment produces an
 * `InvalidSignatureException` in production only.
 */
import axios, { type InternalAxiosRequestConfig } from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { API_URL } from "@/lib/api";
import { qk } from "@/api/keys";
import type { OfferingDecisionRequest, OfferingPublicRead } from "@/api/types";

async function sha256Hex(input: string): Promise<string> {
  const bytes = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** No-auth axios: OAC body-hash only, never an Authorization / X-Radal-Token. */
const publicApi = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
});

publicApi.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  // OAC does NOT sign POST bodies; the client sends the body SHA-256 so the
  // origin signature validates. Harmless for local direct-to-backend dev.
  if (crypto?.subtle) {
    let body = "";
    const data = config.data;
    if (data !== undefined && data !== null) {
      body = typeof data === "string" ? data : JSON.stringify(data);
      // Hash exactly the bytes that get sent.
      config.data = body;
    }
    config.headers.set("x-amz-content-sha256", await sha256Hex(body));
  }
  return config;
});

/** GET /public/offerings/{token} — 404 when draft/unknown, 410 when expired. */
export function usePublicOffering(shareToken: string | undefined) {
  return useQuery({
    queryKey: qk.publicOfferings.detail(shareToken ?? ""),
    enabled: !!shareToken,
    retry: false,
    queryFn: async () => {
      const { data } = await publicApi.get<OfferingPublicRead>(
        `/public/offerings/${shareToken}`,
      );
      return data;
    },
  });
}

/** POST /public/offerings/{token}/decision — records the insured's choice. */
export function useRecordDecision(shareToken: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: OfferingDecisionRequest) => {
      const { data } = await publicApi.post<OfferingPublicRead>(
        `/public/offerings/${shareToken}/decision`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.publicOfferings.detail(shareToken), data);
    },
  });
}
