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

  // --- Groups & accounts (spec v3) ------------------------------------------
  /**
   * The main rail. One flat key: every group mutation invalidates the whole
   * navigator, because a rename, an archive or a new account all change it.
   */
  navigator: {
    all: ["navigator"] as const,
  },
  accountGroups: {
    ...scope("account-groups"),
    /** `GET /account-groups/{id}/tree` — the contextual rail. */
    tree: (id: number) => ["account-groups", "detail", String(id), "tree"] as const,
    /** Cursor-paged, so the cursor is part of the key. */
    timeline: (id: number, params?: QueryParams) =>
      ["account-groups", "detail", String(id), "timeline", params ?? {}] as const,
    archives: (id: number) => ["account-groups", "detail", String(id), "archives"] as const,
  },

  // --- Case files (expedientes) ---------------------------------------------
  caseFiles: {
    ...scope("case-files"),
    transitions: (id: number) => ["case-files", "detail", String(id), "transitions"] as const,
    timeline: (id: number) => ["case-files", "detail", String(id), "timeline"] as const,
    documents: (id: number, params?: QueryParams) =>
      ["case-files", "detail", String(id), "documents", params ?? {}] as const,
    packs: (id: number) => ["case-files", "detail", String(id), "packs"] as const,
    recipients: (id: number) => ["case-files", "detail", String(id), "recipients"] as const,
    /** `GET /case-files/{id}/history` — the origin chain + the prior vigencia. */
    history: (id: number) => ["case-files", "detail", String(id), "history"] as const,
  },
  leads: scope("leads"),
  notes: {
    ...scope("notes"),
    forEntity: (entityType: string, entityId: number, params?: QueryParams) =>
      ["notes", "list", entityType, String(entityId), params ?? {}] as const,
  },
  activities: scope("activities"),
  packs: scope("packs"),

  // --- Post-sale. Published HERE so the postsale pass never invents a key ----
  policies: {
    ...scope("policies"),
    mirrorDiff: (id: number) => ["policies", "detail", String(id), "mirror-diff"] as const,
    warranties: (id: number) => ["policies", "detail", String(id), "warranties"] as const,
    caseFiles: (id: number) => ["policies", "detail", String(id), "case-files"] as const,
  },
  endorsements: {
    ...scope("endorsements"),
    /** The N members of one prórroga, shared `batch_key` (spec v3 rule 3). */
    batch: (batchKey: string) => ["endorsements", "batch", batchKey] as const,
  },
  collections: {
    ...scope("collections"),
    installments: (id: number) =>
      ["collections", "detail", String(id), "installments"] as const,
    status: (id: number) => ["collections", "detail", String(id), "status"] as const,
  },
  claims: {
    ...scope("claims"),
    items: (id: number) => ["claims", "detail", String(id), "items"] as const,
  },
  warranties: scope("warranties"),

  // --- AI registry ----------------------------------------------------------
  aiCategories: scope("ai-categories"),

  // --- v6 antecedentes expediente + per-ramo record schemas ------------------
  /** The registered/in-progress expediente, keyed by account `case_file_id`. */
  expedientes: scope("antecedentes-expedientes"),
  /** Per-ramo antecedentes schemas; `detail(insuranceLineId)` = resolution. */
  ramoSchemas: scope("ramo-schemas"),
} as const;

/** Drop `undefined` / `null` / `""` so they never reach the query string. */
export function clean<T extends object>(params?: T): Record<string, unknown> {
  if (!params) return {};
  return Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""),
  );
}
