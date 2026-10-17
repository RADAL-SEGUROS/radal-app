/**
 * Turning a `GroupTimelineEntry` into screen copy.
 *
 * `GET /account-groups/{id}/timeline` sends IDENTIFIERS and enum TOKENS, never
 * prose: CLAUDE.md rule 1 keeps Spanish out of the API, and rule 4 puts every
 * label in the locale files. So the row's words are built here, from the
 * namespaces that already own each enum:
 *
 *   stage        -> cases:stages.<CaseStage>
 *   policy       -> postsale:policy.status.<PolicyStatus>
 *   endorsement  -> postsale:endorsement.kind.<EndorsementKind>
 *   claim        -> postsale:claim.status.<ClaimStatus>
 *   collection   -> postsale:collection.status.<CollectionPlanStatus>
 *
 * Each token is read through an explicit enum → literal-key map from
 * `src/i18n/labels.ts` (never a constructed key; docs/technical-reference.md
 * §9.5). A token the map does not know falls back to the raw token, so it
 * shows up in review instead of collapsing into a blank line or a raw key.
 *
 * `detail` is different — it is text the user typed themself (a note body, a
 * stage note), so it is passed through untouched.
 */
import { useCallback } from "react";
import i18n from "@/i18n";
import { useTranslation } from "react-i18next";
import {
  CASE_STAGE_LABEL,
  CLAIM_STATUS_LABEL,
  COLLECTION_PLAN_STATUS_LABEL,
  ENDORSEMENT_KIND_LABEL,
  GROUP_TIMELINE_KIND_LABEL,
  GROUP_TIMELINE_NOTE_LABEL,
  POLICY_STATUS_LABEL,
  enumLabel,
} from "@/i18n/labels";

/**
 * Both timelines — `GET /account-groups/{id}/timeline` and
 * `GET /case-files/{id}/timeline` — send the same token contract, so one
 * structural type serves both rather than two near-identical renderers.
 */
export interface TranslatableTimelineEntry {
  kind: string;
  occurred_at?: string | null;
  case_file_id?: number | null;
  title: string;
  detail: string | null;
  detail_token: string | null;
  from_stage: string | null;
  to_stage: string | null;
  /** Case timeline only — the event's own meta (`{skipped: true}` on an omitida). */
  meta?: Record<string, unknown> | null;
  /** Server flag for an omitida stage event (group timeline, v12 review). */
  is_skipped?: boolean | null;
  /** Server flag: `occurred_at` is a calendar date (a policy's start), not an instant. */
  date_only?: boolean | null;
  /** Set by `collapseSkipped`: this arrival came FROM an omitted stage. */
  from_skipped?: boolean;
  /**
   * Set by `collapseSkipped` (or by a server that already collapsed the run):
   * the stage tokens of a run of omitida events, rendered as ONE line.
   */
  skipped_stages?: string[] | null;
}

export interface TimelineCopy {
  /** Badge text: what kind of movement this is. */
  kindLabel: string;
  /** Headline: the translated stage, or the entity's own identifier. */
  label: string;
  /** Sub-line: the translated enum, or the user's own words. */
  detail: string | null;
}

/** Entry kind → the explicit label map its `detail_token` is read through. */
const TOKEN_LABEL: Record<string, Readonly<Record<string, string>>> = {
  policy: POLICY_STATUS_LABEL,
  endorsement: ENDORSEMENT_KIND_LABEL,
  claim: CLAIM_STATUS_LABEL,
  collection: COLLECTION_PLAN_STATUS_LABEL,
};

/** Activity `action` → literal key. Unknown actions read as a generic movement. */
const ACTIVITY_ACTION_LABEL: Readonly<Record<string, string>> = {
  "case_file.created": "accounts:timeline.actions.created",
  "case_file.updated": "accounts:timeline.actions.updated",
  "case_file.transitioned": "accounts:timeline.actions.transitioned",
  "case_file.skipped_to": "accounts:timeline.actions.skipped_to",
  "case_file.stage_activated": "accounts:timeline.actions.stage_activated",
  "case_file.versioned": "accounts:timeline.actions.versioned",
  "case_file.renewed": "accounts:timeline.actions.renewed",
  "case_file.reperiod": "accounts:timeline.actions.reperiod",
  "case_file.deleted": "accounts:timeline.actions.deleted",
  "case_pack.generated": "accounts:timeline.actions.pack_generated",
  "case_pack.summary_confirmed": "accounts:timeline.actions.pack_summary_confirmed",
  "extraction.committed": "accounts:timeline.actions.extraction_committed",
  "note.created": "accounts:timeline.actions.note_created",
  "policy.created": "accounts:timeline.actions.policy_created",
  "policy.uploaded": "accounts:timeline.actions.policy_created",
  "policy.created_from_proposal": "accounts:timeline.actions.policy_created",
  "policy.completed": "accounts:timeline.actions.policy_completed",
  "policy.corrected": "accounts:timeline.actions.policy_corrected",
};

const STAGE_TOKEN = /\b[a-z]+(?:_[a-z]+)*\b/g;
/** Internal rule codes ("R42") never reach the screen, with their dangling glue. */
const RULE_CODE = /\s*\(\s*R\d{1,3}(?:\s*,\s*R\d{1,3})*\s*\)|,?\s*\bR\d{1,3}\b,?/g;

/** True for an omitida stage event, whichever way the server flags it. */
export function isSkippedEntry(entry: TranslatableTimelineEntry): boolean {
  if (entry.kind !== "stage") return false;
  if (entry.is_skipped) return true;
  if (entry.meta && entry.meta["skipped"] === true) return true;
  // The group timeline sends no meta: the system-written note is the marker.
  return !!entry.detail && /^Etapa omitida\b/.test(entry.detail);
}

/**
 * Fold every run of consecutive omitida stage events of the same folder into
 * ONE row ("10 etapas omitidas"): an account that enters with its policy
 * writes one event per stage it never lived, and a dozen identical lines per
 * account drowned the bitácora (review v12 #3). Order is preserved; the
 * folded row keeps the run's first `occurred_at` in display order.
 */
export function collapseSkipped<T extends TranslatableTimelineEntry>(
  entries: T[],
): (T & TranslatableTimelineEntry)[] {
  const caseKey = (entry: TranslatableTimelineEntry) => entry.case_file_id ?? null;
  const instant = (entry: TranslatableTimelineEntry) =>
    entry.occurred_at ? Date.parse(entry.occurred_at) : Number.NaN;

  // A jump writes ONE `case_file.skipped_to` activity next to its omitida
  // events ("La cuenta entró con la póliza; 11 etapas omitidas"): that line
  // IS the collapsed summary, so the events it covers are not repeated.
  const jumps = entries.filter(
    (entry) => entry.kind === "activity" && entry.title === "case_file.skipped_to",
  );
  const coveredByJump = (entry: TranslatableTimelineEntry) =>
    jumps.some((jump) => {
      if (caseKey(jump) !== caseKey(entry)) return false;
      const gap = instant(jump) - instant(entry);
      return Number.isFinite(gap) && Math.abs(gap) <= JUMP_WINDOW_MS;
    });

  const skippedStages = new Set(
    entries
      .filter(isSkippedEntry)
      .map((entry) => `${caseKey(entry)}|${entry.to_stage ?? entry.title}`),
  );

  const out: (T & TranslatableTimelineEntry)[] = [];
  for (const entry of entries) {
    if (!isSkippedEntry(entry)) {
      // An arrival from an omitted stage reads as the stage it reached, not
      // as "Ratificación → Póliza emitida" (ratification never happened).
      const fromSkipped =
        entry.kind === "stage" &&
        !!entry.from_stage &&
        skippedStages.has(`${caseKey(entry)}|${entry.from_stage}`);
      out.push(fromSkipped ? { ...entry, from_skipped: true } : entry);
      continue;
    }
    if (coveredByJump(entry)) continue;
    const last = out[out.length - 1];
    const stage = entry.to_stage ?? entry.title;
    if (last && last.skipped_stages && caseKey(last) === caseKey(entry)) {
      last.skipped_stages = [...last.skipped_stages, stage];
      continue;
    }
    out.push({ ...entry, skipped_stages: [stage] });
  }
  return out;
}

/** How close (ms) an omitida event sits to the jump activity that wrote it. */
const JUMP_WINDOW_MS = 10_000;

/** `T00:00:00` with any zero offset (or none) = a calendar date sent as a datetime. */
const MIDNIGHT = /^(\d{4}-\d{2}-\d{2})(?:[T ]00:00(?::00(?:\.0+)?)?(?:Z|[+-]00:?00)?)?$/;
const HAS_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/;

const CHILE = "America/Santiago";

/**
 * Kinds whose `occurred_at` comes from a CONTRACTUAL date (issue / start /
 * effect / due date — wall-clock values under the noon convention): their
 * hour is not an event time, so the bitácora prints the date alone.
 */
export const DATE_ONLY_TIMELINE_KINDS: ReadonlySet<string> = new Set([
  "policy",
  "endorsement",
  "claim",
  "collection",
]);

function intlLocale(): string {
  return i18n.language?.startsWith("en") ? "en-GB" : "es-CL";
}

/**
 * A bitácora instant in Chile time. Event timestamps are UTC instants: a
 * naive string (no `Z`) is UTC by the house rule — the browser would read it
 * as local and show 15:29 for a 12:29 event. A calendar date (a policy's
 * start date sent at midnight) renders as a DATE, never with a fake "00:00"
 * and never shifted into the previous evening.
 */
export function formatTimelineMoment(
  value: string | null | undefined,
  opts: { dateOnly?: boolean } = {},
): string {
  if (!value) return "—";
  const midnight = MIDNIGHT.exec(value.trim());
  const calendar = midnight?.[1] ?? (opts.dateOnly ? /^\d{4}-\d{2}-\d{2}/.exec(value)?.[0] : undefined);
  if (calendar) {
    const [y, m, d] = calendar.split("-").map(Number);
    return new Intl.DateTimeFormat(intlLocale(), {
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(Date.UTC(y, m - 1, d)));
  }
  const iso = HAS_OFFSET.test(value) ? value : `${value.replace(" ", "T")}Z`;
  const instant = new Date(iso);
  if (Number.isNaN(instant.getTime())) return value;
  return new Intl.DateTimeFormat(intlLocale(), {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: CHILE,
  }).format(instant);
}

export function useTimelineCopy(): (entry: TranslatableTimelineEntry) => TimelineCopy {
  const { t } = useTranslation("accounts");
  const { t: tPostsale } = useTranslation("postsale");
  const { t: tCases } = useTranslation("cases");

  return useCallback(
    (entry: TranslatableTimelineEntry): TimelineCopy => {
      const stage = (token: string) => enumLabel(tCases, CASE_STAGE_LABEL, token);
      const isStage = (token: string) =>
        Object.prototype.hasOwnProperty.call(CASE_STAGE_LABEL, token);

      /** System-written prose: stage tokens → labels, rule codes out. */
      const clean = (text: string) =>
        text
          .replace(RULE_CODE, "")
          .replace(/'([a-z_]+)'/g, (whole, token: string) =>
            isStage(token) ? `«${stage(token)}»` : whole,
          )
          .replace(STAGE_TOKEN, (token) => (isStage(token) ? stage(token) : token))
          .replace(/\(\s*\)/g, "")
          .replace(/\s{2,}/g, " ")
          .trim();

      if (entry.skipped_stages && entry.skipped_stages.length) {
        const list = entry.skipped_stages.map(stage).join(", ");
        return {
          kindLabel: enumLabel(t, GROUP_TIMELINE_KIND_LABEL, "stage"),
          label: t("timeline.skippedCollapsed", { count: entry.skipped_stages.length }),
          detail: list,
        };
      }

      let label: string;
      if (entry.kind === "stage" && entry.to_stage) {
        label =
          entry.from_stage && !entry.from_skipped
            ? `${stage(entry.from_stage)} → ${stage(entry.to_stage)}`
            : stage(entry.to_stage);
      } else if (entry.kind === "note") {
        label = enumLabel(t, GROUP_TIMELINE_NOTE_LABEL, entry.title);
      } else if (entry.kind === "activity") {
        label = enumLabel(t, ACTIVITY_ACTION_LABEL, entry.title, t("timeline.actions.other"));
      } else {
        label = entry.title;
      }

      const tokenMap = entry.detail_token
        ? Object.prototype.hasOwnProperty.call(TOKEN_LABEL, entry.kind)
          ? TOKEN_LABEL[entry.kind]
          : undefined
        : undefined;

      let detail: string | null;
      if (tokenMap) {
        detail = enumLabel(tPostsale, tokenMap, entry.detail_token);
      } else if (entry.kind === "activity" && entry.title === "case_file.skipped_to") {
        // Legacy rows read "Etapa lead → active (omitidas: a, b)" — rebuilt
        // from their parts; newer rows already carry the Spanish summary.
        const m = /^Etapa\s+([a-z_]+)\s*→\s*([a-z_]+)(?:\s*\(omitidas:\s*([a-z_,\s]+)\))?/.exec(
          entry.detail ?? "",
        );
        const metaSkipped = entry.meta?.["skipped"];
        const skipped = m?.[3]
          ? m[3].split(",").map((x) => x.trim()).filter(Boolean)
          : Array.isArray(metaSkipped)
            ? (metaSkipped as string[])
            : [];
        const from = m?.[1] ?? (entry.meta?.["from"] as string | undefined);
        const to = m?.[2] ?? (entry.meta?.["to"] as string | undefined);
        detail =
          entry.detail && !m
            ? clean(entry.detail)
            : from && to
              ? `${stage(from)} → ${stage(to)}` +
                (skipped.length
                  ? ` · ${t("timeline.skippedCollapsed", { count: skipped.length })}`
                  : "")
              : null;
      } else if (entry.kind === "activity" || (entry.kind === "stage" && entry.detail)) {
        // Activity descriptions and stage notes are written by the SYSTEM in
        // most cases; scrub the internal tokens either way (a user's own
        // Spanish words carry no snake_case tokens or rule codes).
        detail = entry.detail ? clean(entry.detail) || null : null;
      } else {
        detail = entry.detail ?? null;
      }

      return {
        kindLabel: enumLabel(t, GROUP_TIMELINE_KIND_LABEL, entry.kind),
        label,
        detail,
      };
    },
    [t, tPostsale, tCases],
  );
}
