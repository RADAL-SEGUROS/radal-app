/**
 * The copilot welcome — `/agent` with no thread, the broker's home.
 *
 * Three bands, top to bottom:
 *   1. the greeting ("Hola, {first_name}") and one line of what the copilot
 *      can do;
 *   2. 3–5 FLOATING indicator cards from `GET /ai/copilot/home` — staggered
 *      entrance (100 ms, `cubic-bezier(0.2, 0, 0, 1)`), a slow continuous bob
 *      with a per-card phase so they never move in lockstep, a hover lift.
 *      `prefers-reduced-motion` turns the entrance and the bob off. Each card
 *      opens the server's `link`. On a 375 px phone they become a horizontal
 *      snap strip;
 *   3. the common queries — the server's suggestions (max 6) plus "Ver más",
 *      which reveals the static catalogue grouped Cobranza · Pólizas · Cartera
 *      · Documentos. A click starts a conversation (the page owns that flow).
 *
 * Unknown indicator / suggestion keys are skipped through the explicit maps in
 * `catalog.ts` — never rendered as a raw key.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { motion, useReducedMotion, type Variants } from "framer-motion";
import { Building2, ChevronDown, Loader2, Sparkles, User as UserIcon } from "lucide-react";

import { useCopilotHome, type CopilotIndicator, type CopilotTone } from "@/api/copilot";
import { useAuth } from "@/providers/AuthProvider";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { CountUp } from "@/components/common/motion";
import { formatNumber, formatUF } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  DEFAULT_SUGGESTIONS,
  MAX_INDICATORS,
  MAX_SUGGESTIONS,
  QUERY_CATALOG,
  QUERY_GROUPS,
  indicatorSpec,
  querySpec,
  type IndicatorSpec,
  type QuerySpec,
} from "./catalog";

/** What a click on a query hands the page: the prompt + the skill to load. */
export interface QueryStart {
  key: string;
  prompt: string;
  skill: string | null;
}

const EASE_STAGE = [0.2, 0, 0, 1] as const;

const stagger: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.1, delayChildren: 0.05 } },
};
const rise: Variants = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: EASE_STAGE } },
};

/** Tone → the Signal status trio (icon tile + value colour). */
const TONE_TILE: Record<CopilotTone, string> = {
  neutral: "bg-brand-soft text-brand-deep",
  ok: "bg-pos-soft text-pos-text",
  warn: "bg-warn-soft text-warn-text",
  danger: "bg-neg-soft text-neg-text",
};
const TONE_VALUE: Record<CopilotTone, string> = {
  neutral: "text-ink",
  ok: "text-pos-text",
  warn: "text-warn-text",
  danger: "text-neg-text",
};
const TONE_DOT: Record<CopilotTone, string> = {
  neutral: "bg-brand",
  ok: "bg-pos",
  warn: "bg-warn",
  danger: "bg-neg",
};

/** Explicit grid sizes — a dynamic `lg:grid-cols-${n}` never reaches the CSS. */
const GRID_COLS: Record<number, string> = {
  1: "sm:grid-cols-1",
  2: "sm:grid-cols-2",
  3: "sm:grid-cols-3",
  4: "sm:grid-cols-2 lg:grid-cols-4",
  5: "sm:grid-cols-3 lg:grid-cols-5",
};

function asTone(value: unknown): CopilotTone {
  return value === "warn" || value === "danger" || value === "ok" ? value : "neutral";
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function firstNameOf(fullName: string | undefined | null): string | null {
  const first = fullName?.trim().split(/\s+/)[0];
  return first ? first : null;
}

// --- Page band -----------------------------------------------------------------------

export function Welcome({
  onStart,
  startingKey,
  disabled,
}: {
  onStart: (query: QueryStart) => void;
  /** The query whose conversation is being opened right now (spinner). */
  startingKey: string | null;
  disabled: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const { user } = useAuth();
  const reduce = useReducedMotion();
  const home = useCopilotHome();

  const name = home.data?.first_name?.trim() || firstNameOf(user?.full_name);

  const indicators = (home.data?.indicators ?? [])
    .map((indicator) => ({ indicator, spec: indicatorSpec(indicator.key) }))
    .filter((row): row is { indicator: CopilotIndicator; spec: IndicatorSpec } => !!row.spec)
    .slice(0, MAX_INDICATORS);

  // Server suggestions first (its skill wins), the static defaults otherwise.
  const serverSuggestions = (home.data?.suggestions ?? [])
    .map((s) => {
      const spec = querySpec(s.key);
      return spec ? { spec, skill: s.skill ?? spec.skill } : null;
    })
    .filter((row): row is { spec: QuerySpec; skill: string | null } => !!row);
  const suggestions = (
    serverSuggestions.length > 0
      ? serverSuggestions
      : DEFAULT_SUGGESTIONS.map((key) => querySpec(key))
          .filter((spec): spec is QuerySpec => !!spec)
          .map((spec) => ({ spec, skill: spec.skill }))
  ).slice(0, MAX_SUGGESTIONS);
  const shown = new Set(suggestions.map((s) => s.spec.key));
  const rest = QUERY_CATALOG.filter((spec) => !shown.has(spec.key));

  const [showAll, setShowAll] = React.useState(false);

  const start = (spec: QuerySpec, skill: string | null) =>
    onStart({ key: spec.key, prompt: t(spec.promptKey), skill });

  const scope = home.data?.scope;

  return (
    <div className="flex flex-col gap-8 pb-4 pt-2 sm:pt-6">
      {/* 1 · Greeting */}
      <motion.header
        initial={reduce ? false : { opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: EASE_STAGE }}
        className="flex flex-col gap-3"
      >
        <span className="grid h-10 w-10 place-items-center rounded-lg bg-brand-soft text-brand-deep">
          <Sparkles className="h-5 w-5" strokeWidth={1.5} aria-hidden />
        </span>
        <h1 className="text-display text-ink [text-wrap:balance]">
          {name ? t("agent.home.greeting", { name }) : t("agent.home.greetingAnon")}
        </h1>
        <p className="max-w-[60ch] text-body text-ink-3 [text-wrap:pretty]">
          {t("agent.home.intro")}
        </p>
      </motion.header>

      {/* 2 · Floating indicators */}
      <section aria-labelledby="copilot-numbers" className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <h2 id="copilot-numbers" className="text-label text-ink-2">
            {t("agent.home.indicatorsTitle")}
          </h2>
          {scope === "mine" || scope === "all" ? (
            <span
              className="inline-flex items-center gap-1.5 rounded-full bg-paper-2 px-2.5 py-0.5 text-caption text-ink-3"
              title={scope === "mine" ? t("agent.home.scopeMineHint") : t("agent.home.scopeAllHint")}
            >
              {scope === "mine" ? (
                <UserIcon className="h-3 w-3" strokeWidth={1.75} aria-hidden />
              ) : (
                <Building2 className="h-3 w-3" strokeWidth={1.75} aria-hidden />
              )}
              {scope === "mine" ? t("agent.home.scopeMine") : t("agent.home.scopeAll")}
            </span>
          ) : null}
        </div>

        {home.isLoading ? (
          <div className="-mx-4 flex gap-3 overflow-hidden px-4 sm:mx-0 sm:grid sm:grid-cols-3 sm:px-0 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-[118px] w-[168px] shrink-0 rounded-card sm:w-auto" />
            ))}
          </div>
        ) : home.isError ? (
          <div className="flex flex-wrap items-center gap-3 rounded-lg bg-paper-2 px-3.5 py-2.5">
            <p className="min-w-0 flex-1 text-caption text-ink-3">{t("agent.home.indicatorsError")}</p>
            <Button
              size="sm"
              variant="ghost"
              className="min-h-10 sm:min-h-8"
              disabled={home.isFetching}
              onClick={() => void home.refetch()}
            >
              {t("agent.home.retry")}
            </Button>
          </div>
        ) : indicators.length === 0 ? (
          <p className="rounded-lg bg-paper-2 px-3.5 py-2.5 text-caption text-ink-3">
            {t("agent.home.indicatorsEmpty")}
          </p>
        ) : (
          <motion.ul
            variants={reduce ? undefined : stagger}
            initial={reduce ? false : "hidden"}
            animate="show"
            className={cn(
              // Phone: a horizontal snap strip (vertical padding leaves room
              // for the bob and the hover lift inside the scroller's clip).
              "-mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 py-2",
              "sm:mx-0 sm:grid sm:snap-none sm:overflow-visible sm:px-0",
              GRID_COLS[indicators.length] ?? GRID_COLS[5],
            )}
          >
            {indicators.map(({ indicator, spec }, index) => (
              <motion.li
                key={indicator.key}
                variants={reduce ? undefined : rise}
                className="w-[168px] shrink-0 snap-start sm:w-auto"
              >
                <FloatingIndicator indicator={indicator} spec={spec} index={index} reduce={!!reduce} />
              </motion.li>
            ))}
          </motion.ul>
        )}
      </section>

      {/* 3 · Common queries */}
      <section aria-labelledby="copilot-queries" className="flex flex-col gap-3">
        <div className="flex flex-col gap-0.5">
          <h2 id="copilot-queries" className="text-label text-ink-2">
            {t("agent.home.queriesTitle")}
          </h2>
          <p className="text-caption text-ink-3">{t("agent.home.queriesHint")}</p>
        </div>

        {home.isLoading ? (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-lg" />
            ))}
          </div>
        ) : (
          <motion.ul
            variants={reduce ? undefined : stagger}
            initial={reduce ? false : "hidden"}
            animate="show"
            className="flex flex-wrap gap-2 sm:grid sm:grid-cols-2 lg:grid-cols-3"
          >
            {suggestions.map(({ spec, skill }) => (
              <motion.li key={spec.key} variants={reduce ? undefined : rise} className="min-w-0 sm:w-auto">
                <QueryButton
                  spec={spec}
                  label={t(spec.labelKey)}
                  busy={startingKey === spec.key}
                  disabled={disabled}
                  onClick={() => start(spec, skill)}
                />
              </motion.li>
            ))}
          </motion.ul>
        )}

        {rest.length > 0 ? (
          <div className="flex flex-col gap-4">
            <Button
              variant="ghost"
              size="sm"
              className="min-h-10 self-start sm:min-h-8"
              aria-expanded={showAll}
              aria-controls="copilot-catalog"
              onClick={() => setShowAll((v) => !v)}
            >
              <ChevronDown
                className={cn(
                  "h-4 w-4 transition-transform duration-150 ease-out",
                  showAll && "rotate-180",
                )}
                aria-hidden
              />
              {showAll ? t("agent.home.showLess") : t("agent.home.showMore")}
            </Button>

            {showAll ? (
              <motion.div
                id="copilot-catalog"
                initial={reduce ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25, ease: EASE_STAGE }}
                className="grid gap-5 sm:grid-cols-2"
              >
                {QUERY_GROUPS.map((group) => {
                  const items = rest.filter((spec) => spec.group === group.key);
                  if (items.length === 0) return null;
                  return (
                    <div key={group.key} className="flex flex-col gap-2">
                      <h3 className="text-caption font-medium text-ink-3">{t(group.labelKey)}</h3>
                      <ul className="flex flex-wrap gap-2 sm:flex-col">
                        {items.map((spec) => (
                          <li key={spec.key} className="min-w-0">
                            <QueryButton
                              spec={spec}
                              label={t(spec.labelKey)}
                              busy={startingKey === spec.key}
                              disabled={disabled}
                              onClick={() => start(spec, spec.skill)}
                              compact
                            />
                          </li>
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </motion.div>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}

// --- Pieces ------------------------------------------------------------------------------

function FloatingIndicator({
  indicator,
  spec,
  index,
  reduce,
}: {
  indicator: CopilotIndicator;
  spec: IndicatorSpec;
  index: number;
  reduce: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const tone = asTone(indicator.tone);
  const Icon = spec.icon;
  const value = asNumber(indicator.value);
  const secondary = asNumber(indicator.secondary);
  const isUf = indicator.unit === "uf";
  const label = t(spec.labelKey);

  const format = (n: number) => (isUf ? formatUF(n, { decimals: 0 }) : formatNumber(n));
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <span className={cn("grid h-8 w-8 place-items-center rounded-sm", TONE_TILE[tone])}>
          <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden />
        </span>
        {tone !== "neutral" ? (
          <span className={cn("mt-1 h-1.5 w-1.5 rounded-full", TONE_DOT[tone])} aria-hidden />
        ) : null}
      </div>
      <div className="mt-3 flex flex-col">
        <span className={cn("text-kpi tabular-nums", TONE_VALUE[tone])}>
          {value === null ? (
            typeof indicator.value === "string" && indicator.value ? indicator.value : "—"
          ) : (
            <CountUp value={value} format={format} duration={900} />
          )}
        </span>
        {secondary !== null ? (
          <span className="mt-0.5 text-caption tabular-nums text-ink-3">
            {formatUF(secondary, { decimals: 0 })}
          </span>
        ) : typeof indicator.secondary === "string" && indicator.secondary ? (
          <span className="mt-0.5 truncate text-caption text-ink-3">{indicator.secondary}</span>
        ) : null}
        <span className="mt-1.5 text-caption text-ink-2 [text-wrap:pretty]">{label}</span>
      </div>
    </>
  );

  const surface =
    "block h-full rounded-card border border-line bg-bone p-4 text-left shadow-elev";
  const interactive =
    "outline-none transition-[transform,box-shadow,border-color] duration-150 ease-out hover:-translate-y-1 hover:border-line-strong hover:shadow-elev-hover focus-visible:ring-2 focus-visible:ring-brand-ring active:scale-[0.98]";

  const link = indicator.link?.trim() || null;
  const card = !link ? (
    <div className={surface}>{body}</div>
  ) : /^https?:\/\//.test(link) ? (
    <a href={link} target="_blank" rel="noreferrer" className={cn(surface, interactive)} aria-label={label}>
      {body}
    </a>
  ) : (
    <Link to={link} className={cn(surface, interactive)} title={t("agent.home.openIndicator")}>
      {body}
    </Link>
  );

  if (reduce) return card;
  // The bob lives on its own wrapper so it never fights the entrance (parent
  // `li`) or the hover lift (the card's CSS transform). Per-card duration and
  // delay give each one its own phase.
  return (
    <motion.div
      className="h-full"
      animate={{ y: [0, -5, 0] }}
      transition={{
        duration: 4.6 + (index % 3) * 0.7,
        delay: 0.8 + index * 0.35,
        repeat: Infinity,
        ease: "easeInOut",
      }}
    >
      {card}
    </motion.div>
  );
}

function QueryButton({
  spec,
  label,
  busy,
  disabled,
  onClick,
  compact,
}: {
  spec: QuerySpec;
  label: string;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
  compact?: boolean;
}) {
  const Icon = spec.icon;
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      aria-busy={busy || undefined}
      className={cn(
        "group flex w-full min-w-0 items-center gap-2.5 rounded-lg border border-line bg-bone text-left text-label text-ink-2 shadow-elev",
        "outline-none transition-[transform,box-shadow,border-color,color] duration-150 ease-out",
        "hover:border-line-strong hover:text-ink hover:shadow-elev-hover focus-visible:ring-2 focus-visible:ring-brand-ring active:scale-[0.98]",
        "disabled:cursor-not-allowed disabled:opacity-60 disabled:active:scale-100",
        compact ? "min-h-10 px-3 py-1.5" : "min-h-12 px-3 py-2.5",
      )}
    >
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-sm bg-paper-2 text-ink-3 transition-[background-color,color] duration-150 ease-out group-hover:bg-brand-soft group-hover:text-brand-deep">
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <Icon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
        )}
      </span>
      <span className="min-w-0 [text-wrap:pretty]">{label}</span>
    </button>
  );
}
