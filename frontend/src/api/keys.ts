/**
 * Central react-query key factory.
 *
 * One place for every key so a mutation can invalidate precisely
 * (`qk.clients.detail(id)`) or broadly (`qk.clients.all`) without a module
 * having to guess how another module spelled its key.
 *
 * Convention: `all` -> `lists()` -> `list(params)` -> `detail(id)`, which is
 * what `invalidateQueries({ queryKey: qk.x.all })` walks.
 */

/** Any plain params object. `object` (not `Record<string, unknown>`) so a plain
 *  `interface` — which has no index signature — is still assignable. */
export type QueryParams = object | undefined;

function scope(name: string) {
  const all = [name] as const;
  return {
    all,
    lists: () => [...all, "list"] as const,
    list: (params?: QueryParams) => [...all, "list", params ?? {}] as const,
    details: () => [...all, "detail"] as const,
    detail: (id: number | string) => [...all, "detail", String(id)] as const,
    summary: (params?: QueryParams) => [...all, "summary", params ?? {}] as const,
  };
}

export const qk = {
  auth: {
    all: ["auth"] as const,
    me: ["auth", "me"] as const,
    permissions: ["auth", "permissions"] as const,
  },
  clients: scope("clients"),
  assets: {
    ...scope("assets"),
    byClient: (clientId: number, params?: QueryParams) =>
      ["assets", "byClient", clientId, params ?? {}] as const,
  },
  placements: {
    ...scope("placements"),
    transitions: (id: number) => ["placements", "detail", String(id), "transitions"] as const,
  },
  quotes: {
    ...scope("quotes"),
    lineItems: (id: number) => ["quotes", "detail", String(id), "line-items"] as const,
    comparison: (id: number, params?: QueryParams) =>
      ["quotes", "detail", String(id), "comparison", params ?? {}] as const,
  },
  proposals: {
    ...scope("proposals"),
    coverages: (id: number, params?: QueryParams) =>
      ["proposals", "detail", String(id), "coverages", params ?? {}] as const,
  },
  inspectionRequests: scope("inspection-requests"),
  inspections: {
    ...scope("inspections"),
    boundaries: (id: number) => ["inspections", "detail", String(id), "boundaries"] as const,
  },
  insurers: {
    ...scope("insurers"),
    recommendations: (params?: QueryParams) => ["insurers", "recommendations", params ?? {}] as const,
    contacts: (id: number, params?: QueryParams) =>
      ["insurers", "detail", String(id), "contacts", params ?? {}] as const,
    resolvedContact: (id: number, params?: QueryParams) =>
      ["insurers", "detail", String(id), "contacts", "resolve", params ?? {}] as const,
  },
  offerings: {
    ...scope("offerings"),
    pdf: (id: number) => ["offerings", "detail", String(id), "pdf"] as const,
  },
  documents: {
    ...scope("documents"),
    download: (id: number) => ["documents", "detail", String(id), "download"] as const,
  },
  extractions: scope("extractions"),
  agentThreads: {
    ...scope("agent-threads"),
    messages: (id: number) => ["agent-threads", "detail", String(id), "messages"] as const,
  },
  users: {
    ...scope("users"),
    roles: ["users", "roles"] as const,
  },
} as const;

/** Drop `undefined` / `null` / `""` so they never reach the query string. */
export function clean<T extends object>(params?: T): Record<string, unknown> {
  if (!params) return {};
  return Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""),
  );
}
