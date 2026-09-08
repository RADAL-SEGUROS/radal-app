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
 * These are dynamic keys, which `tsc` cannot check (docs/technical-reference.md §9.5). Every lookup
 * therefore carries `defaultValue: <token>`: a member we forgot to translate
 * shows up as the raw token in review instead of collapsing into a blank line.
 *
 * `detail` is different — it is text the user typed themself (a note body, a
 * stage note), so it is passed through untouched.
 */
import { useCallback } from "react";
import { useTranslation } from "react-i18next";

/**
 * Both timelines — `GET /account-groups/{id}/timeline` and
 * `GET /case-files/{id}/timeline` — send the same token contract, so one
 * structural type serves both rather than two near-identical renderers.
 */
export interface TranslatableTimelineEntry {
  kind: string;
  title: string;
  detail: string | null;
  detail_token: string | null;
  from_stage: string | null;
  to_stage: string | null;
}

export interface TimelineCopy {
  /** Badge text: what kind of movement this is. */
  kindLabel: string;
  /** Headline: the translated stage, or the entity's own identifier. */
  label: string;
  /** Sub-line: the translated enum, or the user's own words. */
  detail: string | null;
}

const TOKEN_PATH: Record<string, (token: string) => string> = {
  policy: (token) => `policy.status.${token}`,
  endorsement: (token) => `endorsement.kind.${token}`,
  claim: (token) => `claim.status.${token}`,
  collection: (token) => `collection.status.${token}`,
};

export function useTimelineCopy(): (entry: TranslatableTimelineEntry) => TimelineCopy {
  const { t } = useTranslation("accounts");
  const { t: tPostsale } = useTranslation("postsale");
  const { t: tCases } = useTranslation("cases");

  return useCallback(
    (entry: TranslatableTimelineEntry): TimelineCopy => {
      const stage = (token: string) =>
        tCases(`stages.${token}`, { defaultValue: token });

      const label =
        entry.kind === "stage" && entry.to_stage
          ? entry.from_stage
            ? `${stage(entry.from_stage)} → ${stage(entry.to_stage)}`
            : stage(entry.to_stage)
          : entry.kind === "note"
            ? t(`timeline.notes.${entry.title}`, { defaultValue: entry.title })
            : entry.title;

      const path = entry.detail_token
        ? TOKEN_PATH[entry.kind]?.(entry.detail_token)
        : undefined;
      const detail = path
        ? tPostsale(path, { defaultValue: entry.detail_token as string })
        : (entry.detail ?? null);

      return {
        kindLabel: t(`timeline.kinds.${entry.kind}`, { defaultValue: entry.kind }),
        label,
        detail,
      };
    },
    [t, tPostsale, tCases],
  );
}
