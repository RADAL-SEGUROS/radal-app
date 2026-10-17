/**
 * The v12 blocks of the policy ficha (lane F1), in the team's order:
 * cabecera → "Falta: X" → bloques.
 *
 *  - Falta     — each missing datum, the functions it switches off and the
 *                server's Spanish reason (`operability`), with Completar.
 *  - Partes    — who is who (`parties`), editable via `PUT /parties`.
 *  - Cobranza  — the collection line (`collection_summary`) + link to the plan,
 *                or "Cargar plan de pago" when there is none.
 *  - Viaje     — entered vs skipped stages of the colocación (`journey`), with
 *                a link to the group account, opened as a viewer.
 *
 * Every control is wired, or visibly disabled with the reason (no dead buttons).
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  CircleSlash,
  FolderOpen,
  PencilLine,
  Upload,
  Users,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { KeyValue, Section } from "@/components/common/kit";
import { TouchHint } from "@/components/policies/TouchHint";
import { LevelBadge } from "@/components/collections/LevelBadge";
import { useCan } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { formatRut } from "@/pages/clients/rut";
import type { PartyRole, PolicyDetail } from "@/api/policies";
import {
  ACCOUNT_STAGES,
  COLLECTION_FILTER_LABEL,
  FEATURE_LABEL,
  MISSING_LABEL,
  ORIGIN_LABEL,
  PARTY_ROLE_LABEL,
  PAYMENT_METHOD_LABEL,
  STAGE_LABEL,
  labelOf,
} from "@/components/policies/policyLabels";
import { money } from "@/components/policies/PolicyCells";
import {
  CompleteFieldDialog,
  PlanLoadDialog,
  type CompleteTarget,
} from "@/components/policies/PolicyCompleteDialogs";
import { PolicyPartiesEditor } from "@/components/policies/PolicyPartiesEditor";
import { PolicyUploadDialog } from "@/components/policies/PolicyUploadDialog";

// =============================================================================
// Falta: X
// =============================================================================

/** What "Completar" does for each missing key. */
type CompleteAction =
  | { kind: "field"; target: CompleteTarget }
  | { kind: "parties" }
  | { kind: "plan" }
  | { kind: "upload" };

const COMPLETE_ACTION: Record<string, CompleteAction> = {
  policy_number: { kind: "field", target: "policy_number" },
  gross_premium: { kind: "field", target: "premiums" },
  premiums: { kind: "field", target: "premiums" },
  commission: { kind: "field", target: "commission" },
  payer_contact: { kind: "parties" },
  plan: { kind: "plan" },
  // Team 3.6: "Subir póliza" on a typed policy completes it. The upload is
  // filed into the policy's own folder, where the server matches it by folio
  // (or the folder's stand-in row) and clears "Sin respaldo".
  source_document: { kind: "upload" },
};

export function PolicyMissingBlock({
  policy,
  onEditParties,
  onLoadPlan,
  onUpload,
}: {
  policy: PolicyDetail;
  onEditParties: () => void;
  onLoadPlan: () => void;
  onUpload: () => void;
}) {
  const { t } = useTranslation("policies");
  const canEdit = useCan("Policies", "Edit");
  const canPlan = useCan("Collections", "Create");
  const canCreate = useCan("Policies", "Create");
  const canUploadDoc = useCan("Documents", "Upload");
  const uploadHint = !canCreate.allowed
    ? t("noPermission.create")
    : !canUploadDoc.allowed
      ? t("noPermission.upload")
      : null;
  const [target, setTarget] = React.useState<CompleteTarget | null>(null);

  const missing = policy.missing ?? [];
  if (missing.length === 0) return null;

  const featuresFor = (key: string) =>
    (policy.operability ?? []).filter((row) => row.missing.includes(key));

  return (
    <>
      <Section
        title={t("missingBlock.title")}
        description={t("missingBlock.subtitle")}
        className="border-warn-line"
        bodyClassName="p-0"
      >
        <ul className="divide-y divide-line">
          {missing.map((key) => {
            const features = featuresFor(key);
            // The server writes each feature's reason for its FIRST lacking key
            // (`policy_registry.policy_operability`), so only a feature led by
            // this key speaks for it — otherwise "Contacto de cobranza" would
            // print the plan's reason.
            const reason = features.find((f) => f.missing[0] === key && f.reason)?.reason ?? null;
            const action = Object.prototype.hasOwnProperty.call(COMPLETE_ACTION, key)
              ? COMPLETE_ACTION[key]
              : undefined;
            return (
              <li
                key={key}
                className="flex flex-col gap-2 px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5 text-body text-ink">
                    <AlertTriangle className="h-4 w-4 shrink-0 text-warn-text" />
                    <span className="font-medium">
                      {t("missingBlock.line", { label: labelOf(MISSING_LABEL, key, t) })}
                    </span>
                    {features.length > 0 ? (
                      <span className="text-ink-3">
                        → {features.map((f) => labelOf(FEATURE_LABEL, f.feature, t)).join(" · ")}
                      </span>
                    ) : null}
                  </div>
                  {reason ? <p className="mt-0.5 text-caption text-ink-3">{reason}</p> : null}
                </div>
                <div className="shrink-0">
                  {!action ? null : action.kind === "upload" ? (
                    <TouchHint hint={uploadHint}>
                      <Button
                        variant="secondary"
                        size="sm"
                        className="h-9"
                        disabled={!!uploadHint}
                        onClick={onUpload}
                      >
                        <Upload className="h-4 w-4" />
                        {t("actions.upload")}
                      </Button>
                    </TouchHint>
                  ) : action.kind === "plan" ? (
                    <TouchHint
                      hint={
                        !canPlan.allowed
                          ? t("noPermission.plan")
                          : !policy.total_premium_uf
                            ? t("plan.needsGross")
                            : null
                      }
                    >
                      <Button
                        variant="secondary"
                        size="sm"
                        className="h-9"
                        disabled={!canPlan.allowed || !policy.total_premium_uf}
                        onClick={onLoadPlan}
                      >
                        {t("plan.load")}
                      </Button>
                    </TouchHint>
                  ) : (
                    <TouchHint hint={canEdit.allowed ? null : t("noPermission.edit")}>
                      <Button
                        variant="secondary"
                        size="sm"
                        className="h-9"
                        disabled={!canEdit.allowed}
                        onClick={() =>
                          action.kind === "parties" ? onEditParties() : setTarget(action.target)
                        }
                      >
                        {t("complete.action")}
                      </Button>
                    </TouchHint>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </Section>
      <CompleteFieldDialog
        policy={policy}
        target={target}
        open={target !== null}
        onOpenChange={(open) => {
          if (!open) setTarget(null);
        }}
      />
    </>
  );
}

// =============================================================================
// Partes
// =============================================================================

const ROLE_ORDER: PartyRole[] = ["insured", "policyholder", "payer", "beneficiary", "additional_insured"];

export function PolicyPartiesBlock({
  policy,
  onEdit,
}: {
  policy: PolicyDetail;
  onEdit: () => void;
}) {
  const { t } = useTranslation("policies");
  const canEdit = useCan("Policies", "Edit");
  const parties = [...(policy.parties ?? [])].sort(
    (a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role),
  );
  const hasPayer = parties.some((p) => p.role === "payer");
  // Reminders go to the payer, else the contratante (`payer_contact_email`):
  // "no tiene a quién avisar" only when NEITHER has a contact. A default payer
  // row copied from the contratante kept the warning after the contratante's
  // contact was completed (QA v12).
  const collectionsReachable = parties.some(
    (p) => (p.role === "payer" || p.role === "policyholder") && (p.email || p.phone),
  );

  return (
    <Section
      title={t("party.title")}
      actions={
        <TouchHint hint={canEdit.allowed ? null : t("noPermission.edit")}>
          <Button variant="secondary" size="sm" disabled={!canEdit.allowed} onClick={onEdit}>
            <PencilLine className="h-4 w-4" />
            {t("party.edit")}
          </Button>
        </TouchHint>
      }
      bodyClassName="p-0"
    >
      {parties.length === 0 ? (
        <p className="px-5 py-4 text-caption text-ink-3">{t("party.empty")}</p>
      ) : (
        <ul className="divide-y divide-line">
          {parties.map((p) => (
            <li key={p.id} className="flex flex-col gap-0.5 px-5 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={p.role === "insured" || p.role === "policyholder" ? "brand" : "neutral"}>
                  {labelOf(PARTY_ROLE_LABEL, p.role, t)}
                </Badge>
                <span className="min-w-0 truncate font-medium text-ink">{p.name}</span>
                {p.rut ? (
                  <span className="text-caption tabular-nums text-ink-3">{formatRut(p.rut)}</span>
                ) : null}
              </div>
              <div className="flex flex-wrap gap-x-3 text-caption text-ink-3">
                {p.email ? <span className="break-all">{p.email}</span> : null}
                {p.phone ? <span>{p.phone}</span> : null}
                {p.beneficiary_kind ? <span>{p.beneficiary_kind}</span> : null}
                {!collectionsReachable && !p.email && !p.phone && (p.role === "payer" || p.role === "policyholder") ? (
                  <span className="text-warn-text">{t("party.noContact")}</span>
                ) : null}
              </div>
            </li>
          ))}
          {!hasPayer ? (
            <li className="px-5 py-2.5 text-caption text-ink-3">{t("party.payerDefault")}</li>
          ) : null}
        </ul>
      )}
    </Section>
  );
}

// =============================================================================
// Cobranza
// =============================================================================

export function PolicyCollectionBlock({
  policy,
  onLoadPlan,
}: {
  policy: PolicyDetail;
  onLoadPlan: () => void;
}) {
  const { t } = useTranslation("policies");
  const canPlan = useCan("Collections", "Create");
  const canViewCollections = useCan("Collections", "View");
  const c = policy.collection_summary;
  const hasPlan = !!c?.plan_id && c.state !== "no_plan";

  return (
    <Section
      title={t("collectionBlock.title")}
      actions={
        hasPlan ? (
          canViewCollections.allowed ? (
            <Button asChild variant="secondary" size="sm">
              <Link to={`/collections/${c!.plan_id}`}>
                {t("collectionBlock.openPlan")}
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          ) : (
            <TouchHint hint={t("noPermission.collections")}>
              <Button variant="secondary" size="sm" disabled>
                {t("collectionBlock.openPlan")}
                <ArrowRight className="h-3.5 w-3.5" />
              </Button>
            </TouchHint>
          )
        ) : (
          <TouchHint
            hint={
              !canPlan.allowed
                ? t("noPermission.plan")
                : !policy.total_premium_uf
                  ? t("plan.needsGross")
                  : null
            }
          >
            <Button
              variant="secondary"
              size="sm"
              disabled={!canPlan.allowed || !policy.total_premium_uf}
              onClick={onLoadPlan}
            >
              {t("plan.load")}
            </Button>
          </TouchHint>
        )
      }
    >
      {!hasPlan ? (
        <div className="flex items-start gap-2.5">
          <CircleSlash className="mt-0.5 h-4 w-4 shrink-0 text-ink-3" />
          <p className="text-caption text-ink-3">{t("collectionBlock.noPlan")}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant={
                c!.state === "overdue" ? "danger" : c!.state === "due_soon" ? "warn" : "success"
              }
              dot
            >
              {labelOf(COLLECTION_FILTER_LABEL, c!.state, t)}
            </Badge>
            <LevelBadge level={c!.level} reason={c!.level_reason} size="md" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <KeyValue
              label={t("collectionBlock.next")}
              value={
                c!.next_due_date ? (
                  <span className="inline-flex items-center gap-1">
                    <CalendarClock className="h-3.5 w-3.5 text-ink-3" />
                    {formatDate(c!.next_due_date)}
                  </span>
                ) : (
                  "—"
                )
              }
            />
            <KeyValue
              label={t("collectionBlock.nextAmount")}
              value={money(c!.next_due_amount_uf, policy.currency)}
            />
            <KeyValue
              label={t("collectionBlock.progress")}
              value={t("collectionBlock.progressValue", {
                paid: c!.paid_count,
                total: c!.installments_count,
              })}
            />
            <KeyValue
              label={t("collectionBlock.overdue")}
              value={
                c!.overdue_count > 0
                  ? `${c!.overdue_count} · ${money(c!.overdue_uf, policy.currency)}`
                  : "—"
              }
              tone={c!.overdue_count > 0 ? "danger" : "default"}
            />
          </div>
          {policy.payment_method ? (
            <p className="text-caption text-ink-3">
              {t("collectionBlock.method", {
                method: labelOf(PAYMENT_METHOD_LABEL, policy.payment_method, t),
              })}
            </p>
          ) : null}
        </div>
      )}
    </Section>
  );
}

// =============================================================================
// Viaje
// =============================================================================

export function PolicyJourneyBlock({ policy }: { policy: PolicyDetail }) {
  const { t } = useTranslation("policies");
  const canViewCases = useCan("CaseFiles", "View");
  const j = policy.journey;
  const entered = new Set(j?.entered_stages ?? []);
  const skipped = new Set(j?.skipped_stages ?? []);
  const shown = ACCOUNT_STAGES.filter((s) => entered.has(s) || skipped.has(s) || s === j?.stage);
  const accountLink =
    j?.case_file_id && j.group_id
      ? `/groups/${j.group_id}/accounts/${j.case_file_id}`
      : j?.case_file_id
        ? `/cases/${j.case_file_id}`
        : null;

  return (
    <Section
      title={t("journeyBlock.title")}
      description={
        policy.origin
          ? t("journeyBlock.origin", { origin: labelOf(ORIGIN_LABEL, policy.origin, t) })
          : undefined
      }
      actions={
        !accountLink ? null : canViewCases.allowed ? (
          <Button asChild variant="secondary" size="sm">
            <Link to={accountLink}>
              <FolderOpen className="h-4 w-4" />
              {t("journeyBlock.openAccount")}
            </Link>
          </Button>
        ) : (
          <TouchHint hint={t("noPermission.cases")}>
            <Button variant="secondary" size="sm" disabled>
              <FolderOpen className="h-4 w-4" />
              {t("journeyBlock.openAccount")}
            </Button>
          </TouchHint>
        )
      }
    >
      {shown.length === 0 ? (
        <p className="text-caption text-ink-3">{t("journeyBlock.empty")}</p>
      ) : (
        <ol className="flex flex-wrap items-center gap-1.5" aria-label={t("journeyBlock.title")}>
          {shown.map((stage) => {
            const isCurrent = stage === j?.stage;
            const isSkipped = skipped.has(stage) && !entered.has(stage) && !isCurrent;
            return (
              <li key={stage}>
                <span
                  className={cn(
                    "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[12px] leading-5",
                    isSkipped
                      ? "border border-dashed border-line text-ink-3"
                      : isCurrent
                        ? "bg-brand text-white"
                        : "bg-brand-soft text-brand-deep",
                  )}
                  title={isSkipped ? t("journeyBlock.skipped") : undefined}
                >
                  {t(STAGE_LABEL[stage])}
                  {isSkipped ? (
                    <span className="text-[11px] text-ink-3">· {t("journeyBlock.skipped")}</span>
                  ) : null}
                </span>
              </li>
            );
          })}
        </ol>
      )}
      <p className="mt-3 flex items-center gap-1.5 text-caption text-ink-3">
        <Users className="h-3.5 w-3.5" />
        {t("journeyBlock.viewerHint")}
      </p>
    </Section>
  );
}

// =============================================================================
// All of them, in order
// =============================================================================

export function PolicyV12Blocks({ policy }: { policy: PolicyDetail }) {
  const [partiesOpen, setPartiesOpen] = React.useState(false);
  const [planOpen, setPlanOpen] = React.useState(false);
  const [uploadOpen, setUploadOpen] = React.useState(false);

  return (
    <>
      <PolicyMissingBlock
        policy={policy}
        onEditParties={() => setPartiesOpen(true)}
        onLoadPlan={() => setPlanOpen(true)}
        onUpload={() => setUploadOpen(true)}
      />
      <div className="grid grid-cols-1 gap-[18px] lg:grid-cols-2">
        <PolicyPartiesBlock policy={policy} onEdit={() => setPartiesOpen(true)} />
        <PolicyCollectionBlock policy={policy} onLoadPlan={() => setPlanOpen(true)} />
      </div>
      <PolicyJourneyBlock policy={policy} />

      <PolicyPartiesEditor policy={policy} open={partiesOpen} onOpenChange={setPartiesOpen} />
      <PlanLoadDialog policy={policy} open={planOpen} onOpenChange={setPlanOpen} />
      <PolicyUploadDialog
        caseId={policy.case_file_id ?? undefined}
        open={uploadOpen}
        onOpenChange={setUploadOpen}
      />
    </>
  );
}
