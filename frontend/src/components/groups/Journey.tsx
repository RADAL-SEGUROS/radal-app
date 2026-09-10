/**
 * The Journey — ONE visualization for "where is this case and what comes next"
 * (v4 spec §3, standing decision 3). A continuous stroke ringed by nodes — the
 * same idea as the Radal mark, laid horizontally: the stroke is the shared
 * record, the nodes are the milestones.
 *
 * The component is PRESENTATION ONLY. Every enabled/disabled decision comes
 * from `GET /case-files/{id}/transitions` (`transitions` prop) — the server's
 * reasons stay authoritative and are surfaced verbatim; nothing here
 * re-implements a stage guard. `CASE_STAGE_FLOW` supplies the micro-stage
 * order; the macro-phase table below groups all 29 `CaseStage` values into the
 * phases the broker actually thinks in.
 *
 * Variants:
 *  - `hero`    — the account page's hero: labelled nodes, micro-stage ticks,
 *                the current stage name, the "qué sigue" line and THE one
 *                primary `Avanzar` button of the page (Signal: pine, never ink).
 *  - `compact` — ~28px rail for account/ramo cards: no labels except the
 *                current `{phase} · {stage}` inline, no fetch, no buttons.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Check, ChevronDown, Info, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DisabledHint } from "@/components/common/kit";
import { cn } from "@/lib/utils";
import {
  CASE_STAGE_FLOW,
  CLAIM_STAGES,
  ENDORSEMENT_STAGES,
  type CaseFileKind,
  type CaseStage,
  type CaseTransitionOption,
  type PendingAction,
} from "@/api/types";

export interface JourneyProps {
  kind: CaseFileKind;
  stage: CaseStage;
  /** From `GET /case-files/{id}/transitions` — omit/[] on compact cards. */
  transitions?: CaseTransitionOption[];
  variant: "hero" | "compact";
  /**
   * The account's actionable gaps (`GET /case-files/{id}/pending-actions`),
   * hero only. Surfaced as a "N pendientes" chip on the current stage so the
   * hero says not just WHERE the case is but WHAT the broker must still do.
   */
  pendingActions?: PendingAction[];
  /** Gate + mutation wiring, hero only (the contract the old `JourneyStrip` had). */
  canTransition?: boolean;
  isPending?: boolean;
  pendingStage?: CaseStage | null;
  onTransition?: (stage: CaseStage) => void;
  className?: string;
}

/**
 * The first forward stage of the rail after `stage` (server flow order), or
 * `null` when the case is already at the end / closed. Used by the overview
 * cards to print a light "next step" caption WITHOUT a per-account transitions
 * request — the caption is orientation, the account page owns the real guard.
 */
export function nextForwardStage(kind: CaseFileKind, stage: CaseStage): CaseStage | null {
  if (stage === "closed") return null;
  const flow = CASE_STAGE_FLOW[kind] ?? [];
  const idx = flow.indexOf(stage);
  if (idx < 0 || idx >= flow.length - 1) return null;
  return flow[idx + 1];
}

// -----------------------------------------------------------------------------
// Macro-phase grouping — every one of the 29 CaseStage values maps to exactly
// one node (spec §3.2). Account kinds group micro-stages under six phases;
// post-sale rails are already macro-grained, so each stage IS its own node.
// -----------------------------------------------------------------------------

interface RailNode {
  /**
   * Label source: account/renewal nodes read `cases:journey.phases.${id}`;
   * post-sale nodes read `cases:stages.${id}` (the id IS the stage).
   */
  id: string;
  stages: readonly CaseStage[];
}

// The seven milestones the broker actually names (v8): records/intake ·
// technical spec (bases técnicas) · market/quotes · comparison · proposal ·
// policy · active. Splitting the old "mercado" node into "technical" (the
// bases técnicas) and "market" (submission + quotes) is what lets the rail read
// like the real journey the account travels.
const ACCOUNT_PHASES: readonly RailNode[] = [
  { id: "records", stages: ["lead", "intake", "pre_underwriting"] },
  { id: "technical", stages: ["technical_basis"] },
  { id: "market", stages: ["market_submission", "quotes_received"] },
  { id: "comparison", stages: ["comparison", "insured_decision"] },
  { id: "proposal", stages: ["proposal_issued", "ratified"] },
  { id: "policy", stages: ["policy_issued", "mirror_validation"] },
  { id: "active", stages: ["active"] },
];

/**
 * `collection_suspended` is deliberately NOT a main-rail node: it is a side
 * state drawn as a branch below the line between `in_progress` and `settled`
 * (spec §3.2). When current, the whole rail renders in warn tone and the
 * branch node itself in danger.
 */
const COLLECTION_RAIL: readonly CaseStage[] = [
  "collection_scheduled",
  "collection_in_progress",
  "collection_overdue",
  "collection_settled",
];

const NODES_BY_KIND: Record<CaseFileKind, readonly RailNode[]> = {
  account: ACCOUNT_PHASES,
  renewal: [{ id: "renewal", stages: ["renewal_review"] }, ...ACCOUNT_PHASES],
  endorsement: ENDORSEMENT_STAGES.map((s) => ({ id: s, stages: [s] })),
  collection: COLLECTION_RAIL.map((s) => ({ id: s, stages: [s] })),
  claim: CLAIM_STAGES.map((s) => ({ id: s, stages: [s] })),
};

/** Post-sale rails carry one overarching phase label (compact `{phase} · {stage}`). */
const KIND_PHASE: Partial<Record<CaseFileKind, string>> = {
  endorsement: "endoso",
  collection: "cobranza",
  claim: "siniestro",
};

const POST_SALE = new Set<CaseFileKind>(["endorsement", "collection", "claim"]);

// The only stages that recolor their node — status is meaning (spec §3.2).
const NODE_TONE: Partial<Record<CaseStage, "warn" | "danger">> = {
  collection_overdue: "warn",
  collection_suspended: "danger",
};

const TONE_COLOR = {
  brand: "var(--brand)",
  warn: "var(--warn)",
  danger: "var(--neg)",
} as const;
const TONE_RING = {
  brand: "var(--brand-soft)",
  warn: "var(--warn-soft)",
  danger: "var(--neg-soft)",
} as const;
const TONE_TEXT = {
  brand: "var(--ink)",
  warn: "var(--warn-text)",
  danger: "var(--neg-text)",
} as const;

type Tone = keyof typeof TONE_COLOR;

// One-shot mount keyframes (hero only): the pine segment draws itself over
// 400ms cubic-bezier(0.2,0,0,1) and the current node pops in (scale .25→1).
// The current node then breathes: a soft ring expands and fades on a slow
// loop — status you can find at a glance without reading. Everything off
// under prefers-reduced-motion (keyframes are for one-shot/ambient staged
// sequences; interactive states below use 150ms transitions).
const JOURNEY_CSS = `
@keyframes rj-draw { from { stroke-dashoffset: 1; } to { stroke-dashoffset: 0; } }
@keyframes rj-pop { from { scale: .25; opacity: 0; } to { scale: 1; opacity: 1; } }
@keyframes rj-halo { 0% { transform: scale(1); opacity: .9; } 75% { transform: scale(2.7); opacity: 0; } 100% { transform: scale(2.7); opacity: 0; } }
.rj-draw { stroke-dasharray: 1; animation: rj-draw 400ms cubic-bezier(0.2, 0, 0, 1) both; }
.rj-pop { animation: rj-pop 300ms cubic-bezier(0.2, 0, 0, 1) both; }
.rj-halo { animation: rj-halo 2400ms cubic-bezier(0.2, 0, 0, 1) 400ms infinite; }
@media (prefers-reduced-motion: reduce) {
  .rj-draw, .rj-pop { animation: none; }
  .rj-halo { animation: none; opacity: 0; }
}
`;

// -----------------------------------------------------------------------------
// Node dot
// -----------------------------------------------------------------------------

type NodeState = "done" | "current" | "upcoming" | "muted";

function NodeDot({
  state,
  tone,
  hero,
  animate,
}: {
  state: NodeState;
  tone: Tone;
  hero: boolean;
  animate: boolean;
}) {
  // done 16 / current 24 / upcoming 14 (hero, a legible stepper); 6 / 10 / 6
  // (compact) — enlarged from spec §3.3 for the restyle.
  const size =
    state === "current"
      ? hero
        ? "h-6 w-6"
        : "h-2.5 w-2.5"
      : state === "upcoming"
        ? hero
          ? "h-3.5 w-3.5"
          : "h-1.5 w-1.5"
        : hero
          ? "h-4 w-4"
          : "h-1.5 w-1.5";

  const style: React.CSSProperties =
    state === "current"
      ? {
          background: TONE_COLOR[tone],
          // pine fill + panel-gap ring + soft brand ring (spec's exact recipe).
          boxShadow: hero
            ? `0 0 0 3px var(--bone), 0 0 0 7px ${TONE_RING[tone]}`
            : `0 0 0 1.5px var(--bone), 0 0 0 3px ${TONE_RING[tone]}`,
        }
      : state === "done"
        ? { background: TONE_COLOR[tone], boxShadow: hero ? "0 0 0 3px var(--bone)" : undefined }
        : state === "muted"
          ? { background: "var(--line)" }
          : {
              background: "var(--paper)",
              boxShadow: hero ? "0 0 0 2px var(--line)" : "0 0 0 1.5px var(--line)",
            };

  // The current node breathes: an expanding, fading halo behind the dot
  // (ambient — disabled under prefers-reduced-motion via the CSS block).
  if (state === "current") {
    return (
      <span className={cn("relative shrink-0", size)} aria-hidden>
        {animate ? (
          <span
            className="rj-halo absolute inset-0 rounded-full"
            style={{ background: TONE_COLOR[tone], opacity: 0.25 }}
          />
        ) : null}
        <span
          className={cn(
            "relative flex h-full w-full items-center justify-center rounded-full",
            animate && "rj-pop",
          )}
          style={style}
        >
          {hero ? <span className="h-2 w-2 rounded-full bg-white/90" /> : null}
        </span>
      </span>
    );
  }

  // Done hero nodes carry a check — the rail reads as a real stepper.
  if (state === "done" && hero) {
    return (
      <span
        className={cn("flex shrink-0 items-center justify-center rounded-full", size)}
        style={style}
        aria-hidden
      >
        <Check className="h-2.5 w-2.5 text-white" strokeWidth={3} />
      </span>
    );
  }

  return <span className={cn("block shrink-0 rounded-full", size)} style={style} aria-hidden />;
}

// -----------------------------------------------------------------------------
// The Journey
// -----------------------------------------------------------------------------

export function Journey({
  kind,
  stage,
  transitions = [],
  variant,
  pendingActions = [],
  canTransition = false,
  isPending = false,
  pendingStage = null,
  onTransition,
  className,
}: JourneyProps) {
  const { t } = useTranslation("cases");
  const { t: tc } = useTranslation("common");

  const hero = variant === "hero";
  const nodes = NODES_BY_KIND[kind] ?? ACCOUNT_PHASES;
  const n = nodes.length;
  const isPostSale = POST_SALE.has(kind);
  const closed = stage === "closed";
  const suspended = stage === "collection_suspended";

  const nodeLabel = (node: RailNode) =>
    isPostSale
      ? t(`stages.${node.id}`, { defaultValue: node.id })
      : t(`journey.phases.${node.id}`, { defaultValue: node.id });

  // Where the case is on the rail. Suspended sits on the branch: the main-rail
  // progress is its branch point (`in_progress`).
  const anchorStage: CaseStage = suspended ? "collection_in_progress" : stage;
  const currentIdx = closed
    ? -1
    : nodes.findIndex((node) => node.stages.includes(anchorStage));

  const railTone: Tone = suspended ? "warn" : "brand";
  const currentTone: Tone = NODE_TONE[stage] ?? "brand";

  // Micro-stage position inside the current phase (hero ticks + `2/3` badge).
  const currentNode = currentIdx >= 0 ? nodes[currentIdx] : null;
  const microTotal = currentNode?.stages.length ?? 1;
  const microIdx = currentNode ? Math.max(0, currentNode.stages.indexOf(anchorStage)) : 0;

  // Node centers as percentages of the rail width.
  const center = (i: number) => ((i + 0.5) / n) * 100;
  const first = center(0);
  const last = center(n - 1);
  // The pine stroke reaches the current node and walks partway into its
  // segment as micro-stages complete — one continuous line, not steps.
  const doneEnd =
    closed || currentIdx < 0
      ? null
      : center(currentIdx) +
        (currentIdx < n - 1 && microTotal > 1
          ? (microIdx / microTotal) * (center(currentIdx + 1) - center(currentIdx))
          : 0);

  // ---------------------------------------------------------------------------
  // Transitions, in rail order (server options only — never a local rule).
  // ---------------------------------------------------------------------------
  const railStages = CASE_STAGE_FLOW[kind] ?? [];
  const orderOf = (s: CaseStage) => {
    if (s === "closed") return 999;
    const i = railStages.indexOf(s);
    return i === -1 ? 500 : i;
  };
  const sorted = React.useMemo(
    () => [...transitions].sort((a, b) => orderOf(a.to_stage) - orderOf(b.to_stage)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [transitions, kind],
  );
  const currentRailIdx = railStages.indexOf(stage);
  const forward = sorted.filter(
    (o) => o.to_stage !== "closed" && orderOf(o.to_stage) > currentRailIdx,
  );
  const nextAllowed = forward.find((o) => o.allowed) ?? null;
  const nextBlocked = !nextAllowed && forward.length > 0 ? forward[0] : null;
  const closeOption = sorted.find((o) => o.to_stage === "closed") ?? null;
  const otherOptions = sorted.filter(
    (o) => o.to_stage !== "closed" && o !== nextAllowed,
  );

  const optionsByStage = React.useMemo(() => {
    const map = new Map<CaseStage, CaseTransitionOption>();
    for (const option of transitions) map.set(option.to_stage, option);
    return map;
  }, [transitions]);

  /** Hover reason for an upcoming node — server reason, `notReachable` otherwise. */
  const nodeHint = (node: RailNode, state: NodeState): string | null => {
    if (!hero || state === "done" || state === "current" || closed) return null;
    const offered = node.stages
      .map((s) => optionsByStage.get(s))
      .filter((o): o is CaseTransitionOption => !!o);
    if (offered.length === 0) return t("journey.notReachable");
    if (offered.some((o) => o.allowed)) return null;
    return offered[0].reason ?? t("journey.blocked");
  };

  const stageLabel = t(`stages.${stage}`, { defaultValue: stage });
  const phaseLabel = isPostSale
    ? t(`journey.phases.${KIND_PHASE[kind] ?? kind}`)
    : currentNode
      ? nodeLabel(currentNode)
      : stageLabel;

  const act = (to: CaseStage) => {
    if (onTransition) onTransition(to);
  };

  // ---------------------------------------------------------------------------
  // The rail (shared by both variants)
  // ---------------------------------------------------------------------------
  const nodeRowH = hero ? "h-8" : "h-3";
  const svgTop = hero ? 12 : 2;

  const rail = (
    <div className="relative">
      <svg
        className="absolute inset-x-0"
        style={{ top: svgTop }}
        height="8"
        width="100%"
        viewBox="0 0 100 8"
        preserveAspectRatio="none"
        aria-hidden
        focusable="false"
      >
        {/* the road */}
        <line
          x1={first}
          y1="4"
          x2={last}
          y2="4"
          stroke="var(--line)"
          strokeWidth={hero ? 3 : 2}
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
        {/* the completed portion — pine, drawn once on mount (hero) */}
        {doneEnd !== null && doneEnd > first ? (
          <line
            x1={first}
            y1="4"
            x2={doneEnd}
            y2="4"
            stroke={TONE_COLOR[railTone]}
            strokeWidth={hero ? 2.5 : 2}
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
            pathLength={1}
            className={hero ? "rj-draw" : undefined}
          />
        ) : null}
        {/* micro-stage ticks on the current phase's segment (hero only) */}
        {hero && !closed && currentIdx >= 0 && currentIdx < n - 1 && microTotal > 1
          ? Array.from({ length: microTotal - 1 }, (_, j) => {
              const frac = (j + 1) / microTotal;
              const x =
                center(currentIdx) + frac * (center(currentIdx + 1) - center(currentIdx));
              return (
                <line
                  key={j}
                  x1={x}
                  y1="2.5"
                  x2={x}
                  y2="5.5"
                  stroke={j < microIdx ? "var(--brand-deep)" : "var(--line)"}
                  strokeWidth={1.5}
                  vectorEffect="non-scaling-stroke"
                />
              );
            })
          : null}
      </svg>

      <div
        className="relative grid"
        style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}
      >
        {nodes.map((node, i) => {
          const state: NodeState = closed
            ? "muted"
            : i < currentIdx
              ? "done"
              : i === currentIdx && !suspended
                ? "current"
                : "upcoming";
          const hint = nodeHint(node, state);
          const column = (
            <div className="flex min-w-0 flex-col items-center gap-2 px-1">
              <span className={cn("flex items-center justify-center", nodeRowH)}>
                <NodeDot
                  state={state}
                  tone={state === "current" ? currentTone : railTone}
                  hero={hero}
                  animate={hero}
                />
              </span>
              {hero ? (
                <span
                  className={cn(
                    "whitespace-nowrap text-center text-caption leading-tight",
                    state === "current"
                      ? "font-semibold text-ink"
                      : state === "done"
                        ? "font-medium text-ink-2"
                        : "text-ink-3",
                  )}
                >
                  {nodeLabel(node)}
                  {state === "current" && microTotal > 1 ? (
                    <span className="ml-1 tabular-nums text-ink-3">
                      {t("journey.progress", { done: microIdx + 1, total: microTotal })}
                    </span>
                  ) : null}
                </span>
              ) : null}
            </div>
          );
          return hint ? (
            <DisabledHint key={node.id} hint={hint}>
              {column}
            </DisabledHint>
          ) : (
            <React.Fragment key={node.id}>{column}</React.Fragment>
          );
        })}
      </div>

      {/* collection's suspension branch — a side state below the line, not a step */}
      {hero && kind === "collection" && !closed ? (
        <div className="relative h-7">
          <div
            className="absolute top-1 flex -translate-x-1/2 items-center gap-1.5"
            style={{ left: `${(center(1) + center(3)) / 2}%` }}
          >
            <NodeDot
              state={suspended ? "current" : "upcoming"}
              tone="danger"
              hero={hero}
              animate={hero}
            />
            <span
              className={cn(
                "whitespace-nowrap text-caption",
                suspended ? "font-medium" : "text-ink-3 opacity-70",
              )}
              style={suspended ? { color: TONE_TEXT.danger } : undefined}
            >
              {t("stages.collection_suspended")}
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );

  // ---------------------------------------------------------------------------
  // Compact — rail + `{phase} · {stage}` inline; nothing interactive.
  // ---------------------------------------------------------------------------
  if (!hero) {
    return (
      <div className={cn("flex min-w-0 items-center gap-3", className)}>
        <div className="min-w-[96px] flex-1">{rail}</div>
        <span
          className={cn(
            "shrink-0 whitespace-nowrap text-caption",
            closed ? "text-ink-3" : "text-ink-2",
          )}
          style={
            !closed && currentTone !== "brand"
              ? { color: TONE_TEXT[currentTone] }
              : undefined
          }
        >
          {closed
            ? t("journey.closedChip")
            : // A phase whose only stage shares its name (Comparación, Vigente…)
              // would otherwise read "Comparación · Comparación".
              phaseLabel === stageLabel
              ? stageLabel
              : `${phaseLabel} · ${stageLabel}`}
        </span>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Hero — the rail plus the current block: stage name, qué sigue, actions.
  // ---------------------------------------------------------------------------
  const advancing = isPending && pendingStage != null && pendingStage !== "closed";
  const noPermissionHint = !canTransition ? t("journey.noPermission") : null;

  // The account's open gaps, folded to one glanceable chip on the current
  // stage: total count + whether any is a hard blocker (recolors the chip).
  const pendingCount = pendingActions.reduce((sum, a) => sum + (a.count || 1), 0);
  const hasBlocker = pendingActions.some((a) => a.severity === "blocker");

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <style>{JOURNEY_CSS}</style>

      <div className="overflow-x-auto pb-1">
        <div className={cn("pt-2", n >= 7 ? "min-w-[720px]" : n >= 6 ? "min-w-[620px]" : "min-w-[480px]")}>
          {rail}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-64">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-h2 font-semibold tracking-tight text-ink">
              {closed ? t("stages.closed") : stageLabel}
            </h3>
            {closed ? <Badge variant="muted">{t("journey.closedChip")}</Badge> : null}
            {!closed && pendingCount > 0 ? (
              <Badge variant={hasBlocker ? "danger" : "warn"} dot>
                {t("journey.pending", { count: pendingCount })}
              </Badge>
            ) : null}
          </div>

          {/* Qué sigue — the first allowed forward step, or the server's
              blocking reason for the immediate next one, verbatim. */}
          {!closed && nextAllowed ? (
            <p className="mt-0.5 text-pretty text-caption text-ink-2">
              <span className="font-medium text-ink">
                {t("journey.whatNext")}:
              </span>{" "}
              {t(`journey.next.${nextAllowed.to_stage}`, {
                defaultValue: t(`stages.${nextAllowed.to_stage}`, {
                  defaultValue: nextAllowed.to_stage,
                }),
              })}
            </p>
          ) : !closed && nextBlocked ? (
            <p className="mt-0.5 flex items-start gap-1.5 text-pretty text-caption text-ink-3">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>{nextBlocked.reason ?? t("journey.blocked")}</span>
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {!closed && nextAllowed ? (
            <DisabledHint hint={noPermissionHint}>
              <Button
                size="sm"
                disabled={!canTransition || isPending}
                onClick={() => act(nextAllowed.to_stage)}
              >
                {advancing && pendingStage === nextAllowed.to_stage ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : null}
                {t("journey.advanceTo", {
                  stage: t(`stages.${nextAllowed.to_stage}`, {
                    defaultValue: nextAllowed.to_stage,
                  }),
                })}
              </Button>
            </DisabledHint>
          ) : null}

          {otherOptions.length > 0 ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" disabled={isPending}>
                  {t("journey.otherStages")}
                  <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="max-w-xs">
                {otherOptions.map((option) => {
                  const enabled = option.allowed && canTransition && !isPending;
                  const reason = !canTransition
                    ? t("journey.noPermission")
                    : (option.reason ?? t("journey.blocked"));
                  return (
                    <DropdownMenuItem
                      key={option.to_stage}
                      onSelect={(event) => {
                        if (!enabled) {
                          event.preventDefault();
                          return;
                        }
                        act(option.to_stage);
                      }}
                      className={cn(
                        "flex-col items-start gap-0.5",
                        !enabled && "cursor-not-allowed opacity-60",
                      )}
                    >
                      <span>
                        {t(`stages.${option.to_stage}`, {
                          defaultValue: option.to_stage,
                        })}
                      </span>
                      {!enabled ? (
                        <span className="text-pretty text-caption text-ink-3">
                          {reason}
                        </span>
                      ) : null}
                    </DropdownMenuItem>
                  );
                })}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}

          {!closed && closeOption ? (
            <DisabledHint
              hint={
                noPermissionHint ??
                (!closeOption.allowed
                  ? (closeOption.reason ?? t("journey.blocked"))
                  : null)
              }
            >
              <Button
                variant="ghost"
                size="sm"
                disabled={!closeOption.allowed || !canTransition || isPending}
                onClick={() => act("closed")}
              >
                {isPending && pendingStage === "closed" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : null}
                {tc("actions.close")}
              </Button>
            </DisabledHint>
          ) : null}
        </div>
      </div>
    </div>
  );
}
