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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAssets } from "@/api/assets";
import { usePlacements } from "@/api/placements";
import {
  useCreateInspectionRequest,
  useUpdateInspectionRequest,
} from "@/api/inspections";
import { PRIORITIES } from "@/api/types";
import type {
  InspectionRequest,
  InspectionRequestStatus,
  Priority,
} from "@/api/types";
import { INSPECTION_REQUEST_STATUSES } from "@/api/types";
import { Field } from "./shared";

/**
 * Create / edit an inspection request.
 *
 * `POST /inspection-requests` and `PATCH /inspection-requests/{id}`. The asset
 * picker is fed by `GET /assets` and the placement picker only appears once an
 * asset is chosen, because the server rejects a placement that covers another
 * asset (422).
 */

const NONE = "__none__";

interface Draft {
  asset_id: string;
  placement_id: string;
  urgency: Priority;
  target_date: string;
  reason: string;
  status: InspectionRequestStatus;
}

const EMPTY: Draft = {
  asset_id: "",
  placement_id: NONE,
  urgency: "normal",
  target_date: "",
  reason: "",
  status: "pending",
};

/** Editing an existing request keeps the same form; `request` drives the mode. */
export function RequestFormDialog({
  request,
  open,
  onOpenChange,
}: {
  request?: InspectionRequest | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");

  const [draft, setDraft] = React.useState<Draft>(EMPTY);
  const [error, setError] = React.useState<string | null>(null);

  const { data: assetsPage } = useAssets({ page_size: 100 }, open);
  const assetId = draft.asset_id ? Number(draft.asset_id) : undefined;
  const { data: placementsPage } = usePlacements(
    { asset_id: assetId, page_size: 50 },
    open && !!assetId,
  );

  const create = useCreateInspectionRequest();
  const update = useUpdateInspectionRequest(request?.id ?? 0);
  const pending = create.isPending || update.isPending;

  React.useEffect(() => {
    if (!open) return;
    setError(null);
    setDraft(
      request
        ? {
            asset_id: String(request.asset_id),
            placement_id: request.placement_id ? String(request.placement_id) : NONE,
            urgency: request.urgency,
            target_date: request.target_date ?? "",
            reason: request.reason ?? "",
            status: request.status,
          }
        : EMPTY,
    );
  }, [open, request]);

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!draft.asset_id) {
      setError(t("requests.form.assetRequired"));
      return;
    }
    const shared = {
      placement_id: draft.placement_id === NONE ? null : Number(draft.placement_id),
      urgency: draft.urgency,
      target_date: draft.target_date || null,
      reason: draft.reason.trim() || null,
    };

    if (request) {
      update.mutate(
        { ...shared, status: draft.status },
        {
          onSuccess: () => {
            toast.success(t("requests.toast.updated"));
            onOpenChange(false);
          },
          onError: () => toast.error(t("error.save")),
        },
      );
    } else {
      create.mutate(
        { asset_id: Number(draft.asset_id), ...shared },
        {
          onSuccess: () => {
            toast.success(t("requests.toast.created"));
            onOpenChange(false);
          },
          onError: () => toast.error(t("error.save")),
        },
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{request ? t("requests.edit") : t("requests.new")}</DialogTitle>
          <DialogDescription>{t("subtitle")}</DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <Field label={t("requests.form.asset")} error={error ?? undefined}>
            <Select
              value={draft.asset_id}
              disabled={!!request}
              onValueChange={(value) =>
                setDraft((p) => ({ ...p, asset_id: value, placement_id: NONE }))
              }
            >
              <SelectTrigger>
                <SelectValue placeholder={t("requests.form.assetPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {(assetsPage?.items ?? []).map((asset) => (
                  <SelectItem key={asset.id} value={String(asset.id)}>
                    {asset.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label={t("requests.form.placement")}>
            <Select
              value={draft.placement_id}
              disabled={!assetId}
              onValueChange={(value) => setDraft((p) => ({ ...p, placement_id: value }))}
            >
              <SelectTrigger>
                <SelectValue placeholder={t("requests.form.placementPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>{t("requests.form.placementPlaceholder")}</SelectItem>
                {(placementsPage?.items ?? []).map((placement) => (
                  <SelectItem key={placement.id} value={String(placement.id)}>
                    {placement.insurance_line?.name ?? `#${placement.id}`}
                    {placement.period ? ` · ${placement.period}` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("requests.form.urgency")}>
              <Select
                value={draft.urgency}
                onValueChange={(value) =>
                  setDraft((p) => ({ ...p, urgency: value as Priority }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRIORITIES.map((priority) => (
                    <SelectItem key={priority} value={priority}>
                      {t(`urgency.${priority}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label={t("requests.form.targetDate")} htmlFor="request-target-date">
              <Input
                id="request-target-date"
                type="date"
                value={draft.target_date}
                onChange={(e) => setDraft((p) => ({ ...p, target_date: e.target.value }))}
              />
            </Field>
          </div>

          {request ? (
            <Field label={t("requests.form.status")}>
              <Select
                value={draft.status}
                onValueChange={(value) =>
                  setDraft((p) => ({ ...p, status: value as InspectionRequestStatus }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {INSPECTION_REQUEST_STATUSES.map((status) => (
                    <SelectItem key={status} value={status}>
                      {t(`status.request.${status}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          ) : null}

          <Field label={t("requests.form.reason")} htmlFor="request-reason">
            <Input
              id="request-reason"
              value={draft.reason}
              placeholder={t("requests.form.reasonPlaceholder")}
              onChange={(e) => setDraft((p) => ({ ...p, reason: e.target.value }))}
            />
          </Field>

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
              {pending
                ? tc("actions.loading")
                : request
                  ? t("requests.form.save")
                  : t("requests.form.submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
