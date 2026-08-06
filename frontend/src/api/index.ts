/**
 * Barrel for the typed v2 API layer.
 *
 * Every hook is react-query based and speaks strictly to an endpoint that
 * exists in `backend/app/api/routers/*`. If a hook is missing here, the endpoint
 * does not exist yet — build the API before the UI, never the other way round.
 *
 * Notable gaps in this pass (deliberately NOT stubbed):
 *   - there is no `/dashboard` aggregate: compose the three summary endpoints
 *     (`useClientsSummary`, `useAssetsSummary`, `usePlacementsSummary`);
 *   - there is no global `/search` endpoint;
 *   - there is no `/insurance-lines` endpoint, so the placement form cannot yet
 *     offer a line picker.
 */
export * from "@/api/keys";
export * from "@/api/types";

export * from "@/api/ai";
export * from "@/api/assets";
export * from "@/api/clients";
export * from "@/api/documents";
export * from "@/api/inspections";
export * from "@/api/insurers";
export * from "@/api/offerings";
export * from "@/api/placements";
export * from "@/api/proposals";
export * from "@/api/quotes";
export * from "@/api/users";
