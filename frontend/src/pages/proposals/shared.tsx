/**
 * Thin re-export of the cross-app kit.
 *
 * These primitives (EmptyState, ErrorBanner, DisabledHint, SoonButton,
 * StatusBadge, Section, KeyValue, the money/deductible formatters, apiError…)
 * started life here while the proposal pass owned the deal-flow modules; the
 * Signal restyle promoted them to `components/common/kit.tsx`, where every
 * module can reach them without importing from a sibling page directory.
 *
 * Existing importers of `@/pages/proposals/shared` keep compiling unchanged —
 * new code should import from `@/components/common/kit` directly.
 */
export * from "@/components/common/kit";
