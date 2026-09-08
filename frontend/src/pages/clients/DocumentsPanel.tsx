import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Download, FileText, Loader2, Trash2, Upload } from "lucide-react";
import api, { API_URL } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import {
  useDeleteDocument,
  useDocuments,
  useUploadDocument,
} from "@/api/documents";
import {
  DOCUMENT_CATEGORIES,
  type DocumentCategory,
  type DocumentDownload,
  type EntityType,
  type RadalDocument,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { FadeUp, Stagger } from "@/components/common/motion";
import { SoonButton } from "@/pages/clients/Soon";

/**
 * The documents tab, shared by the client and placement detail pages.
 *
 * `document` is the ONLY table that holds an S3 key, so every file here is
 * fetched through `GET /documents?entity_type=&entity_id=` and opened through
 * `GET /documents/{id}/download`, which mints the (expiring) URL server-side.
 */
interface DocumentsPanelProps {
  entityType: EntityType;
  entityId: number;
  /** Categories offered in the upload picker; defaults to the full vocabulary. */
  categories?: readonly DocumentCategory[];
  defaultCategory?: DocumentCategory;
}

function humanSize(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function DocumentsPanel({
  entityType,
  entityId,
  categories = DOCUMENT_CATEGORIES,
  defaultCategory = "other",
}: DocumentsPanelProps) {
  const { t } = useTranslation(["clients", "common"]);
  const { data, isLoading } = useDocuments({
    entity_type: entityType,
    entity_id: entityId,
    limit: 100,
  });
  const upload = useUploadDocument();
  const remove = useDeleteDocument();
  const canUpload = useCan("Documents", "Upload");
  const canDelete = useCan("Documents", "Delete");

  const fileRef = React.useRef<HTMLInputElement>(null);
  const [category, setCategory] = React.useState<DocumentCategory>(defaultCategory);
  const [downloadingId, setDownloadingId] = React.useState<number | null>(null);

  const onPick = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      await upload.mutateAsync({
        file,
        entity_type: entityType,
        entity_id: entityId,
        category,
      });
      toast.success(t("clients:documents.uploaded"));
    } catch {
      toast.error(t("common:toast.error"));
    }
  };

  const onDownload = async (doc: RadalDocument) => {
    setDownloadingId(doc.id);
    try {
      const { data: link } = await api.get<DocumentDownload>(
        `/documents/${doc.id}/download`,
      );
      // The local backend answers with a root-relative path (`/media/...`),
      // which must resolve against the API origin, not the SPA origin.
      const href = /^https?:\/\//i.test(link.url)
        ? link.url
        : new URL(link.url, API_URL.replace(/\/api\/v1\/?$/, "/")).toString();
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.target = "_blank";
      anchor.rel = "noopener";
      anchor.click();
    } catch {
      toast.error(t("clients:documents.downloadError"));
    } finally {
      setDownloadingId(null);
    }
  };

  const onDelete = async (doc: RadalDocument) => {
    try {
      await remove.mutateAsync(doc.id);
      toast.success(t("common:toast.deleted"));
    } catch {
      toast.error(t("common:toast.error"));
    }
  };

  const items = data?.items ?? [];

  return (
    <div className="flex flex-col gap-4">
      <FadeUp className="flex flex-wrap items-end gap-3">
        <div className="min-w-[220px]">
          <Label className="mb-1.5 block text-caption text-text-muted">
            {t("clients:documents.category")}
          </Label>
          <Select
            value={category}
            onValueChange={(value) => setCategory(value as DocumentCategory)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {categories.map((value) => (
                <SelectItem key={value} value={value}>
                  {t(`clients:documents.categories.${value}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <input
          ref={fileRef}
          type="file"
          className="hidden"
          onChange={onPick}
          aria-hidden
          tabIndex={-1}
        />

        {canUpload.allowed ? (
          <Button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={upload.isPending}
          >
            {upload.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Upload />
            )}
            {t("clients:documents.upload")}
          </Button>
        ) : (
          <SoonButton
            label={t("clients:documents.upload")}
            reason={t("clients:permissions.noUpload")}
            icon={<Upload />}
          />
        )}
      </FadeUp>

      {isLoading ? (
        <div className="flex flex-col gap-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-14 w-full rounded-card" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <Card className="p-10 text-center text-body text-text-muted">
          {t("clients:documents.empty")}
        </Card>
      ) : (
        <Stagger className="flex flex-col gap-2">
          {items.map((doc) => (
            <FadeUp key={doc.id}>
              <Card
                interactive
                className="flex flex-wrap items-center gap-3 p-3.5"
              >
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand-soft text-brand-deep">
                  <FileText className="h-[18px] w-[18px]" strokeWidth={1.75} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-label font-medium text-text-primary">
                    {doc.original_name}
                  </p>
                  <p className="truncate text-caption text-text-muted">
                    {t(`clients:documents.categories.${doc.category}`)} ·{" "}
                    {humanSize(doc.size_bytes)} · {formatDateTime(doc.created_at)}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => void onDownload(doc)}
                  disabled={downloadingId === doc.id}
                >
                  {downloadingId === doc.id ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Download />
                  )}
                  {t("common:actions.download")}
                </Button>
                {canDelete.allowed ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => void onDelete(doc)}
                    disabled={remove.isPending}
                    aria-label={t("common:actions.delete")}
                  >
                    <Trash2 />
                  </Button>
                ) : (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          disabled
                          aria-label={t("common:actions.delete")}
                        >
                          <Trash2 />
                        </Button>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      {t("clients:permissions.noDelete")}
                    </TooltipContent>
                  </Tooltip>
                )}
              </Card>
            </FadeUp>
          ))}
        </Stagger>
      )}
    </div>
  );
}
