import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { motion } from "framer-motion";
import { Download, FileText, ImageIcon, Trash2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { FadeUp, Stagger } from "@/components/common/motion";
import {
  useDeleteDocument,
  useDocumentDownload,
  useDocuments,
  useUploadDocument,
} from "@/api/documents";
import type { RadalDocument } from "@/api/types";
import { formatDate } from "@/lib/format";
import { EmptyState, GuardedButton, SectionCard, type Guard } from "./shared";

/**
 * Evidence attached to the report: `GET /documents?entity_type=inspection`.
 * Images render as a gallery (each thumbnail asks for its own short-lived
 * download URL), everything else is listed as an attachment.
 */

function isImage(doc: RadalDocument): boolean {
  return (doc.mime_type ?? "").startsWith("image/");
}

function EvidenceThumb({
  doc,
  onOpen,
  onDelete,
  deleteGuard,
}: {
  doc: RadalDocument;
  onOpen: (url: string) => void;
  onDelete: () => void;
  deleteGuard: Guard;
}) {
  const { t } = useTranslation("inspections");
  const { data, isLoading } = useDocumentDownload(doc.id);

  return (
    <FadeUp>
      <motion.div
        whileHover={{ y: -3 }}
        transition={{ duration: 0.2, ease: "easeOut" }}
        className="group relative overflow-hidden rounded-lg border border-line bg-bg-recessed"
      >
        {isLoading || !data ? (
          <Skeleton className="aspect-[4/3] w-full" />
        ) : (
          <button
            type="button"
            className="block w-full"
            onClick={() => onOpen(data.url)}
            aria-label={t("evidence.open")}
          >
            <img
              src={data.url}
              alt={doc.original_name}
              loading="lazy"
              className="aspect-[4/3] w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
            />
          </button>
        )}
        <div className="flex items-center justify-between gap-2 px-2.5 py-2">
          <span className="truncate text-caption text-text-tertiary" title={doc.original_name}>
            {doc.original_name}
          </span>
          <GuardedButton
            guard={deleteGuard}
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0"
            aria-label={t("evidence.delete")}
            onClick={onDelete}
          >
            <Trash2 />
          </GuardedButton>
        </div>
      </motion.div>
    </FadeUp>
  );
}

function AttachmentRow({
  doc,
  onDelete,
  deleteGuard,
}: {
  doc: RadalDocument;
  onDelete: () => void;
  deleteGuard: Guard;
}) {
  const { t } = useTranslation("inspections");
  const [wanted, setWanted] = React.useState(false);
  const { data } = useDocumentDownload(doc.id, wanted);

  React.useEffect(() => {
    if (wanted && data?.url) {
      window.open(data.url, "_blank", "noopener,noreferrer");
      setWanted(false);
    }
  }, [wanted, data]);

  return (
    <li className="flex items-center gap-3 rounded-lg border border-line px-3 py-2.5">
      <FileText className="h-4 w-4 shrink-0 text-text-muted" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-body text-text-primary">{doc.original_name}</p>
        <p className="text-caption text-text-muted">{formatDate(doc.created_at)}</p>
      </div>
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8"
        aria-label={t("evidence.open")}
        onClick={() => setWanted(true)}
      >
        <Download />
      </Button>
      <GuardedButton
        guard={deleteGuard}
        variant="ghost"
        size="icon"
        className="h-8 w-8"
        aria-label={t("evidence.delete")}
        onClick={onDelete}
      >
        <Trash2 />
      </GuardedButton>
    </li>
  );
}

export function EvidenceGallery({
  inspectionId,
  uploadGuard,
  deleteGuard,
}: {
  inspectionId: number;
  uploadGuard: Guard;
  deleteGuard: Guard;
}) {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const inputRef = React.useRef<HTMLInputElement>(null);

  const { data, isLoading } = useDocuments({
    entity_type: "inspection",
    entity_id: inspectionId,
    limit: 100,
  });
  const upload = useUploadDocument();
  const remove = useDeleteDocument();

  const [preview, setPreview] = React.useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = React.useState<RadalDocument | null>(null);

  // The signed report itself lives in its own card, so keep it out of here.
  const documents = (data?.items ?? []).filter(
    (doc) => doc.category !== "inspection_report",
  );
  const images = documents.filter(isImage);
  const files = documents.filter((doc) => !isImage(doc));

  const onFiles = (fileList: FileList | null) => {
    if (!fileList?.length) return;
    const picked = Array.from(fileList);
    let done = 0;
    picked.forEach((file) => {
      upload.mutate(
        { file, entity_type: "inspection", entity_id: inspectionId, category: "evidence" },
        {
          onSuccess: () => {
            done += 1;
            if (done === picked.length) toast.success(t("evidence.uploaded"));
          },
          onError: () => toast.error(t("error.save")),
        },
      );
    });
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <SectionCard
      title={t("evidence.title")}
      icon={<ImageIcon />}
      description={t("evidence.count", { count: documents.length })}
      actions={
        <>
          <input
            ref={inputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => onFiles(e.target.files)}
          />
          <GuardedButton
            guard={uploadGuard}
            variant="secondary"
            size="sm"
            disabled={upload.isPending}
            onClick={() => inputRef.current?.click()}
          >
            <Upload /> {upload.isPending ? t("evidence.uploading") : t("evidence.upload")}
          </GuardedButton>
        </>
      }
    >
      {isLoading ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="aspect-[4/3] w-full rounded-lg" />
          ))}
        </div>
      ) : documents.length === 0 ? (
        <EmptyState>{t("evidence.empty")}</EmptyState>
      ) : (
        <div className="flex flex-col gap-5">
          {images.length > 0 ? (
            <Stagger className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {images.map((doc) => (
                <EvidenceThumb
                  key={doc.id}
                  doc={doc}
                  onOpen={setPreview}
                  onDelete={() => setPendingDelete(doc)}
                  deleteGuard={deleteGuard}
                />
              ))}
            </Stagger>
          ) : null}

          {files.length > 0 ? (
            <div>
              <h3 className="mb-2 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-text-muted">
                {t("evidence.files")}
              </h3>
              <ul className="flex flex-col gap-2">
                {files.map((doc) => (
                  <AttachmentRow
                    key={doc.id}
                    doc={doc}
                    onDelete={() => setPendingDelete(doc)}
                    deleteGuard={deleteGuard}
                  />
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}

      <Dialog open={!!preview} onOpenChange={(open) => (open ? undefined : setPreview(null))}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{t("evidence.title")}</DialogTitle>
          </DialogHeader>
          {preview ? (
            <img
              src={preview}
              alt={t("evidence.title")}
              className="max-h-[70vh] w-full rounded-lg object-contain"
            />
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!pendingDelete}
        onOpenChange={(open) => (open ? undefined : setPendingDelete(null))}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t("evidence.deleteTitle")}</DialogTitle>
            <DialogDescription>{t("evidence.deleteDescription")}</DialogDescription>
          </DialogHeader>
          <p className="truncate text-body text-text-secondary">
            {pendingDelete?.original_name}
          </p>
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => setPendingDelete(null)}
              disabled={remove.isPending}
            >
              {tc("actions.cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => {
                if (!pendingDelete) return;
                remove.mutate(pendingDelete.id, {
                  onSuccess: () => {
                    toast.success(t("evidence.deleted"));
                    setPendingDelete(null);
                  },
                  onError: () => toast.error(t("error.save")),
                });
              }}
            >
              {remove.isPending ? tc("actions.loading") : tc("actions.delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SectionCard>
  );
}
