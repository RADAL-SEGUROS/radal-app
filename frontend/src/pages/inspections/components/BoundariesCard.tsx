import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Compass, Pencil, Plus, Trash2 } from "lucide-react";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useAddInspectionBoundary,
  useDeleteInspectionBoundary,
  useInspectionBoundaries,
  useUpdateInspectionBoundary,
} from "@/api/inspections";
import type { InspectionBoundary } from "@/api/types";
import { EmptyState, Field, GuardedButton, SectionCard, type Guard } from "./shared";

/**
 * Colindancias — child rows with their own CRUD
 * (`/inspections/{id}/boundaries`). Every control here is wired; when the
 * report is frozen or the role lacks Inspections:Edit the buttons render
 * disabled with the reason.
 */

interface BoundaryDraft {
  orientation: string;
  description: string;
  distance: string;
  aggravating: string;
  is_aggravating: boolean;
}

const EMPTY_DRAFT: BoundaryDraft = {
  orientation: "",
  description: "",
  distance: "",
  aggravating: "",
  is_aggravating: false,
};

function BoundaryDialog({
  inspectionId,
  boundary,
  open,
  onOpenChange,
}: {
  inspectionId: number;
  boundary: InspectionBoundary | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const add = useAddInspectionBoundary(inspectionId);
  const update = useUpdateInspectionBoundary(inspectionId);
  const [draft, setDraft] = React.useState<BoundaryDraft>(EMPTY_DRAFT);

  React.useEffect(() => {
    if (!open) return;
    setDraft(
      boundary
        ? {
            orientation: boundary.orientation ?? "",
            description: boundary.description ?? "",
            distance: boundary.distance ?? "",
            aggravating: boundary.aggravating ?? "",
            is_aggravating: boundary.is_aggravating,
          }
        : EMPTY_DRAFT,
    );
  }, [open, boundary]);

  const pending = add.isPending || update.isPending;

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const payload = {
      orientation: draft.orientation.trim() || null,
      description: draft.description.trim() || null,
      distance: draft.distance.trim() || null,
      aggravating: draft.aggravating.trim() || null,
      is_aggravating: draft.is_aggravating,
    };
    const handlers = {
      onSuccess: () => {
        toast.success(boundary ? t("boundaries.updated") : t("boundaries.created"));
        onOpenChange(false);
      },
      onError: () => toast.error(t("error.save")),
    };
    if (boundary) {
      update.mutate({ boundaryId: boundary.id, ...payload }, handlers);
    } else {
      add.mutate(payload, handlers);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>
            {boundary ? t("boundaries.editTitle") : t("boundaries.addTitle")}
          </DialogTitle>
          <DialogDescription>{t("boundaries.title")}</DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <Field label={t("boundaries.orientation")} htmlFor="boundary-orientation">
            <Input
              id="boundary-orientation"
              value={draft.orientation}
              placeholder={t("boundaries.orientationPlaceholder")}
              onChange={(e) => setDraft((p) => ({ ...p, orientation: e.target.value }))}
            />
          </Field>
          <Field label={t("boundaries.description")} htmlFor="boundary-description">
            <Input
              id="boundary-description"
              value={draft.description}
              placeholder={t("boundaries.descriptionPlaceholder")}
              onChange={(e) => setDraft((p) => ({ ...p, description: e.target.value }))}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label={t("boundaries.distance")} htmlFor="boundary-distance">
              <Input
                id="boundary-distance"
                value={draft.distance}
                placeholder={t("boundaries.distancePlaceholder")}
                onChange={(e) => setDraft((p) => ({ ...p, distance: e.target.value }))}
              />
            </Field>
            <Field label={t("boundaries.aggravating")} htmlFor="boundary-aggravating">
              <Input
                id="boundary-aggravating"
                value={draft.aggravating}
                placeholder={t("boundaries.aggravatingPlaceholder")}
                onChange={(e) => setDraft((p) => ({ ...p, aggravating: e.target.value }))}
              />
            </Field>
          </div>
          <label className="flex cursor-pointer items-center gap-2.5 text-body text-text-secondary">
            <input
              type="checkbox"
              className="h-4 w-4 accent-[var(--teal)]"
              checked={draft.is_aggravating}
              onChange={(e) => setDraft((p) => ({ ...p, is_aggravating: e.target.checked }))}
            />
            {t("boundaries.isAggravating")}
          </label>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
              disabled={pending}
            >
              {tc("actions.cancel")}
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? tc("actions.loading") : tc("actions.save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteBoundaryDialog({
  inspectionId,
  boundary,
  onDone,
}: {
  inspectionId: number;
  boundary: InspectionBoundary;
  onDone: () => void;
}) {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const remove = useDeleteInspectionBoundary(inspectionId);

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onDone())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("boundaries.deleteTitle")}</DialogTitle>
          <DialogDescription>{t("boundaries.deleteDescription")}</DialogDescription>
        </DialogHeader>
        <p className="text-body text-text-secondary">
          {boundary.orientation ?? "—"} · {boundary.description ?? "—"}
        </p>
        <DialogFooter>
          <Button variant="secondary" onClick={onDone} disabled={remove.isPending}>
            {tc("actions.cancel")}
          </Button>
          <Button
            variant="destructive"
            disabled={remove.isPending}
            onClick={() =>
              remove.mutate(boundary.id, {
                onSuccess: () => {
                  toast.success(t("boundaries.deleted"));
                  onDone();
                },
                onError: () => toast.error(t("error.save")),
              })
            }
          >
            {remove.isPending ? tc("actions.loading") : tc("actions.delete")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function BoundariesCard({
  inspectionId,
  editGuard,
}: {
  inspectionId: number;
  editGuard: Guard;
}) {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const { data: boundaries = [], isLoading } = useInspectionBoundaries(inspectionId);

  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<InspectionBoundary | null>(null);
  const [deleting, setDeleting] = React.useState<InspectionBoundary | null>(null);

  const sorted = React.useMemo(
    () => [...boundaries].sort((a, b) => a.sort_order - b.sort_order || a.id - b.id),
    [boundaries],
  );

  return (
    <SectionCard
      title={t("boundaries.title")}
      icon={<Compass />}
      actions={
        <GuardedButton
          guard={editGuard}
          variant="secondary"
          size="sm"
          onClick={() => {
            setEditing(null);
            setDialogOpen(true);
          }}
        >
          <Plus /> {t("boundaries.add")}
        </GuardedButton>
      }
      bodyClassName="p-0"
    >
      {isLoading ? (
        <p className="p-5 text-body text-text-muted">{tc("state.loading")}</p>
      ) : sorted.length === 0 ? (
        <div className="p-5">
          <EmptyState>{t("boundaries.empty")}</EmptyState>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("boundaries.orientation")}</TableHead>
                <TableHead>{t("boundaries.description")}</TableHead>
                <TableHead>{t("boundaries.distance")}</TableHead>
                <TableHead>{t("boundaries.aggravating")}</TableHead>
                <TableHead className="w-[100px]" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((boundary) => (
                <TableRow key={boundary.id}>
                  <TableCell className="font-medium text-text-primary">
                    {boundary.orientation ?? "—"}
                  </TableCell>
                  <TableCell>{boundary.description ?? "—"}</TableCell>
                  <TableCell className="font-mono text-mono">
                    {boundary.distance ?? "—"}
                  </TableCell>
                  <TableCell>
                    <Badge variant={boundary.is_aggravating ? "warn" : "muted"}>
                      {boundary.aggravating ??
                        (boundary.is_aggravating ? tc("units.si") : tc("units.no"))}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-1">
                      <GuardedButton
                        guard={editGuard}
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        aria-label={tc("actions.edit")}
                        onClick={() => {
                          setEditing(boundary);
                          setDialogOpen(true);
                        }}
                      >
                        <Pencil />
                      </GuardedButton>
                      <GuardedButton
                        guard={editGuard}
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        aria-label={tc("actions.delete")}
                        onClick={() => setDeleting(boundary)}
                      >
                        <Trash2 />
                      </GuardedButton>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <BoundaryDialog
        inspectionId={inspectionId}
        boundary={editing}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
      />
      {deleting ? (
        <DeleteBoundaryDialog
          inspectionId={inspectionId}
          boundary={deleting}
          onDone={() => setDeleting(null)}
        />
      ) : null}
    </SectionCard>
  );
}
