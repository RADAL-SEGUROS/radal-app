/**
 * Agent skills (v12, docs/v12/05-agent.md §3) — three small pieces:
 *
 * - `SkillsStrip`: the catalogue from `GET /ai/skills` as toggle chips; the
 *   pressed ones are the thread's active skills (`POST /ai/threads/{id}/skills`
 *   replaces the set). Titles and descriptions are the server's Spanish copy.
 * - `ActiveSkillChips`: the active skills shown beside the composer's context
 *   chips, each removable — the working method stays visible, like the refs.
 * - `SkillSuggestion` + `suggestSkill()`: while the user types, the skill whose
 *   server `triggers` match best is offered (never activated by itself).
 */
import { useTranslation } from "react-i18next";
import { BookOpen, Check, Sparkles, X } from "lucide-react";
import type { AgentSkill } from "@/api/ai";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

// --- Trigger matching -----------------------------------------------------------

/** Lowercase, accent-free — "Cotización" and "cotizacion" match alike. */
function normalize(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * The best-matching skill for what the user is typing, or `null`.
 * Whole-word matches only; the score is the number of distinct triggers hit.
 * `exclude` = active + dismissed keys.
 */
export function suggestSkill(
  text: string,
  skills: AgentSkill[] | undefined,
  exclude: ReadonlySet<string>,
): AgentSkill | null {
  const haystack = normalize(text);
  if (!skills?.length || haystack.trim().length < 3) return null;
  let best: AgentSkill | null = null;
  let bestScore = 0;
  for (const skill of skills) {
    if (exclude.has(skill.key)) continue;
    let score = 0;
    for (const trigger of skill.triggers ?? []) {
      const needle = normalize(trigger).trim();
      if (!needle) continue;
      const re = new RegExp(`(^|[^a-z0-9])${escapeRegExp(needle)}($|[^a-z0-9])`);
      if (re.test(haystack)) score += 1;
    }
    if (score > bestScore) {
      best = skill;
      bestScore = score;
    }
  }
  return best;
}

// --- Strip ------------------------------------------------------------------------

export function SkillsStrip({
  skills,
  activeKeys,
  onToggle,
  isLoading,
  isError,
  disabled,
}: {
  skills: AgentSkill[] | undefined;
  activeKeys: readonly string[];
  onToggle: (key: string) => void;
  isLoading?: boolean;
  isError?: boolean;
  disabled?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const active = new Set(activeKeys);

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex items-center gap-1.5 text-caption font-medium text-ink-3">
        <BookOpen className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
        <span>{t("agent.skills.title")}</span>
        <span className="hidden font-normal text-ink-3 md:inline">· {t("agent.skills.hint")}</span>
      </div>
      {isLoading ? (
        <div className="flex gap-2">
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-8 w-28" />
          <Skeleton className="h-8 w-36" />
        </div>
      ) : isError ? (
        <p className="text-caption text-neg-text">{t("agent.skills.loadError")}</p>
      ) : !skills?.length ? (
        <p className="text-caption text-ink-3">{t("agent.skills.empty")}</p>
      ) : (
        // One scrollable row on a phone, wrapping from `sm`.
        <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 sm:flex-wrap sm:overflow-visible sm:pb-0">
          {skills.map((skill) => {
            const on = active.has(skill.key);
            return (
              <button
                key={skill.key}
                type="button"
                aria-pressed={on}
                disabled={disabled}
                title={skill.description}
                aria-label={
                  on
                    ? t("agent.skills.deactivate", { title: skill.title })
                    : t("agent.skills.activate", { title: skill.title })
                }
                onClick={() => onToggle(skill.key)}
                className={cn(
                  "inline-flex min-h-10 shrink-0 items-center gap-1.5 rounded-full px-3 text-caption font-medium sm:min-h-8",
                  "transition-[background-color,color,border-color,scale] duration-150 ease-out active:scale-[0.96]",
                  "disabled:cursor-not-allowed disabled:opacity-60",
                  on
                    ? "border border-brand-line bg-brand-soft text-brand-deep"
                    : "border border-line bg-bone text-ink-2 hover:border-line-strong hover:text-ink",
                )}
              >
                {on ? (
                  <Check className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
                ) : (
                  <Sparkles className="h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} aria-hidden />
                )}
                <span className="whitespace-nowrap">{skill.title}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- Active chips beside the composer --------------------------------------------------

export function ActiveSkillChips({
  skills,
  onRemove,
  disabled,
}: {
  skills: AgentSkill[];
  onRemove: (key: string) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  if (skills.length === 0) return null;
  return (
    <>
      {skills.map((skill) => (
        <span
          key={skill.key}
          title={skill.description}
          className="inline-flex max-w-full items-center gap-1.5 rounded-[5px] border border-brand-line bg-bone py-[3px] pe-1 ps-2 text-caption text-brand-deep"
        >
          <BookOpen className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} aria-hidden />
          <span className="truncate">{skill.title}</span>
          <button
            type="button"
            disabled={disabled}
            aria-label={t("agent.skills.deactivate", { title: skill.title })}
            onClick={() => onRemove(skill.key)}
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[4px] transition-[background-color] duration-150 ease-out hover:bg-brand-soft disabled:opacity-50 sm:h-5 sm:w-5"
          >
            <X className="h-3 w-3" strokeWidth={2} aria-hidden />
          </button>
        </span>
      ))}
    </>
  );
}

// --- Suggestion bar ------------------------------------------------------------------------

export function SkillSuggestion({
  skill,
  onActivate,
  onDismiss,
  disabled,
}: {
  skill: AgentSkill;
  onActivate: () => void;
  onDismiss: () => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div
      role="status"
      // Stacks on a phone: beside the buttons the text was squeezed into a
      // one-word column 300+ px tall (QA v12, 375 px).
      className="flex flex-col gap-2 rounded-lg bg-brand-soft px-3 py-2 text-caption text-brand-deep sm:flex-row sm:items-center"
    >
      <div className="flex min-w-0 flex-1 items-start gap-2">
        <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.5} aria-hidden />
        <span className="min-w-0 flex-1 [text-wrap:pretty]">
          {t("agent.skills.suggest", { title: skill.title })}{" "}
          <span className="text-ink-2">{skill.description}</span>
        </span>
      </div>
      <div className="flex items-center gap-1.5 self-end sm:self-auto">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="min-h-10 sm:min-h-8"
          disabled={disabled}
          onClick={onActivate}
        >
          {t("agent.skills.suggestAction")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="min-h-10 sm:min-h-8"
          onClick={onDismiss}
        >
          {t("agent.skills.dismiss")}
        </Button>
      </div>
    </div>
  );
}
