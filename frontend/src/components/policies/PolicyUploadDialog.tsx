/**
 * Policy upload dialog — validate-then-dynamic ingestion, reusable.
 *
 * The broker drops a policy PDF. It is filed through the documents flow first
 * (`entity_type=case_file`, `category=policy`) and then `POST /policies/upload`
 * runs the context-aware POLICY read and the fixed-core gate. Three outcomes,
 * all VISIBLE — never a silent no-op:
 *
 *   - success        → the created policy, its core verdict, links to inspect it;
 *   - not_a_policy    → a REJECT card naming the missing core fields (corredor /
 *                       asegurado / vigencia / prima) plus an explicit
 *                       "Registrar de todas formas" that re-submits `override:true`
 *                       against the SAME document (the confirmed override path);
 *   - any other error → the server's message in an inline banner.
 *
 * Copy lives in the `postsale` namespace (`policy.upload.*`), `es` authoritative.
 *
 * v12 (lane F1): `caseId` is optional. Without it — "Subir póliza" from the
 * Pólizas list — the broker picks the ramo·subtipo (required: the colocación is
 * located or created for it) and, optionally, the insured. The PDF is filed on
 * the client (or the broker when no client is picked) and the server resolves
 * the insured from the RUT it reads. A RUT unknown to the portfolio comes back
 * as 422 `insured_unresolved` with the reader's suggestion: the broker either
 * confirms "Crear asegurado" (`create_client: true`) or picks the client —
 * suggest → confirm → commit, the reader never creates a client on its own.
 * Copy for these v12 steps lives in the `policies` namespace (`upload.*`).
 *
 * Fast reading (docs/v12/11): the upload runs only the CORE read (≈10–20 s),
 * shown as an honest stepped bar (Subiendo · Leyendo datos principales · Listo
 * — never a still screen). When the answer carries
 * `technical_read.status === "pending"` the dialog fires
 * `POST /policies/{id}/technical-read` at once; that mutation outlives the
 * dialog (the Pólizas list navigates away on success) and the ficha picks it
 * up as "Leyendo datos técnicos…".
 *
 * Insurer by name only: real policy PDFs often print the insurer's name with
 * neither RUT nor CMF code, and the server answers 422 `insurer_unresolved`
 * with the name it read and its best matches. The dialog asks "¿Qué compañía
 * emitió esta póliza?" (suggestions first, the first one preselected) and
 * re-posts the SAME document with every choice already made (subtipo, client /
 * create_client, override) plus `insurer_id` — the server re-uses the AI read.
 * An id the server no longer knows (`insurer_not_found`) returns to that step
 * with the reason. Every choice travels in the stage (`Choices`), so a second
 * 422 (insured after insurer, or the reverse) never drops the first answer.
 * Any other structured 422 shows the server's own `reason` / `message`.
 *
 * Review round 2 (docs/v12/12 C4):
 *  - 409 `policy_duplicate` → "Esta póliza ya existe: N° … de …" with **Ver
 *    póliza**; nothing was written and no technical read is fired;
 *  - a 201 whose plan already has past-due cuotas (`past_installments.count`)
 *    asks "¿Ya están pagadas?" before the done card — Sí, marcar pagadas / No,
 *    quedan vencidas / Decidir después (the policy page keeps asking with a
 *    banner while it is pending). `onUploaded` fires once that is answered, so
 *    a caller that navigates away does not skip the question.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Upload,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { Label } from "@/components/ui/label";
import { useUploadDocument } from "@/api/documents";
import {
  useUploadPolicy,
  type PolicyDuplicate,
  type PolicyInsuredUnresolved,
  type PolicyInsurerUnresolved,
  type PolicyUploadResponseV12,
} from "@/api/policies";
import {
  useLastTechnicalReadError,
  useStartTechnicalRead,
  useTechnicalReadInFlight,
} from "@/api/technicalRead";
import type { PolicyNotAPolicyDetail } from "@/api/types";
import { ErrorBanner } from "@/components/common/kit";
import { TouchHint } from "@/components/policies/TouchHint";
import { PastInstallmentsPrompt } from "@/components/policies/PastInstallmentsPrompt";
import { InsurerCatalogSelect, useInsurerCatalog } from "@/components/insurers/InsurerCatalogSelect";
import { ReadingProgress } from "@/components/common/ReadingProgress";
import { isLostResponse } from "@/lib/aiErrors";
import { useAuth } from "@/providers/AuthProvider";
import { RamoSubtypeSelect } from "@/components/catalog/RamoSubtypeSelect";
import { ClientPicker, type PickedClient } from "@/components/policies/ClientPicker";
import { formatRut } from "@/pages/clients/rut";
import { POLICY_CORE_FIELD_LABEL, enumLabel } from "@/i18n/labels";

/** Pull the structured `not_a_policy` body out of an axios 422, if that is what it is. */
function notAPolicy(error: unknown): PolicyNotAPolicyDetail | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data
    ?.detail;
  if (
    detail &&
    typeof detail === "object" &&
    (detail as { code?: string }).code === "not_a_policy"
  ) {
    return detail as PolicyNotAPolicyDetail;
  }
  return null;
}

/** The structured 409 `policy_duplicate` body (D2), if that is what it is. */
function policyDuplicate(error: unknown): PolicyDuplicate | null {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    ?.response;
  const detail = response?.data?.detail;
  if (
    detail &&
    typeof detail === "object" &&
    (detail as { code?: string }).code === "policy_duplicate" &&
    typeof (detail as { policy_id?: unknown }).policy_id === "number"
  ) {
    return detail as PolicyDuplicate;
  }
  return null;
}

/** The structured `insured_unresolved` body (v12 no-folder upload), if that is what it is. */
function insuredUnresolved(error: unknown): PolicyInsuredUnresolved | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data
    ?.detail;
  if (
    detail &&
    typeof detail === "object" &&
    (detail as { code?: string }).code === "insured_unresolved"
  ) {
    return detail as PolicyInsuredUnresolved;
  }
  return null;
}

/** The structured body of an axios error, when the server sent one. */
function errorDetail(error: unknown): { code?: string; reason?: string; message?: string } | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data
    ?.detail;
  return detail && typeof detail === "object" && !Array.isArray(detail)
    ? (detail as { code?: string; reason?: string; message?: string })
    : null;
}

/** The structured `insurer_unresolved` body (the PDF names the insurer only), if that is what it is. */
function insurerUnresolved(error: unknown): PolicyInsurerUnresolved | null {
  const detail = errorDetail(error);
  return detail?.code === "insurer_unresolved" ? (detail as PolicyInsurerUnresolved) : null;
}

/** The server's own Spanish explanation (`reason`, else `message`), if it sent one. */
function serverReason(error: unknown): string | null {
  const detail = errorDetail(error);
  return detail?.reason || detail?.message || null;
}

/**
 * Every answer the broker has already given for this document. Carried in the
 * stage so a re-post (override, insured, insurer) never drops an earlier one.
 */
interface Choices {
  clientId?: number | null;
  createClient?: boolean;
  insurerId?: number | null;
}

/** 0 = filing the PDF, 1 = the core read (`POST /policies/upload`). */
type WorkingStep = 0 | 1;

type Stage =
  | { kind: "idle" }
  | { kind: "working"; step: WorkingStep }
  | {
      kind: "rejected";
      documentId: number;
      reject: PolicyNotAPolicyDetail;
      fileName: string;
      choices: Choices;
    }
  | {
      kind: "unresolved";
      documentId: number;
      unresolved: PolicyInsuredUnresolved;
      fileName: string;
      override: boolean;
      choices: Choices;
    }
  | {
      kind: "insurer";
      documentId: number;
      unresolved: PolicyInsurerUnresolved;
      fileName: string;
      override: boolean;
      choices: Choices;
      /** Why the broker is asked again (e.g. `insurer_not_found`). */
      notice?: string;
    }
  | { kind: "past"; result: PolicyUploadResponseV12; technical: boolean }
  | { kind: "duplicate"; duplicate: PolicyDuplicate; fileName: string }
  | { kind: "done"; result: PolicyUploadResponseV12; technical: boolean };

export function PolicyUploadDialog({
  caseId,
  open,
  onOpenChange,
  onUploaded,
}: {
  /**
   * The account case file — the folder the policy is filed into. Omit it for
   * the v12 no-folder upload (Pólizas list): the dialog then asks for the
   * ramo·subtipo and, optionally, the insured.
   */
  caseId?: number;
  open: boolean;
  onOpenChange: (value: boolean) => void;
  /** Fired once a policy is committed (either clean or via override). */
  onUploaded?: (result: PolicyUploadResponseV12) => void;
}) {
  const { t } = useTranslation("postsale");
  const { t: tp } = useTranslation("policies");
  const { t: tc } = useTranslation("common");
  const { user } = useAuth();

  const uploadDoc = useUploadDocument();
  const uploadPolicy = useUploadPolicy();
  const startTechnical = useStartTechnicalRead();

  const onDemand = caseId === undefined;
  const [subtypeId, setSubtypeId] = React.useState<number | null>(null);
  const [client, setClient] = React.useState<PickedClient | null>(null);

  const [stage, setStage] = React.useState<Stage>({ kind: "idle" });
  const [error, setError] = React.useState<unknown>(null);
  const [dragOver, setDragOver] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);
  /** The last `insurer_unresolved` body — re-shown when the pick is `insurer_not_found`. */
  const insurerAsk = React.useRef<PolicyInsurerUnresolved | null>(null);

  const reset = React.useCallback(() => {
    setStage({ kind: "idle" });
    setError(null);
    insurerAsk.current = null;
  }, []);

  // Fresh dialog every open — never show the last upload's verdict.
  React.useEffect(() => {
    if (open) {
      reset();
      setSubtypeId(null);
      setClient(null);
    }
  }, [open, reset]);

  const commit = React.useCallback(
    (
      documentId: number,
      override: boolean,
      fileName: string,
      choices: Choices = {},
    ) => {
      setError(null);
      setStage({ kind: "working", step: 1 });
      const insurerId = choices.insurerId ?? null;
      uploadPolicy.mutate(
        onDemand
          ? {
              document_id: documentId,
              override,
              insurance_subtype_id: subtypeId,
              client_id: choices.clientId ?? client?.id ?? null,
              create_client: choices.createClient ?? false,
              ...(insurerId !== null ? { insurer_id: insurerId } : {}),
            }
          : {
              document_id: documentId,
              case_file_id: caseId,
              override,
              ...(insurerId !== null ? { insurer_id: insurerId } : {}),
            },
        {
          onSuccess: (result) => {
            // Fire the technical read BEFORE `onUploaded` (which may navigate
            // away): the mutation's cache work is mutation-level, so it lands
            // on the ficha even once this dialog is gone.
            const technical = result.technical_read?.status === "pending";
            if (technical) startTechnical.mutate({ policyId: result.policy.id });
            // D1: cuotas already past due → ask first; `onUploaded` (which may
            // navigate away) waits for the answer.
            if ((result.past_installments?.count ?? 0) > 0) {
              setStage({ kind: "past", result, technical });
              return;
            }
            setStage({ kind: "done", result, technical });
            onUploaded?.(result);
          },
          onError: (err) => {
            const duplicate = policyDuplicate(err);
            if (duplicate) {
              // D2: the policy already exists — nothing was written.
              setStage({ kind: "duplicate", duplicate, fileName });
              return;
            }
            const reject = notAPolicy(err);
            const unresolved = insuredUnresolved(err);
            const insurerAsked = insurerUnresolved(err);
            if (reject) {
              setStage({ kind: "rejected", documentId, reject, fileName, choices });
            } else if (unresolved) {
              setStage({ kind: "unresolved", documentId, unresolved, fileName, override, choices });
            } else if (insurerAsked) {
              insurerAsk.current = insurerAsked;
              setStage({
                kind: "insurer",
                documentId,
                unresolved: insurerAsked,
                fileName,
                override,
                choices,
              });
            } else if (errorDetail(err)?.code === "insurer_not_found" && insurerAsk.current) {
              // The picked id is gone: back to the same question, saying why.
              setStage({
                kind: "insurer",
                documentId,
                unresolved: insurerAsk.current,
                fileName,
                override,
                choices: { ...choices, insurerId: null },
                notice: serverReason(err) ?? tp("upload.insurer.notFound"),
              });
            } else {
              setStage({ kind: "idle" });
              // A lost answer (edge 504 / dropped connection) is not a failed
              // read: the policy may have been committed. Say so honestly.
              // Otherwise the server's own Spanish reason beats the generic
              // "Error al cargar" (which stays only as ErrorBanner's fallback).
              setError(
                isLostResponse(err)
                  ? tp("upload.lost")
                  : errorDetail(err)?.code === "insurer_not_found"
                    ? (serverReason(err) ?? tp("upload.insurer.notFound"))
                    : (serverReason(err) ?? err),
              );
            }
          },
        },
      );
    },
    [caseId, client?.id, onDemand, onUploaded, startTechnical, subtypeId, tp, uploadPolicy],
  );

  const handleFile = React.useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      setError(null);
      setStage({ kind: "working", step: 0 });
      // Folder upload: file it in the folder. No folder: on the picked client,
      // else on the broker itself (the server moves it into the colocación).
      const target: { entity_type: "case_file" | "client" | "broker"; entity_id: number } | null =
        !onDemand
          ? { entity_type: "case_file", entity_id: caseId as number }
          : client
            ? { entity_type: "client", entity_id: client.id }
            : user?.broker_id
              ? { entity_type: "broker", entity_id: user.broker_id }
              : null;
      if (!target) {
        setStage({ kind: "idle" });
        setError(tp("upload.noTarget"));
        return;
      }
      try {
        const doc = await uploadDoc.mutateAsync({
          file,
          ...target,
          category: "policy",
        });
        commit(doc.id, false, file.name);
      } catch (err) {
        setStage({ kind: "idle" });
        setError(serverReason(err) ?? err);
      }
    },
    [caseId, client, commit, onDemand, tp, uploadDoc, user?.broker_id],
  );

  /** The past-cuotas question was answered (or left for later): show done. */
  const finishPast = (result: PolicyUploadResponseV12, technical: boolean) => {
    setStage({ kind: "done", result, technical });
    onUploaded?.(result);
  };

  const busy = stage.kind === "working";
  /** The no-folder path needs the subtipo before a file can be read. */
  const needsSubtype = onDemand && subtypeId === null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("policy.upload.title")}</DialogTitle>
          <DialogDescription>{t("policy.upload.subtitle")}</DialogDescription>
        </DialogHeader>

        {error ? <ErrorBanner error={error} /> : null}

        {/* ── v12 no-folder: ramo·subtipo (required) + insured (optional) ── */}
        {onDemand && (stage.kind === "idle" || stage.kind === "working") ? (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="upload-subtype">{tp("upload.subtype")}</Label>
              <RamoSubtypeSelect
                id="upload-subtype"
                value={subtypeId}
                onChange={setSubtypeId}
                disabled={busy}
                showSuggestedRoute
              />
              <span className="text-caption text-ink-3">{tp("upload.subtypeHint")}</span>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{tp("upload.client")}</Label>
              <ClientPicker value={client} onChange={setClient} disabled={busy} />
              <span className="text-caption text-ink-3">{tp("upload.clientHint")}</span>
            </div>
          </div>
        ) : null}

        {/* ── Working: the honest stepped bar, never a still screen ── */}
        {stage.kind === "working" ? (
          <ReadingProgress
            title={tp("upload.progress.title")}
            hint={tp("upload.progress.hint")}
            active={stage.step}
            steps={[
              { label: tp("upload.progress.uploading"), expectedSeconds: 3 },
              { label: tp("upload.progress.readingCore"), expectedSeconds: 15 },
              { label: tp("upload.progress.ready") },
            ]}
          />
        ) : null}

        {/* ── Dropzone (idle) ── */}
        {stage.kind === "idle" ? (
          <>
            <input
              ref={inputRef}
              type="file"
              accept="application/pdf,.pdf"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                void handleFile(file);
              }}
            />
            <button
              type="button"
              disabled={busy || needsSubtype}
              title={needsSubtype ? tp("upload.subtypeFirst") : undefined}
              onClick={() => inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                if (!busy && !needsSubtype) void handleFile(e.dataTransfer.files?.[0]);
              }}
              className={cn(
                "flex w-full items-center gap-3 rounded-lg border border-dashed border-line px-4 py-6 text-left transition-colors",
                "hover:border-brand-line hover:bg-brand-soft/40",
                dragOver && "border-brand-line bg-brand-soft/60",
                (busy || needsSubtype) && "cursor-not-allowed opacity-60",
              )}
            >
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep">
                {busy ? (
                  <RefreshCw className="h-5 w-5 animate-spin" />
                ) : (
                  <Upload className="h-5 w-5" />
                )}
              </span>
              <span className="min-w-0">
                <span className="block text-body font-medium text-ink">
                  {busy ? t("policy.upload.working") : t("policy.upload.dropTitle")}
                </span>
                <span className="block text-caption text-ink-3">
                  {needsSubtype ? tp("upload.subtypeFirst") : t("policy.upload.dropHint")}
                </span>
              </span>
            </button>
          </>
        ) : null}

        {/* ── Reject: not a policy ── */}
        {stage.kind === "rejected" ? (
          <RejectPanel
            reject={stage.reject}
            fileName={stage.fileName}
            overriding={busy}
            onOverride={() => commit(stage.documentId, true, stage.fileName, stage.choices)}
            onRetry={reset}
          />
        ) : null}

        {/* ── v12: the insured RUT read is not in the portfolio ── */}
        {stage.kind === "unresolved" ? (
          <UnresolvedPanel
            unresolved={stage.unresolved}
            fileName={stage.fileName}
            onCreate={() =>
              commit(stage.documentId, stage.override, stage.fileName, {
                ...stage.choices,
                clientId: null,
                createClient: true,
              })
            }
            onPick={(picked) => {
              setClient(picked);
              commit(stage.documentId, stage.override, stage.fileName, {
                ...stage.choices,
                clientId: picked.id,
                createClient: false,
              });
            }}
            onRetry={reset}
          />
        ) : null}

        {/* ── The PDF names the insurer without RUT / CMF code ── */}
        {stage.kind === "insurer" ? (
          <InsurerPanel
            // Remount on each ask so the preselection follows the new answer.
            key={`${stage.documentId}-${stage.notice ?? ""}`}
            unresolved={stage.unresolved}
            fileName={stage.fileName}
            notice={stage.notice}
            onUse={(insurerId) =>
              commit(stage.documentId, stage.override, stage.fileName, {
                ...stage.choices,
                insurerId,
              })
            }
            onRetry={reset}
          />
        ) : null}

        {/* ── D2: this policy already exists ── */}
        {stage.kind === "duplicate" ? (
          <DuplicatePanel
            duplicate={stage.duplicate}
            fileName={stage.fileName}
            onOpen={() => onOpenChange(false)}
            onRetry={reset}
          />
        ) : null}

        {/* ── D1: cuotas already past due — ask before alerting ── */}
        {stage.kind === "past" && stage.result.past_installments ? (
          <PastInstallmentsPrompt
            variant="step"
            policyId={stage.result.policy.id}
            planId={stage.result.past_installments.plan_id ?? null}
            count={stage.result.past_installments.count}
            totalUf={stage.result.past_installments.total_uf}
            firstDue={stage.result.past_installments.first_due}
            lastDue={stage.result.past_installments.last_due}
            onDecided={() => finishPast(stage.result, stage.technical)}
            onLater={() => finishPast(stage.result, stage.technical)}
          />
        ) : null}

        {/* ── Success ── */}
        {stage.kind === "done" ? (
          <DonePanel result={stage.result} technical={stage.technical} />
        ) : null}

        <DialogFooter>
          {stage.kind === "done" ? (
            <Button onClick={() => onOpenChange(false)}>{tc("actions.close")}</Button>
          ) : (
            <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={busy}>
              {tc("actions.cancel")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RejectPanel({
  reject,
  fileName,
  overriding,
  onOverride,
  onRetry,
}: {
  reject: PolicyNotAPolicyDetail;
  fileName: string;
  overriding: boolean;
  onOverride: () => void;
  onRetry: () => void;
}) {
  const { t } = useTranslation("postsale");
  const missing = reject.missing ?? [];

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-warn-line bg-warn-soft/40 p-4">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-warn-soft text-warn-text">
          <AlertTriangle className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div className="text-body font-medium text-ink">{t("policy.upload.rejectTitle")}</div>
          <p className="mt-0.5 flex items-center gap-1.5 text-caption text-ink-3">
            <FileText className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{fileName}</span>
          </p>
        </div>
      </div>

      {missing.length > 0 ? (
        <div>
          <div className="text-caption font-medium text-ink-2">
            {t("policy.upload.missingLabel")}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {missing.map((field) => (
              <Badge key={field} variant="warn">
                {enumLabel(t, POLICY_CORE_FIELD_LABEL, field)}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}

      <p className="text-caption text-ink-2">
        {reject.reason ?? t("policy.upload.rejectReasonFallback")}
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={onRetry} disabled={overriding}>
          {t("policy.upload.tryAnother")}
        </Button>
        {/* The confirmed override — the broker's explicit "sí, regístrala igual". */}
        <Button size="sm" onClick={onOverride} disabled={overriding}>
          {overriding ? (
            <RefreshCw className="h-4 w-4 animate-spin" />
          ) : (
            <ShieldCheck className="h-4 w-4" />
          )}
          {t("policy.upload.overrideAction")}
        </Button>
      </div>
    </div>
  );
}

function DonePanel({
  result,
  technical,
}: {
  result: PolicyUploadResponseV12;
  /** The technical read was fired for this policy. */
  technical: boolean;
}) {
  const { t } = useTranslation("postsale");
  const p = result.policy;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-pos-line bg-pos-soft/40 p-4">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-pos-soft text-pos-text">
          <CheckCircle2 className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div className="text-body font-medium text-ink">
            {t("policy.upload.doneTitle", { number: p.policy_number })}
          </div>
          <p className="mt-0.5 text-caption text-ink-3">{p.insurer_name ?? "—"}</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {result.is_core_valid ? (
          <Badge variant="success" dot>
            {t("policy.upload.validated")}
          </Badge>
        ) : (
          <Badge variant="warn" dot>
            {result.overridden
              ? t("policy.upload.registeredOverride")
              : t("policy.upload.needsReview")}
          </Badge>
        )}
        {result.warnings.map((w, i) => (
          <Badge key={i} variant="muted">
            {w}
          </Badge>
        ))}
      </div>

      {technical ? <TechnicalReadLine policyId={p.id} /> : null}

      <Button size="sm" variant="secondary" asChild>
        <Link to={`/policies/${p.id}`}>
          <ShieldCheck className="h-4 w-4" />
          {t("policy.upload.inspect")}
        </Link>
      </Button>
    </div>
  );
}

/** D2: the PDF is a policy the broker already has — say which, link to it. */
function DuplicatePanel({
  duplicate,
  fileName,
  onOpen,
  onRetry,
}: {
  duplicate: PolicyDuplicate;
  fileName: string;
  onOpen: () => void;
  onRetry: () => void;
}) {
  const { t } = useTranslation("policies");
  const message =
    duplicate.reason ||
    (duplicate.insurer_name
      ? t("upload.duplicate.message", {
          number: duplicate.policy_number ?? "—",
          insurer: duplicate.insurer_name,
        })
      : t("upload.duplicate.messageNoInsurer", { number: duplicate.policy_number ?? "—" }));

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-warn-line bg-warn-soft/40 p-4">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-warn-soft text-warn-text">
          <AlertTriangle className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div role="alert" className="text-body font-medium text-ink [text-wrap:pretty]">
            {message}
          </div>
          <p className="mt-0.5 flex items-center gap-1.5 text-caption text-ink-3">
            <FileText className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{fileName}</span>
          </p>
        </div>
      </div>
      <p className="text-caption text-ink-2">{t("upload.duplicate.hint")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" asChild>
          <Link to={`/policies/${duplicate.policy_id}`} onClick={onOpen}>
            <ShieldCheck className="h-4 w-4" />
            {t("upload.duplicate.open")}
          </Link>
        </Button>
        <Button size="sm" variant="ghost" onClick={onRetry}>
          {t("upload.tryAnother")}
        </Button>
      </div>
    </div>
  );
}

function UnresolvedPanel({
  unresolved,
  fileName,
  onCreate,
  onPick,
  onRetry,
}: {
  unresolved: PolicyInsuredUnresolved;
  fileName: string;
  onCreate: () => void;
  onPick: (client: PickedClient) => void;
  onRetry: () => void;
}) {
  const { t } = useTranslation("policies");
  const [picked, setPicked] = React.useState<PickedClient | null>(null);
  const suggestion = unresolved.suggestion ?? null;
  const canCreate = !!suggestion?.rut && !!suggestion?.legal_name;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-warn-line bg-warn-soft/40 p-4">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-warn-soft text-warn-text">
          <AlertTriangle className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div className="text-body font-medium text-ink">{t("upload.unresolvedTitle")}</div>
          <p className="mt-0.5 flex items-center gap-1.5 text-caption text-ink-3">
            <FileText className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{fileName}</span>
          </p>
        </div>
      </div>

      {unresolved.reason ? <p className="text-caption text-ink-2">{unresolved.reason}</p> : null}

      {canCreate ? (
        <div className="flex flex-col gap-2 rounded-lg border border-line bg-bone p-3">
          <div className="text-caption text-ink-3">{t("upload.suggestion")}</div>
          <div className="text-body font-medium text-ink">
            {suggestion?.legal_name}{" "}
            <span className="tabular-nums text-ink-3">{formatRut(suggestion?.rut)}</span>
          </div>
          <Button size="sm" className="self-start" onClick={onCreate}>
            <ShieldCheck className="h-4 w-4" />
            {t("upload.createClient")}
          </Button>
        </div>
      ) : null}

      <div className="flex flex-col gap-1.5">
        <Label>{t("upload.pickClient")}</Label>
        <ClientPicker value={picked} onChange={setPicked} />
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={!picked}
            onClick={() => picked && onPick(picked)}
          >
            {t("upload.useClient")}
          </Button>
          <Button size="sm" variant="ghost" onClick={onRetry}>
            {t("upload.tryAnother")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function InsurerPanel({
  unresolved,
  fileName,
  notice,
  onUse,
  onRetry,
}: {
  unresolved: PolicyInsurerUnresolved;
  fileName: string;
  notice?: string;
  onUse: (insurerId: number) => void;
  onRetry: () => void;
}) {
  const { t } = useTranslation("policies");
  // Same gate as the manual form (`pages/policies/new.tsx`): the list is
  // `GET /insurers`, so it needs Insurers:View. The server's suggestions come
  // inside the 422 itself and stay pickable without it.
  const suggestions = React.useMemo(() => unresolved.suggestions ?? [], [unresolved.suggestions]);
  const catalog = useInsurerCatalog(suggestions);
  const [selected, setSelected] = React.useState<string>(
    suggestions[0] ? String(suggestions[0].id) : "",
  );
  const useBlocked = catalog.blocked ?? (selected ? null : t("upload.insurer.pickFirst"));

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-warn-line bg-warn-soft/40 p-4">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-warn-soft text-warn-text">
          <AlertTriangle className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div className="text-body font-medium text-ink">{t("upload.insurer.title")}</div>
          <p className="mt-0.5 flex items-center gap-1.5 text-caption text-ink-3">
            <FileText className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{fileName}</span>
          </p>
        </div>
      </div>

      {notice ? (
        <p role="alert" className="text-caption font-medium text-neg">
          {notice}
        </p>
      ) : null}

      <div className="flex flex-col gap-1 rounded-lg border border-line bg-bone p-3">
        <div className="text-caption text-ink-3">{t("upload.suggestion")}</div>
        <div className="text-body font-medium text-ink">
          {unresolved.read_name || t("upload.insurer.noName")}
        </div>
      </div>

      <p className="text-caption text-ink-2">
        {unresolved.reason || t("upload.insurer.reasonFallback")}
      </p>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="upload-insurer">{t("upload.insurer.label")}</Label>
        <InsurerCatalogSelect
          id="upload-insurer"
          catalog={catalog}
          value={selected}
          onChange={setSelected}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <TouchHint hint={useBlocked}>
          <Button size="sm" disabled={!!useBlocked} onClick={() => selected && onUse(Number(selected))}>
            <ShieldCheck className="h-4 w-4" />
            {t("upload.insurer.use")}
          </Button>
        </TouchHint>
        <Button size="sm" variant="ghost" onClick={onRetry}>
          {t("upload.tryAnother")}
        </Button>
      </div>
    </div>
  );
}

/** The second (technical) read, as one honest line under the done card. */
function TechnicalReadLine({ policyId }: { policyId: number }) {
  const { t } = useTranslation("policies");
  const inFlight = useTechnicalReadInFlight(policyId);
  const failed = useLastTechnicalReadError(policyId);

  if (inFlight) {
    return (
      <p role="status" className="flex items-center gap-2 text-caption text-ink-2">
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin motion-reduce:animate-none text-brand" />
        {t("upload.technical.reading")}
      </p>
    );
  }
  if (failed) {
    return (
      <p className="flex items-center gap-2 text-caption text-ink-2">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warn-text" />
        {t("upload.technical.failed")}
      </p>
    );
  }
  return (
    <p className="flex items-center gap-2 text-caption text-ink-2">
      <Sparkles className="h-3.5 w-3.5 shrink-0 text-brand" />
      {t("upload.technical.ready")}
    </p>
  );
}
