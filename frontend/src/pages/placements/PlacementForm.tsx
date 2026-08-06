import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AxiosError } from "axios";
import { Loader2 } from "lucide-react";
import { useAssets } from "@/api/assets";
import {
  useCreatePlacement,
  useTransitionPlacement,
  useUpdatePlacement,
} from "@/api/placements";
import type {
  Placement,
  PlacementCreate,
  PlacementStatus,
  PlacementUpdate,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { SoonNote } from "@/pages/clients/Soon";
import { useInsuranceLines } from "@/pages/placements/useInsuranceLines";

function serverMessage(error: unknown, fallback: string): string {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function Field({
  label,
  error,
  children,
  className,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label className="text-caption text-text-muted">{label}</Label>
      {children}
      {error ? <p className="text-caption text-red-deep">{error}</p> : null}
    </div>
  );
}

function text(value: string | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

const textareaClass =
  "w-full rounded-[10px] border border-line bg-bg-surface px-3.5 py-2 text-body text-text-primary outline-none transition-[border-color,box-shadow] duration-150 focus-visible:border-teal focus-visible:shadow-[0_0_0_3px_color-mix(in_srgb,var(--teal)_18%,transparent)]";

// --- Create ------------------------------------------------------------------

const createSchema = z.object({
  asset_id: z.string().min(1),
  insurance_line_id: z.string().min(1),
  period: z.string().optional(),
  period_start: z.string().optional(),
  period_end: z.string().optional(),
  notes: z.string().optional(),
});

type CreateValues = z.infer<typeof createSchema>;

/**
 * Open a placement: one asset x one insurance line x one period.
 *
 * `client_id` is NOT in the payload — the server derives it from the asset,
 * which is why the asset picker is the only owner selection here.
 */
export function CreatePlacementDialog({
  open,
  onOpenChange,
  clientId,
  assetId,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pre-filters the asset picker to one client. */
  clientId?: number;
  /** Pre-selects an asset (deep link from the client's asset sheet). */
  assetId?: number;
  onCreated?: (placement: Placement) => void;
}) {
  const { t } = useTranslation(["placements", "common"]);
  const create = useCreatePlacement();
  const assets = useAssets({ client_id: clientId, page_size: 200, status: ["active"] });
  const { lines, isLoading: linesLoading } = useInsuranceLines();

  const form = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: { asset_id: "", insurance_line_id: "" },
  });

  React.useEffect(() => {
    if (open) {
      form.reset({
        asset_id: assetId ? String(assetId) : "",
        insurance_line_id: "",
        period: "",
        period_start: "",
        period_end: "",
        notes: "",
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, assetId]);

  const onSubmit = async (values: CreateValues) => {
    const payload: PlacementCreate = {
      asset_id: Number(values.asset_id),
      insurance_line_id: Number(values.insurance_line_id),
      period: text(values.period),
      period_start: text(values.period_start),
      period_end: text(values.period_end),
      notes: text(values.notes),
    };
    try {
      const placement = await create.mutateAsync(payload);
      toast.success(t("placements:toast.created"));
      onOpenChange(false);
      onCreated?.(placement);
    } catch (error) {
      toast.error(serverMessage(error, t("common:toast.error")));
    }
  };

  const assetItems = assets.data?.items ?? [];
  const noAssets = !assets.isLoading && assetItems.length === 0;
  const noLines = !linesLoading && lines.length === 0;
  const blocked = noAssets || noLines;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("placements:form.createTitle")}</DialogTitle>
          <DialogDescription>{t("placements:form.createSubtitle")}</DialogDescription>
        </DialogHeader>

        {noAssets ? <SoonNote>{t("placements:form.noAssets")}</SoonNote> : null}
        {noLines ? <SoonNote>{t("placements:form.noLines")}</SoonNote> : null}

        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="grid gap-3.5 sm:grid-cols-2"
        >
          <Field
            label={t("placements:fields.asset")}
            error={
              form.formState.errors.asset_id
                ? t("placements:form.errors.required")
                : undefined
            }
            className="sm:col-span-2"
          >
            <Select
              value={form.watch("asset_id")}
              onValueChange={(value) => form.setValue("asset_id", value)}
              disabled={noAssets}
            >
              <SelectTrigger>
                <SelectValue placeholder={t("placements:fields.assetPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {assetItems.map((asset) => (
                  <SelectItem key={asset.id} value={String(asset.id)}>
                    {asset.name}
                    {asset.client?.legal_name ? ` — ${asset.client.legal_name}` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field
            label={t("placements:fields.insuranceLine")}
            error={
              form.formState.errors.insurance_line_id
                ? t("placements:form.errors.required")
                : undefined
            }
            className="sm:col-span-2"
          >
            <Select
              value={form.watch("insurance_line_id")}
              onValueChange={(value) => form.setValue("insurance_line_id", value)}
              disabled={noLines}
            >
              <SelectTrigger>
                <SelectValue placeholder={t("placements:fields.linePlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {lines.map((line) => (
                  <SelectItem key={line.id} value={String(line.id)}>
                    {line.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label={t("placements:fields.period")} className="sm:col-span-2">
            <Input placeholder="2025-2026" {...form.register("period")} />
          </Field>
          <Field label={t("placements:fields.periodStart")}>
            <Input type="date" {...form.register("period_start")} />
          </Field>
          <Field label={t("placements:fields.periodEnd")}>
            <Input type="date" {...form.register("period_end")} />
          </Field>
          <Field label={t("placements:fields.notes")} className="sm:col-span-2">
            <textarea rows={3} className={textareaClass} {...form.register("notes")} />
          </Field>

          <DialogFooter className="sm:col-span-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
            >
              {t("common:actions.cancel")}
            </Button>
            <Button type="submit" disabled={create.isPending || blocked}>
              {create.isPending ? <Loader2 className="animate-spin" /> : null}
              {t("common:actions.create")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// --- Edit --------------------------------------------------------------------

const editSchema = z.object({
  insurance_line_id: z.string().min(1),
  period: z.string().optional(),
  period_start: z.string().optional(),
  period_end: z.string().optional(),
  notes: z.string().optional(),
});

type EditValues = z.infer<typeof editSchema>;

/** Status is deliberately absent — it moves only through the transition API. */
export function EditPlacementDialog({
  placement,
  open,
  onOpenChange,
}: {
  placement: Placement;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation(["placements", "common"]);
  const update = useUpdatePlacement(placement.id);
  const { lines } = useInsuranceLines();

  const defaults = React.useMemo<EditValues>(
    () => ({
      insurance_line_id: String(placement.insurance_line_id),
      period: placement.period ?? "",
      period_start: placement.period_start ?? "",
      period_end: placement.period_end ?? "",
      notes: placement.notes ?? "",
    }),
    [placement],
  );

  const form = useForm<EditValues>({
    resolver: zodResolver(editSchema),
    defaultValues: defaults,
  });

  React.useEffect(() => {
    if (open) form.reset(defaults);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, defaults]);

  // The current line always has to be selectable, even when this broker has no
  // other placement on it (the options are derived, see useInsuranceLines).
  const options = React.useMemo(() => {
    const known = lines.some((line) => line.id === placement.insurance_line_id);
    if (known) return lines;
    return [
      {
        id: placement.insurance_line_id,
        name: placement.insurance_line?.name ?? `#${placement.insurance_line_id}`,
      },
      ...lines,
    ];
  }, [lines, placement]);

  const onSubmit = async (values: EditValues) => {
    const payload: PlacementUpdate = {
      insurance_line_id: Number(values.insurance_line_id),
      period: text(values.period),
      period_start: text(values.period_start),
      period_end: text(values.period_end),
      notes: text(values.notes),
    };
    try {
      await update.mutateAsync(payload);
      toast.success(t("common:toast.saved"));
      onOpenChange(false);
    } catch (error) {
      toast.error(serverMessage(error, t("common:toast.error")));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("placements:form.editTitle")}</DialogTitle>
          <DialogDescription>{t("placements:form.editSubtitle")}</DialogDescription>
        </DialogHeader>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="grid gap-3.5 sm:grid-cols-2"
        >
          <Field label={t("placements:fields.insuranceLine")} className="sm:col-span-2">
            <Select
              value={form.watch("insurance_line_id")}
              onValueChange={(value) => form.setValue("insurance_line_id", value)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options.map((line) => (
                  <SelectItem key={line.id} value={String(line.id)}>
                    {line.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t("placements:fields.period")} className="sm:col-span-2">
            <Input {...form.register("period")} />
          </Field>
          <Field label={t("placements:fields.periodStart")}>
            <Input type="date" {...form.register("period_start")} />
          </Field>
          <Field label={t("placements:fields.periodEnd")}>
            <Input type="date" {...form.register("period_end")} />
          </Field>
          <Field label={t("placements:fields.notes")} className="sm:col-span-2">
            <textarea rows={3} className={textareaClass} {...form.register("notes")} />
          </Field>
          <DialogFooter className="sm:col-span-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
            >
              {t("common:actions.cancel")}
            </Button>
            <Button type="submit" disabled={update.isPending}>
              {update.isPending ? <Loader2 className="animate-spin" /> : null}
              {t("common:actions.save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// --- Transition --------------------------------------------------------------

/**
 * Move a placement along the state machine.
 *
 * The target status always comes from `GET /placements/{id}/transitions`, so
 * this dialog can never post an illegal move; a 409 from a stale list is
 * surfaced verbatim.
 */
export function TransitionDialog({
  placementId,
  target,
  onOpenChange,
}: {
  placementId: number;
  /** `null` closes the dialog. */
  target: PlacementStatus | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation(["placements", "common"]);
  const transition = useTransitionPlacement(placementId);
  const [note, setNote] = React.useState("");

  React.useEffect(() => {
    if (target) setNote("");
  }, [target]);

  const onConfirm = async () => {
    if (!target) return;
    try {
      await transition.mutateAsync({ status: target, note: note.trim() || null });
      toast.success(
        t("placements:toast.transitioned", { status: t(`placements:status.${target}`) }),
      );
      onOpenChange(false);
    } catch (error) {
      toast.error(serverMessage(error, t("common:toast.error")));
    }
  };

  return (
    <Dialog open={!!target} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("placements:transition.title")}</DialogTitle>
          <DialogDescription>
            {target
              ? t("placements:transition.description", {
                  status: t(`placements:status.${target}`),
                })
              : null}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-1.5">
          <Label className="text-caption text-text-muted">
            {t("placements:transition.note")}
          </Label>
          <textarea
            rows={3}
            className={textareaClass}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {t("common:actions.cancel")}
          </Button>
          <Button onClick={() => void onConfirm()} disabled={transition.isPending}>
            {transition.isPending ? <Loader2 className="animate-spin" /> : null}
            {t("common:actions.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
