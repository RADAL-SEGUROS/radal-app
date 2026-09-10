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
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  RefreshCw,
  ShieldCheck,
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
import { useUploadDocument } from "@/api/documents";
import { useUploadPolicy } from "@/api/policies";
import type {
  PolicyNotAPolicyDetail,
  PolicyUploadResponse,
} from "@/api/types";
import { ErrorBanner } from "@/components/common/kit";

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

type Stage =
  | { kind: "idle" }
  | { kind: "working" }
  | { kind: "rejected"; documentId: number; reject: PolicyNotAPolicyDetail; fileName: string }
  | { kind: "done"; result: PolicyUploadResponse };

export function PolicyUploadDialog({
  caseId,
  open,
  onOpenChange,
  onUploaded,
}: {
  /** The account case file — the folder the policy is filed into. */
  caseId: number;
  open: boolean;
  onOpenChange: (value: boolean) => void;
  /** Fired once a policy is committed (either clean or via override). */
  onUploaded?: (result: PolicyUploadResponse) => void;
}) {
  const { t } = useTranslation("postsale");
  const { t: tc } = useTranslation("common");

  const uploadDoc = useUploadDocument();
  const uploadPolicy = useUploadPolicy();

  const [stage, setStage] = React.useState<Stage>({ kind: "idle" });
  const [error, setError] = React.useState<unknown>(null);
  const [dragOver, setDragOver] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const reset = React.useCallback(() => {
    setStage({ kind: "idle" });
    setError(null);
  }, []);

  // Fresh dialog every open — never show the last upload's verdict.
  React.useEffect(() => {
    if (open) reset();
  }, [open, reset]);

  const commit = React.useCallback(
    (documentId: number, override: boolean, fileName: string) => {
      setError(null);
      setStage({ kind: "working" });
      uploadPolicy.mutate(
        { document_id: documentId, case_file_id: caseId, override },
        {
          onSuccess: (result) => {
            setStage({ kind: "done", result });
            onUploaded?.(result);
          },
          onError: (err) => {
            const reject = notAPolicy(err);
            if (reject) {
              setStage({ kind: "rejected", documentId, reject, fileName });
            } else {
              setStage({ kind: "idle" });
              setError(err);
            }
          },
        },
      );
    },
    [caseId, onUploaded, uploadPolicy],
  );

  const handleFile = React.useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      setError(null);
      setStage({ kind: "working" });
      try {
        const doc = await uploadDoc.mutateAsync({
          file,
          entity_type: "case_file",
          entity_id: caseId,
          category: "policy",
        });
        commit(doc.id, false, file.name);
      } catch (err) {
        setStage({ kind: "idle" });
        setError(err);
      }
    },
    [caseId, commit, uploadDoc],
  );

  const busy = stage.kind === "working";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("policy.upload.title")}</DialogTitle>
          <DialogDescription>{t("policy.upload.subtitle")}</DialogDescription>
        </DialogHeader>

        {error ? <ErrorBanner error={error} /> : null}

        {/* ── Dropzone (idle / working) ── */}
        {stage.kind === "idle" || stage.kind === "working" ? (
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
              disabled={busy}
              onClick={() => inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                if (!busy) void handleFile(e.dataTransfer.files?.[0]);
              }}
              className={cn(
                "flex w-full items-center gap-3 rounded-lg border border-dashed border-line px-4 py-6 text-left transition-colors",
                "hover:border-brand-line hover:bg-brand-soft/40",
                dragOver && "border-brand-line bg-brand-soft/60",
                busy && "cursor-not-allowed opacity-60",
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
                  {t("policy.upload.dropHint")}
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
            onOverride={() => commit(stage.documentId, true, stage.fileName)}
            onRetry={reset}
          />
        ) : null}

        {/* ── Success ── */}
        {stage.kind === "done" ? <DonePanel result={stage.result} /> : null}

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
                {t(`policy.upload.coreField.${field}`, { defaultValue: field })}
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

function DonePanel({ result }: { result: PolicyUploadResponse }) {
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

      <Button size="sm" variant="secondary" asChild>
        <Link to={`/policies/${p.id}`}>
          <ShieldCheck className="h-4 w-4" />
          {t("policy.upload.inspect")}
        </Link>
      </Button>
    </div>
  );
}
