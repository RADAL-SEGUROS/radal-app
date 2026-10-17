/**
 * Explicit i18n key maps for the agent UI (namespace `dashboard`, under `agent.*`).
 *
 * House rule: NO dynamic keys (`t(\`agent.tools.${tool}\`)` compiles and then
 * renders a raw key the day the backend adds a tool). Every label goes through
 * one of these maps, with an explicit fallback for anything unknown — a new
 * server tool renders as the generic label plus its id, never as a raw key.
 *
 * Tool names mirror `backend/app/services/agent_tools.py::TOOL_REGISTRY`.
 */
import type { PermissionAction, PermissionModule } from "@/lib/permissions";
import type { AgentActionStatus } from "@/api/ai";

/** Every registry tool -> its Spanish label key. */
export const TOOL_LABEL_KEY: Record<string, string> = {
  search_entities: "agent.tools.search_entities",
  get_group_tree: "agent.tools.get_group_tree",
  get_case_file: "agent.tools.get_case_file",
  get_case_history: "agent.tools.get_case_history",
  get_quote_comparison: "agent.tools.get_quote_comparison",
  list_documents: "agent.tools.list_documents",
  create_group: "agent.tools.create_group",
  attach_client_to_group: "agent.tools.attach_client_to_group",
  create_case_file: "agent.tools.create_case_file",
  renew_case: "agent.tools.renew_case",
  add_note: "agent.tools.add_note",
  list_policies: "agent.tools.list_policies",
  get_policy: "agent.tools.get_policy",
  list_installments: "agent.tools.list_installments",
  get_collection_overview: "agent.tools.get_collection_overview",
  get_collection_mode: "agent.tools.get_collection_mode",
  list_status_inquiries: "agent.tools.list_status_inquiries",
  list_pending_evidence: "agent.tools.list_pending_evidence",
  check_case_files: "agent.tools.check_case_files",
  check_policy_files: "agent.tools.check_policy_files",
  get_catalog: "agent.tools.get_catalog",
  list_portal_searches: "agent.tools.list_portal_searches",
  get_portal_search: "agent.tools.get_portal_search",
  list_notifications: "agent.tools.list_notifications",
  load_skill: "agent.tools.load_skill",
  create_policy_manual: "agent.tools.create_policy_manual",
  update_policy_fields: "agent.tools.update_policy_fields",
  register_payment: "agent.tools.register_payment",
  record_payment_promise: "agent.tools.record_payment_promise",
  start_collection: "agent.tools.start_collection",
  send_status_inquiry: "agent.tools.send_status_inquiry",
  send_payment_reminder: "agent.tools.send_payment_reminder",
  create_share_link: "agent.tools.create_share_link",
  create_payment_link: "agent.tools.create_payment_link",
  skip_case_to_stage: "agent.tools.skip_case_to_stage",
  activate_case_stage: "agent.tools.activate_case_stage",
  run_portal_search: "agent.tools.run_portal_search",
};

export const GENERIC_TOOL_LABEL_KEY = "agent.tools.generic";

/** Own-key lookup (never a prototype member such as `constructor`). */
export function lookup(map: Record<string, string>, key: string | null | undefined): string | null {
  return key && Object.prototype.hasOwnProperty.call(map, key) ? map[key] : null;
}

/** Label key for a tool; unknown tools fall back to the generic label. */
export function toolLabelKey(tool: string | null | undefined): string {
  return lookup(TOOL_LABEL_KEY, tool) ?? GENERIC_TOOL_LABEL_KEY;
}

/** True when the tool has its own label (otherwise show its id beside the generic one). */
export function isKnownTool(tool: string | null | undefined): boolean {
  return lookup(TOOL_LABEL_KEY, tool) !== null;
}

/** Argument names the write tools take -> label key. Unknown args show humanised. */
export const ARG_LABEL_KEY: Record<string, string> = {
  query: "agent.args.query",
  kinds: "agent.args.kinds",
  group_id: "agent.args.group_id",
  client_id: "agent.args.client_id",
  client_ids: "agent.args.client_ids",
  case_file_id: "agent.args.case_file_id",
  quote_request_id: "agent.args.quote_request_id",
  include_rejected: "agent.args.include_rejected",
  entity_type: "agent.args.entity_type",
  entity_id: "agent.args.entity_id",
  category: "agent.args.category",
  section: "agent.args.section",
  name: "agent.args.name",
  notes: "agent.args.notes",
  kind: "agent.args.kind",
  title: "agent.args.title",
  insurance_line_id: "agent.args.insurance_line_id",
  period_start: "agent.args.period_start",
  period_end: "agent.args.period_end",
  period_label: "agent.args.period_label",
  note: "agent.args.note",
  body: "agent.args.body",
  is_internal: "agent.args.is_internal",
  follow_up_on: "agent.args.follow_up_on",
  policy_id: "agent.args.policy_id",
  insured_rut: "agent.args.insured_rut",
  insured_name: "agent.args.insured_name",
  insurer_id: "agent.args.insurer_id",
  insurance_subtype_id: "agent.args.insurance_subtype_id",
  start_date: "agent.args.start_date",
  end_date: "agent.args.end_date",
  issued_at: "agent.args.issued_at",
  policy_number: "agent.args.policy_number",
  insured_amount_uf: "agent.args.insured_amount_uf",
  taxable_premium_uf: "agent.args.taxable_premium_uf",
  exempt_premium_uf: "agent.args.exempt_premium_uf",
  net_premium_uf: "agent.args.net_premium_uf",
  vat_uf: "agent.args.vat_uf",
  total_premium_uf: "agent.args.total_premium_uf",
  commission_pct: "agent.args.commission_pct",
  payment_method: "agent.args.payment_method",
  currency: "agent.args.currency",
  is_provisional: "agent.args.is_provisional",
  policyholder: "agent.args.policyholder",
  payer: "agent.args.payer",
  installment_id: "agent.args.installment_id",
  installment_ids: "agent.args.installment_ids",
  amount: "agent.args.amount",
  paid_on: "agent.args.paid_on",
  method: "agent.args.method",
  confirmed_by_insurer: "agent.args.confirmed_by_insurer",
  receipt_document_id: "agent.args.receipt_document_id",
  promise_until: "agent.args.promise_until",
  plan_id: "agent.args.plan_id",
  level_preference: "agent.args.level_preference",
  to_address: "agent.args.to_address",
  message: "agent.args.message",
  audience: "agent.args.audience",
  expires_in_days: "agent.args.expires_in_days",
  label: "agent.args.label",
  target_stage: "agent.args.target_stage",
  stage: "agent.args.stage",
  portal: "agent.args.portal",
  input: "agent.args.input",
  key: "agent.args.key",
};

export function humanize(key: string): string {
  return key.replace(/_/g, " ");
}

export const MODULE_LABEL_KEY: Record<PermissionModule, string> = {
  Dashboard: "agent.modules.Dashboard",
  Clients: "agent.modules.Clients",
  Assets: "agent.modules.Assets",
  Placements: "agent.modules.Placements",
  Quotes: "agent.modules.Quotes",
  Proposals: "agent.modules.Proposals",
  Inspections: "agent.modules.Inspections",
  Insurers: "agent.modules.Insurers",
  Offerings: "agent.modules.Offerings",
  Documents: "agent.modules.Documents",
  CaseFiles: "agent.modules.CaseFiles",
  Leads: "agent.modules.Leads",
  Groups: "agent.modules.Groups",
  Policies: "agent.modules.Policies",
  Endorsements: "agent.modules.Endorsements",
  Collections: "agent.modules.Collections",
  Claims: "agent.modules.Claims",
  Reports: "agent.modules.Reports",
  Users: "agent.modules.Users",
  Settings: "agent.modules.Settings",
  Mail: "agent.modules.Mail",
  Integrations: "agent.modules.Integrations",
  Platform: "agent.modules.Platform",
};

export const RBAC_ACTION_LABEL_KEY: Record<PermissionAction, string> = {
  View: "agent.rbacActions.View",
  Create: "agent.rbacActions.Create",
  Edit: "agent.rbacActions.Edit",
  Delete: "agent.rbacActions.Delete",
  Comment: "agent.rbacActions.Comment",
  Upload: "agent.rbacActions.Upload",
  Submit: "agent.rbacActions.Submit",
  Approve: "agent.rbacActions.Approve",
  Manage: "agent.rbacActions.Manage",
};

export const ACTION_STATUS_LABEL_KEY: Record<AgentActionStatus, string> = {
  proposed: "agent.action.proposed",
  confirmed: "agent.action.confirmed",
  discarded: "agent.action.discarded",
  failed: "agent.action.failed",
};

export const REF_TYPE_LABEL_KEY: Record<string, string> = {
  account_group: "agent.refTypes.account_group",
  client: "agent.refTypes.client",
  case_file: "agent.refTypes.case_file",
  policy: "agent.refTypes.policy",
  quote_request: "agent.refTypes.quote_request",
  proposal: "agent.refTypes.proposal",
  document: "agent.refTypes.document",
  sales_lead: "agent.refTypes.sales_lead",
};

export function refTypeLabelKey(entityType: string): string {
  return lookup(REF_TYPE_LABEL_KEY, entityType) ?? "agent.refTypes.other";
}

export const MENTION_SECTION_LABEL_KEY = {
  groups: "agent.mention.groups",
  recent: "agent.mention.recent",
  clients: "agent.mention.clients",
  quotes: "agent.mention.quotes",
  proposals: "agent.mention.proposals",
} as const;
