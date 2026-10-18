/**
 * Explicit maps from the server's copilot keys to UI copy and icons.
 *
 * `GET /ai/copilot/home` speaks in stable keys (`overdue_installments`,
 * `portfolio_summary`, …). Every key the UI can render is listed HERE with its
 * literal i18n keys — never `t(\`agent.queries.${key}\`)`, which compiles and
 * then prints a raw key on screen the day the server adds a member. A key the
 * maps do not know is skipped, not guessed.
 *
 * The query prompt is the text that is actually SENT to the agent; it lives in
 * the locale bundles (Spanish in `es`, the source documents are Spanish).
 */
import {
  AlarmClock,
  BarChart3,
  Bell,
  Building2,
  CalendarClock,
  CalendarDays,
  ClipboardCheck,
  FileSearch,
  FileWarning,
  FolderClock,
  Mail,
  Receipt,
  RefreshCcw,
  ShieldCheck,
  ShieldPlus,
  Trophy,
  type LucideIcon,
} from "lucide-react";

// --- Indicators -----------------------------------------------------------------------

export interface IndicatorSpec {
  labelKey: string;
  icon: LucideIcon;
}

export const INDICATORS: Record<string, IndicatorSpec> = {
  overdue_installments: { labelKey: "agent.indicators.overdue_installments", icon: AlarmClock },
  pending_evidence: { labelKey: "agent.indicators.pending_evidence", icon: Receipt },
  todo_notifications: { labelKey: "agent.indicators.todo_notifications", icon: Bell },
  expiring_policies_30: { labelKey: "agent.indicators.expiring_policies_30", icon: CalendarClock },
  active_policies: { labelKey: "agent.indicators.active_policies", icon: ShieldCheck },
};

export function indicatorSpec(key: string): IndicatorSpec | null {
  return Object.prototype.hasOwnProperty.call(INDICATORS, key) ? INDICATORS[key] : null;
}

/** The welcome shows at most this many indicator cards. */
export const MAX_INDICATORS = 5;

// --- Common queries -----------------------------------------------------------------------

export type QueryGroup = "collections" | "policies" | "portfolio" | "documents";

export const QUERY_GROUPS: readonly { key: QueryGroup; labelKey: string }[] = [
  { key: "collections", labelKey: "agent.queryGroups.collections" },
  { key: "policies", labelKey: "agent.queryGroups.policies" },
  { key: "portfolio", labelKey: "agent.queryGroups.portfolio" },
  { key: "documents", labelKey: "agent.queryGroups.documents" },
];

export interface QuerySpec {
  key: string;
  group: QueryGroup;
  labelKey: string;
  /** The prompt actually sent to the agent. */
  promptKey: string;
  icon: LucideIcon;
  /** Default skill when the server does not name one (catalogue items). */
  skill: string | null;
}

/**
 * The static catalogue behind "Ver más", in display order. The first seven are
 * the keys the server may suggest; the rest exist only here.
 */
export const QUERY_CATALOG: readonly QuerySpec[] = [
  {
    key: "overdue_review",
    group: "collections",
    labelKey: "agent.queries.overdue_review.label",
    promptKey: "agent.queries.overdue_review.prompt",
    icon: AlarmClock,
    skill: "collections_review",
  },
  {
    key: "pending_evidence_review",
    group: "collections",
    labelKey: "agent.queries.pending_evidence_review.label",
    promptKey: "agent.queries.pending_evidence_review.prompt",
    icon: Receipt,
    skill: "collections_review",
  },
  {
    key: "ask_insurer_status",
    group: "collections",
    labelKey: "agent.queries.ask_insurer_status.label",
    promptKey: "agent.queries.ask_insurer_status.prompt",
    icon: Mail,
    skill: "collections_review",
  },
  {
    key: "register_policy",
    group: "policies",
    labelKey: "agent.queries.register_policy.label",
    promptKey: "agent.queries.register_policy.prompt",
    icon: ShieldPlus,
    skill: "policy_intake",
  },
  {
    key: "renewals_upcoming",
    group: "policies",
    labelKey: "agent.queries.renewals_upcoming.label",
    promptKey: "agent.queries.renewals_upcoming.prompt",
    icon: RefreshCcw,
    skill: "portfolio_analysis",
  },
  {
    key: "portfolio_summary",
    group: "portfolio",
    labelKey: "agent.queries.portfolio_summary.label",
    promptKey: "agent.queries.portfolio_summary.prompt",
    icon: BarChart3,
    skill: "portfolio_analysis",
  },
  {
    key: "missing_files",
    group: "documents",
    labelKey: "agent.queries.missing_files.label",
    promptKey: "agent.queries.missing_files.prompt",
    icon: FileWarning,
    skill: "file_check",
  },
  // --- catalogue-only -----------------------------------------------------------------
  {
    key: "due_this_week",
    group: "collections",
    labelKey: "agent.queries.due_this_week.label",
    promptKey: "agent.queries.due_this_week.prompt",
    icon: CalendarDays,
    skill: "collections_review",
  },
  {
    key: "policies_incomplete",
    group: "policies",
    labelKey: "agent.queries.policies_incomplete.label",
    promptKey: "agent.queries.policies_incomplete.prompt",
    icon: ClipboardCheck,
    skill: "file_check",
  },
  {
    key: "top_insured",
    group: "portfolio",
    labelKey: "agent.queries.top_insured.label",
    promptKey: "agent.queries.top_insured.prompt",
    icon: Trophy,
    skill: "portfolio_analysis",
  },
  {
    key: "insurer_mix",
    group: "portfolio",
    labelKey: "agent.queries.insurer_mix.label",
    promptKey: "agent.queries.insurer_mix.prompt",
    icon: Building2,
    skill: "portfolio_analysis",
  },
  {
    key: "stalled_accounts",
    group: "documents",
    labelKey: "agent.queries.stalled_accounts.label",
    promptKey: "agent.queries.stalled_accounts.prompt",
    icon: FolderClock,
    skill: "partial_journey",
  },
  {
    key: "policy_documents",
    group: "documents",
    labelKey: "agent.queries.policy_documents.label",
    promptKey: "agent.queries.policy_documents.prompt",
    icon: FileSearch,
    skill: "file_check",
  },
];

const QUERY_BY_KEY = new Map(QUERY_CATALOG.map((spec) => [spec.key, spec]));

export function querySpec(key: string): QuerySpec | null {
  return QUERY_BY_KEY.get(key) ?? null;
}

/** The welcome shows at most this many server suggestions before "Ver más". */
export const MAX_SUGGESTIONS = 6;

/** Shown when the server sent no (known) suggestion — or the home call failed. */
export const DEFAULT_SUGGESTIONS: readonly string[] = [
  "overdue_review",
  "renewals_upcoming",
  "portfolio_summary",
  "missing_files",
  "register_policy",
  "pending_evidence_review",
];
