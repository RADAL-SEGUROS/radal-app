import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useAssignInspection,
  useCreateInspectionVersion,
  useSetInspectionStatus,
} from "@/api/inspections";
import type { Inspection, InspectionStatus, UserSummary } from "@/api/types";
import { Field, GuardedButton, STATUS_TRANSITIONS, type Guard } from "./shared";

/** Assign / clear the inspector — `POST /inspections/{id}/assign`. */
export function AssignInspectorDialog({
  inspection,
  inspectors,
  open,
  onOpenChange,
}: {
  inspection: Inspection;
  inspectors: UserSummary[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const assign = useAssignInspection(inspection.id);
  const NONE = "__none__";
  const [value, setValue] = React.useState(
    inspection.inspector_id ? String(inspection.inspector_id) : NONE,
  );

  React.useEffect(() => {
    if (open) setValue(inspection.inspector_id ? String(inspection.inspector_id) : NONE);
  }, [open, inspection.inspector_id]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("detail.assign")}</DialogTitle>
          <DialogDescription>{t("reports.form.inspector")}</DialogDescription>
        </DialogHeader>
        <Field label={t("reports.form.inspector")}>
          <Select value={value} onValueChange={setValue}>
            <SelectTrigger>
              <SelectValue placeholder={t("reports.form.inspectorPlaceholder")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>{t("detail.unassign")}</SelectItem>
              {inspectors.map((user) => (
                <SelectItem key={user.id} value={String(user.id)}>
                  {user.full_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <DialogFooter>
          <Button
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={assign.isPending}
          >
            {tc("actions.cancel")}
          </Button>
          <Button
            disabled={assign.isPending}
            onClick={() =>
              assign.mutate(
                { inspector_id: value === NONE ? null : Number(value) },
                {
                  onSuccess: () => {
                    toast.success(t("detail.assigned"));
                    onOpenChange(false);
                  },
                  onError: () => toast.error(t("error.save")),
                },
              )
            }
          >
            {assign.isPending ? tc("actions.loading") : t("detail.assignSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Fork the report into the next version — `POST /inspections/{id}/versions`. */
export function NewVersionDialog({
  inspection,
  open,
  onOpenChange,
}: {
  inspection: Inspection;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();
  const createVersion = useCreateInspectionVersion(inspection.id);

  const [copyChecklist, setCopyChecklist] = React.useState(true);
  const [copyScores, setCopyScores] = React.useState(true);
  const [copyBoundaries, setCopyBoundaries] = React.useState(true);

  const options: Array<[string, boolean, (next: boolean) => void]> = [
    [t("detail.copyChecklist"), copyChecklist, setCopyChecklist],
    [t("detail.copyScores"), copyScores, setCopyScores],
    [t("detail.copyBoundaries"), copyBoundaries, setCopyBoundaries],
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("detail.newVersionTitle")}</DialogTitle>
          <DialogDescription>{t("detail.newVersionDescription")}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2.5">
          {options.map(([label, checked, setChecked]) => (
            <label
              key={label}
              className="flex cursor-pointer items-center gap-2.5 text-body text-text-secondary"
            >
              <input
                type="checkbox"
                className="h-4 w-4 accent-[var(--teal)]"
                checked={checked}
                onChange={(e) => setChecked(e.target.checked)}
              />
              {label}
            </label>
          ))}
        </div>
        <DialogFooter>
          <Button
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={createVersion.isPending}
          >
            {tc("actions.cancel")}
          </Button>
          <Button
            disabled={createVersion.isPending}
            onClick={() =>
              createVersion.mutate(
                {
                  copy_checklist: copyChecklist,
                  copy_scores: copyScores,
                  copy_boundaries: copyBoundaries,
                },
                {
                  onSuccess: (created) => {
                    toast.success(t("detail.versionCreated", { n: created.version }));
                    onOpenChange(false);
                    navigate(`/inspections/${created.id}`);
                  },
                  onError: () => toast.error(t("error.save")),
                },
              )
            }
          >
            {createVersion.isPending ? tc("actions.loading") : t("detail.newVersionSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Status transitions — one button per legal move, mirroring the server's
 * transition table. "Issued" additionally needs the signed report, so without
 * it the button is disabled with that exact reason instead of failing with 422.
 */
export function StatusActions({
  inspection,
  submitGuard,
}: {
  inspection: Inspection;
  submitGuard: Guard;
}) {
  const { t } = useTranslation("inspections");
  const setStatus = useSetInspectionStatus(inspection.id);
  const targets = STATUS_TRANSITIONS[inspection.status];

  if (targets.length === 0) {
    return <span className="text-caption text-text-muted">{t("detail.noTransitions")}</span>;
  }

  return (
    <>
      {targets.map((target: InspectionStatus) => {
        const needsReport = target === "issued" && !inspection.report_document_id;
        const guard: Guard = needsReport
          ? { allowed: false, reason: t("detail.needsReport") }
          : submitGuard;
        return (
          <GuardedButton
            key={target}
            guard={guard}
            variant={target === "issued" ? "primary" : "secondary"}
            size="sm"
            disabled={setStatus.isPending}
            onClick={() =>
              setStatus.mutate(
                { status: target },
                {
                  onSuccess: () => toast.success(t("detail.statusChanged")),
                  onError: () => toast.error(t("error.save")),
                },
              )
            }
          >
            {t("detail.moveTo", { status: t(`status.report.${target}`) })}
          </GuardedButton>
        );
      })}
    </>
  );
}
