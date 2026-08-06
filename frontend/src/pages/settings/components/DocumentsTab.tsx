import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { ColumnDef } from "@tanstack/react-table";
import { Download, FolderOpen, Pencil, Trash2, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataTable } from "@/components/common/DataTable";
import {
  useDeleteDocument,
  useDocumentDownload,
  useDocuments,
  useUpdateDocument,
  useUploadDocument,
} from "@/api/documents";
import { useAuth } from "@/providers/AuthProvider";
import { useModulePermissions } from "@/lib/permissions";
import { formatDateTime } from "@/lib/format";
import {
  DOCUMENT_CATEGORIES,
  ENTITY_TYPES,
  type DocumentCategory,
  type EntityType,
  type RadalDocument,
} from "@/api/types";
import { Field, GuardedButton, SectionCard, formatBytes, type Guard } from "./shared";

/**
 * The broker's whole file registry — `GET /documents`, the one table that holds
 * an S3 key. Upload here attaches to the BROKER entity; attaching to a client,
 * asset or proposal happens from that record's own page, which is why the
 * uploader does not offer an entity picker.
 */

const ALL = "__all__";

/** Download is a two-step: ask for a short-lived URL, then open it. */
function DownloadButton({ doc }: { doc: RadalDocument }) {
  const { t } = useTranslation("settings");
  const [wanted, setWanted] = React.useState(false);
  const { data } = useDocumentDownload(doc.id, wanted);

  React.useEffect(() => {
    if (wanted && data?.url) {
      window.open(data.url, "_blank", "noopener,noreferrer");
      setWanted(false);
    }
  }, [wanted, data]);

  return (
    <Button
      variant="ghost"
      size="icon"
      className="h-8 w-8"
      aria-label={t("documents.download")}
      onClick={() => setWanted(true)}
    >
      <Download />
    </Button>
  );
}

function EditDocumentDialog({ doc, onDone }: { doc: RadalDocument; onDone: () => void }) {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const update = useUpdateDocument(doc.id);
  const [name, setName] = React.useState(doc.original_name);
  const [category, setCategory] = React.useState<DocumentCategory>(doc.category);
  const [phase, setPhase] = React.useState(doc.phase ?? "");

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onDone())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("documents.renameTitle")}</DialogTitle>
          <DialogDescription>{t("documents.renameDescription")}</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            update.mutate(
              {
                original_name: name.trim() || doc.original_name,
                category,
                phase: phase.trim() || null,
              },
              {
                onSuccess: () => {
                  toast.success(t("documents.updated"));
                  onDone();
                },
                onError: () => toast.error(t("error.generic")),
              },
            );
          }}
        >
          <Field label={t("documents.name")} htmlFor="doc-name">
            <Input id="doc-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("documents.category")}>
            <Select
              value={category}
              onValueChange={(value) => setCategory(value as DocumentCategory)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DOCUMENT_CATEGORIES.map((entry) => (
                  <SelectItem key={entry} value={entry}>
                    {t(`categories.${entry}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t("documents.phase")} htmlFor="doc-phase">
            <Input
              id="doc-phase"
              value={phase}
              placeholder={t("documents.phasePlaceholder")}
              onChange={(e) => setPhase(e.target.value)}
            />
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={onDone}
              disabled={update.isPending}
            >
              {tc("actions.cancel")}
            </Button>
            <Button type="submit" disabled={update.isPending}>
              {update.isPending ? tc("actions.loading") : tc("actions.save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function UploadDialog({
  brokerId,
  open,
  onOpenChange,
}: {
  brokerId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const upload = useUploadDocument();
  const [file, setFile] = React.useState<File | null>(null);
  const [category, setCategory] = React.useState<DocumentCategory>("other");

  React.useEffect(() => {
    if (!open) {
      setFile(null);
      setCategory("other");
    }
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("documents.uploadTitle")}</DialogTitle>
          <DialogDescription>{t("documents.uploadDescription")}</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!file) return;
            upload.mutate(
              { file, entity_type: "broker", entity_id: brokerId, category },
              {
                onSuccess: () => {
                  toast.success(t("documents.uploaded"));
                  onOpenChange(false);
                },
                onError: () => toast.error(t("error.generic")),
              },
            );
          }}
        >
          <Field label={t("documents.file")} htmlFor="doc-file">
            <Input
              id="doc-file"
              type="file"
              className="h-auto py-2"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </Field>
          <Field label={t("documents.category")}>
            <Select
              value={category}
              onValueChange={(value) => setCategory(value as DocumentCategory)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DOCUMENT_CATEGORIES.map((entry) => (
                  <SelectItem key={entry} value={entry}>
                    {t(`categories.${entry}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
              disabled={upload.isPending}
            >
              {tc("actions.cancel")}
            </Button>
            <Button type="submit" disabled={upload.isPending || !file}>
              {upload.isPending ? t("documents.uploading") : t("documents.upload")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function DocumentsTab() {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const { organization } = useAuth();
  const perms = useModulePermissions("Documents");

  const [category, setCategory] = React.useState(ALL);
  const [entityType, setEntityType] = React.useState(ALL);
  const [uploadOpen, setUploadOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<RadalDocument | null>(null);
  const [deleting, setDeleting] = React.useState<RadalDocument | null>(null);

  const { data, isLoading } = useDocuments(
    {
      category: category === ALL ? undefined : (category as DocumentCategory),
      entity_type: entityType === ALL ? undefined : (entityType as EntityType),
      limit: 200,
    },
    perms.view,
  );
  const remove = useDeleteDocument();

  const uploadGuard: Guard = perms.upload
    ? { allowed: true, reason: "" }
    : { allowed: false, reason: t("guard.noPermission") };
  const editGuard: Guard = perms.edit
    ? { allowed: true, reason: "" }
    : { allowed: false, reason: t("guard.noPermission") };
  const deleteGuard: Guard = perms.remove
    ? { allowed: true, reason: "" }
    : { allowed: false, reason: t("guard.noPermission") };

  const columns = React.useMemo<ColumnDef<RadalDocument>[]>(
    () => [
      {
        accessorKey: "original_name",
        header: t("documents.columns.name"),
        cell: ({ row }) => (
          <div className="min-w-0">
            <p className="truncate font-medium text-text-primary">
              {row.original.original_name}
            </p>
            <p className="truncate font-mono text-mono-sm text-text-muted">
              {row.original.mime_type ?? "—"}
            </p>
          </div>
        ),
      },
      {
        accessorKey: "category",
        header: t("documents.columns.category"),
        cell: ({ row }) => (
          <Badge variant="outline">{t(`categories.${row.original.category}`)}</Badge>
        ),
      },
      {
        accessorKey: "entity_type",
        header: t("documents.columns.entity"),
        cell: ({ row }) => (
          <span className="text-text-secondary">
            {t(`entities.${row.original.entity_type}`)} #{row.original.entity_id}
          </span>
        ),
      },
      {
        accessorKey: "size_bytes",
        header: t("documents.columns.size"),
        cell: ({ row }) => formatBytes(row.original.size_bytes),
      },
      {
        accessorKey: "created_at",
        header: t("documents.columns.uploadedAt"),
        cell: ({ row }) => formatDateTime(row.original.created_at),
      },
      {
        id: "actions",
        header: "",
        enableSorting: false,
        cell: ({ row }) => (
          <div className="flex items-center justify-end gap-1">
            <DownloadButton doc={row.original} />
            <GuardedButton
              guard={editGuard}
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              aria-label={t("documents.rename")}
              onClick={() => setEditing(row.original)}
            >
              <Pencil />
            </GuardedButton>
            <GuardedButton
              guard={deleteGuard}
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              aria-label={t("documents.delete")}
              onClick={() => setDeleting(row.original)}
            >
              <Trash2 />
            </GuardedButton>
          </div>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t, editGuard.allowed, deleteGuard.allowed],
  );

  const items = data?.items ?? [];

  return (
    <SectionCard
      title={t("documents.title")}
      icon={<FolderOpen />}
      description={t("documents.description")}
      actions={
        <GuardedButton
          guard={
            organization
              ? uploadGuard
              : { allowed: false, reason: t("profile.empty") }
          }
          size="sm"
          onClick={() => setUploadOpen(true)}
        >
          <Upload /> {t("documents.upload")}
        </GuardedButton>
      }
      bodyClassName="p-5 pt-4"
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select value={category} onValueChange={setCategory}>
          <SelectTrigger className="h-9 w-[220px]">
            <SelectValue placeholder={t("documents.filterCategory")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("documents.allCategories")}</SelectItem>
            {DOCUMENT_CATEGORIES.map((entry) => (
              <SelectItem key={entry} value={entry}>
                {t(`categories.${entry}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={entityType} onValueChange={setEntityType}>
          <SelectTrigger className="h-9 w-[220px]">
            <SelectValue placeholder={t("documents.filterEntity")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("documents.allEntities")}</SelectItem>
            {ENTITY_TYPES.map((entry) => (
              <SelectItem key={entry} value={entry}>
                {t(`entities.${entry}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="ml-auto text-caption text-text-muted">
          {t("documents.total", { count: data?.total ?? 0 })}
        </span>
      </div>

      <DataTable
        columns={columns}
        data={items}
        isLoading={isLoading}
        emptyMessage={t("documents.empty")}
      />

      {organization ? (
        <UploadDialog
          brokerId={organization.id}
          open={uploadOpen}
          onOpenChange={setUploadOpen}
        />
      ) : null}
      {editing ? (
        <EditDocumentDialog doc={editing} onDone={() => setEditing(null)} />
      ) : null}

      <Dialog
        open={!!deleting}
        onOpenChange={(open) => (open ? undefined : setDeleting(null))}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t("documents.deleteTitle")}</DialogTitle>
            <DialogDescription>{t("documents.deleteDescription")}</DialogDescription>
          </DialogHeader>
          <p className="truncate text-body text-text-secondary">
            {deleting?.original_name}
          </p>
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => setDeleting(null)}
              disabled={remove.isPending}
            >
              {tc("actions.cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => {
                if (!deleting) return;
                remove.mutate(deleting.id, {
                  onSuccess: () => {
                    toast.success(t("documents.deleted"));
                    setDeleting(null);
                  },
                  onError: () => toast.error(t("error.generic")),
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
