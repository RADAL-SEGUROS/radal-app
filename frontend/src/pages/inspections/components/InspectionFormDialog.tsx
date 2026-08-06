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
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAssets } from "@/api/assets";
import { useCreateInspection } from "@/api/inspections";
import type { InspectionRequest, UserSummary } from "@/api/types";
import { Field } from "./shared";

/**
 * Create an inspection report — `POST /inspections`.
 *
 * The version number is assigned by the server (max+1 per asset), so it is not
 * a form field. When the dialog is opened from a request, the asset is locked
 * to that request's asset because the server rejects a mismatch (422).
 */

const NONE = "__none__";

export function InspectionFormDialog({
  open,
  onOpenChange,
  fromRequest,
  inspectors,
  inspectorsAvailable,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  fromRequest?: InspectionRequest | null;
  inspectors: UserSummary[];
  /** False when the role cannot read the user list; the picker is then hidden. */
  inspectorsAvailable: boolean;
}) {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();
  const create = useCreateInspection();

  const [assetId, setAssetId] = React.useState("");
  const [inspectorId, setInspectorId] = React.useState(NONE);
  const [visitDate, setVisitDate] = React.useState("");
  const [folio, setFolio] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const { data: assetsPage } = useAssets({ page_size: 100 }, open && !fromRequest);

  React.useEffect(() => {
    if (!open) return;
    setError(null);
    setAssetId(fromRequest ? String(fromRequest.asset_id) : "");
    setInspectorId(NONE);
    setVisitDate(fromRequest?.target_date ?? "");
    setFolio("");
  }, [open, fromRequest]);

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!assetId) {
      setError(t("requests.form.assetRequired"));
      return;
    }
    create.mutate(
      {
        asset_id: Number(assetId),
        inspection_request_id: fromRequest ? fromRequest.id : null,
        inspector_id: inspectorId === NONE ? null : Number(inspectorId),
        visit_date: visitDate || null,
        folio: folio.trim() || null,
      },
      {
        onSuccess: (created) => {
          toast.success(t("reports.toast.created"));
          onOpenChange(false);
          navigate(`/inspections/${created.id}`);
        },
        onError: () => toast.error(t("error.save")),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("reports.new")}</DialogTitle>
          <DialogDescription>{t("subtitle")}</DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <Field label={t("reports.form.asset")} error={error ?? undefined}>
            <Select
              value={assetId}
              disabled={!!fromRequest}
              onValueChange={setAssetId}
            >
              <SelectTrigger>
                <SelectValue placeholder={t("requests.form.assetPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {fromRequest ? (
                  <SelectItem value={String(fromRequest.asset_id)}>
                    {`#${fromRequest.asset_id}`}
                  </SelectItem>
                ) : null}
                {(assetsPage?.items ?? []).map((asset) => (
                  <SelectItem key={asset.id} value={String(asset.id)}>
                    {asset.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          {inspectorsAvailable ? (
            <Field label={t("reports.form.inspector")}>
              <Select value={inspectorId} onValueChange={setInspectorId}>
                <SelectTrigger>
                  <SelectValue placeholder={t("reports.form.inspectorPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>{t("reports.form.inspectorPlaceholder")}</SelectItem>
                  {inspectors.map((user) => (
                    <SelectItem key={user.id} value={String(user.id)}>
                      {user.full_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          ) : null}

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("reports.form.visitDate")} htmlFor="inspection-visit-date">
              <Input
                id="inspection-visit-date"
                type="date"
                value={visitDate}
                onChange={(e) => setVisitDate(e.target.value)}
              />
            </Field>
            <Field label={t("reports.form.folio")} htmlFor="inspection-folio">
              <Input
                id="inspection-folio"
                value={folio}
                placeholder={t("reports.form.folioPlaceholder")}
                onChange={(e) => setFolio(e.target.value)}
              />
            </Field>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
              disabled={create.isPending}
            >
              {tc("actions.cancel")}
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? tc("actions.loading") : t("reports.form.submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
