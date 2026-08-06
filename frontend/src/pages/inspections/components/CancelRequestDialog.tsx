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
import { useCancelInspectionRequest } from "@/api/inspections";
import type { InspectionRequest } from "@/api/types";

/**
 * `POST /inspection-requests/{id}/cancel`.
 *
 * Mounted only while a request is selected, so the id-bound mutation hook has a
 * real id. A completed request cannot be cancelled (the server answers 409), so
 * the caller never offers the action for one.
 */
export function CancelRequestDialog({
  request,
  onDone,
}: {
  request: InspectionRequest;
  onDone: () => void;
}) {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const cancel = useCancelInspectionRequest(request.id);

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onDone())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("requests.cancel.title")}</DialogTitle>
          <DialogDescription>{t("requests.cancel.description")}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="secondary" onClick={onDone} disabled={cancel.isPending}>
            {tc("actions.cancel")}
          </Button>
          <Button
            variant="destructive"
            disabled={cancel.isPending}
            onClick={() =>
              cancel.mutate(undefined, {
                onSuccess: () => {
                  toast.success(t("requests.cancel.done"));
                  onDone();
                },
                onError: () => toast.error(t("error.save")),
              })
            }
          >
            {cancel.isPending ? tc("actions.loading") : t("requests.cancel.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
