import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useUpdateInspection } from "@/api/inspections";
import { num } from "@/api/types";
import type { Inspection, InspectionUpdate } from "@/api/types";
import { Field } from "./shared";

/** Edit the promoted score columns. Wired to PATCH /inspections/{id}. */

const NUMERIC_FIELDS = [
  "technical_score",
  "commercial_score",
  "location_score",
  "loss_estimate_score",
  "overall_score",
  "pml_pct",
  "eml_pct",
] as const;

type NumericField = (typeof NUMERIC_FIELDS)[number];

const LABEL_KEY: Record<NumericField, string> = {
  technical_score: "scores.technical",
  commercial_score: "scores.commercial",
  location_score: "scores.location",
  loss_estimate_score: "scores.lossEstimate",
  overall_score: "scores.overall",
  pml_pct: "scores.pml",
  eml_pct: "scores.eml",
};

function toInput(value: string | null): string {
  const parsed = num(value);
  return parsed === null ? "" : String(parsed);
}

export function ScoresDialog({
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
  const update = useUpdateInspection(inspection.id);

  const [values, setValues] = React.useState<Record<NumericField, string>>(() => ({
    technical_score: toInput(inspection.technical_score),
    commercial_score: toInput(inspection.commercial_score),
    location_score: toInput(inspection.location_score),
    loss_estimate_score: toInput(inspection.loss_estimate_score),
    overall_score: toInput(inspection.overall_score),
    pml_pct: toInput(inspection.pml_pct),
    eml_pct: toInput(inspection.eml_pct),
  }));
  const [classification, setClassification] = React.useState(
    inspection.risk_classification ?? "",
  );

  // Re-seed whenever the dialog is (re)opened on a freshly fetched report.
  React.useEffect(() => {
    if (!open) return;
    setValues({
      technical_score: toInput(inspection.technical_score),
      commercial_score: toInput(inspection.commercial_score),
      location_score: toInput(inspection.location_score),
      loss_estimate_score: toInput(inspection.loss_estimate_score),
      overall_score: toInput(inspection.overall_score),
      pml_pct: toInput(inspection.pml_pct),
      eml_pct: toInput(inspection.eml_pct),
    });
    setClassification(inspection.risk_classification ?? "");
  }, [open, inspection]);

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const payload: InspectionUpdate = {
      risk_classification: classification.trim() || null,
    };
    for (const field of NUMERIC_FIELDS) {
      const raw = values[field].trim();
      (payload as Record<string, unknown>)[field] = raw === "" ? null : Number(raw);
    }
    update.mutate(payload, {
      onSuccess: () => {
        toast.success(t("scores.saved"));
        onOpenChange(false);
      },
      onError: () => toast.error(t("error.save")),
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("scores.edit")}</DialogTitle>
          <DialogDescription>{t("scores.range")}</DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {NUMERIC_FIELDS.map((field) => (
              <Field key={field} label={t(LABEL_KEY[field])} htmlFor={`score-${field}`}>
                <Input
                  id={`score-${field}`}
                  type="number"
                  min={0}
                  max={100}
                  step="0.01"
                  value={values[field]}
                  onChange={(e) =>
                    setValues((prev) => ({ ...prev, [field]: e.target.value }))
                  }
                />
              </Field>
            ))}
          </div>
          <Field label={t("scores.classification")} htmlFor="score-classification">
            <Input
              id="score-classification"
              value={classification}
              placeholder={t("scores.classificationPlaceholder")}
              onChange={(e) => setClassification(e.target.value)}
            />
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
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
