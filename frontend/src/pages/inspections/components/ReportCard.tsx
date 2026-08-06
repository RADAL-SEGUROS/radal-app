import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Download, FileCheck2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDocument, useDocumentDownload, useUploadDocument } from "@/api/documents";
import { useUpdateInspection } from "@/api/inspections";
import type { Inspection } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import { EmptyState, GuardedButton, SectionCard, type Guard } from "./shared";

/**
 * The signed report PDF.
 *
 * Upload is two wired calls, in order: `POST /documents` (which is the only
 * place an S3 key is minted) and then `PATCH /inspections/{id}` to point
 * `report_document_id` at the new row — the entity never stores a key itself.
 * Issuing a report requires this document, which is why the card sits next to
 * the status actions.
 */
export function ReportCard({
  inspection,
  uploadGuard,
}: {
  inspection: Inspection;
  uploadGuard: Guard;
}) {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const inputRef = React.useRef<HTMLInputElement>(null);

  const { data: doc } = useDocument(inspection.report_document_id ?? undefined);
  const [wantDownload, setWantDownload] = React.useState(false);
  const { data: download } = useDocumentDownload(
    inspection.report_document_id ?? undefined,
    wantDownload,
  );

  React.useEffect(() => {
    if (wantDownload && download?.url) {
      window.open(download.url, "_blank", "noopener,noreferrer");
      setWantDownload(false);
    }
  }, [wantDownload, download]);

  const upload = useUploadDocument();
  const update = useUpdateInspection(inspection.id);
  const pending = upload.isPending || update.isPending;

  const onFile = (file: File | undefined) => {
    if (!file) return;
    upload.mutate(
      {
        file,
        entity_type: "inspection",
        entity_id: inspection.id,
        category: "inspection_report",
      },
      {
        onSuccess: (created) => {
          update.mutate(
            { report_document_id: created.id },
            {
              onSuccess: () => toast.success(t("report.uploaded")),
              onError: () => toast.error(t("error.save")),
            },
          );
        },
        onError: () => toast.error(t("error.save")),
      },
    );
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <SectionCard
      title={t("report.title")}
      icon={<FileCheck2 />}
      description={t("report.hint")}
      actions={
        <>
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,image/*"
            className="hidden"
            onChange={(e) => onFile(e.target.files?.[0])}
          />
          <GuardedButton
            guard={uploadGuard}
            variant="secondary"
            size="sm"
            disabled={pending}
            onClick={() => inputRef.current?.click()}
          >
            <Upload />
            {pending
              ? tc("actions.loading")
              : inspection.report_document_id
                ? t("report.replace")
                : t("report.upload")}
          </GuardedButton>
        </>
      }
    >
      {!inspection.report_document_id ? (
        <EmptyState>{t("report.empty")}</EmptyState>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line px-4 py-3">
          <div className="min-w-0">
            <p className="truncate text-body font-medium text-text-primary">
              {doc?.original_name ?? `#${inspection.report_document_id}`}
            </p>
            <p className="text-caption text-text-muted">
              {doc?.created_at ? formatDateTime(doc.created_at) : "—"}
            </p>
          </div>
          <Button variant="secondary" size="sm" onClick={() => setWantDownload(true)}>
            <Download /> {t("report.download")}
          </Button>
        </div>
      )}
    </SectionCard>
  );
}
