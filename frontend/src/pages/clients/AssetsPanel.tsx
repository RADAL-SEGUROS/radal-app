import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AxiosError } from "axios";
import { Building2, Briefcase, Loader2, MapPin, Plus } from "lucide-react";
import { useClientAssets, useCreateAsset } from "@/api/assets";
import {
  ASSET_STATUSES,
  type AssetCreate,
  type AssetListItem,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { FadeUp, Stagger } from "@/components/common/motion";
import { useCan } from "@/lib/permissions";
import { formatNumber } from "@/lib/format";
import { num } from "@/api/types";
import { AssetStatusBadge } from "@/pages/clients/status";
import { SoonButton } from "@/pages/clients/Soon";

/**
 * The assets (bienes asegurables) of one client.
 *
 * Storage is hybrid: the ten promoted underwriting attributes are columns, so
 * the form edits them directly; the type-specific `attributes` JSON tail is
 * shaped by the insurance line and is shown read-only in the detail sheet
 * (there is no line-driven field schema endpoint to render a form from yet).
 */

/** Suggestions only — `asset_type` is an OPEN vocabulary on the backend. */
const ASSET_TYPE_SUGGESTIONS = [
  "industrial_plant",
  "distribution_centre",
  "office",
  "warehouse",
  "clinic",
  "retail",
  "fleet",
];

const schema = z.object({
  asset_type: z.string().min(1),
  name: z.string().min(1),
  address: z.string().optional(),
  commune: z.string().optional(),
  region: z.string().optional(),
  status: z.enum(ASSET_STATUSES),
  built_area_m2: z.string().optional(),
  land_area_m2: z.string().optional(),
  construction_year: z.string().optional(),
  floors: z.string().optional(),
  structure: z.string().optional(),
  activity: z.string().optional(),
  fire_station_distance_km: z.string().optional(),
  seismic_zone: z.string().optional(),
});

type Values = z.infer<typeof schema>;

function text(value: string | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function decimal(value: string | undefined): number | null {
  const trimmed = value?.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed.replace(",", "."));
  return Number.isFinite(parsed) ? parsed : null;
}

function serverMessage(error: unknown, fallback: string): string {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line py-2 last:border-0">
      <span className="text-caption text-text-muted">{label}</span>
      <span className="text-right text-body text-text-primary">{value ?? "—"}</span>
    </div>
  );
}

function CreateAssetDialog({
  clientId,
  open,
  onOpenChange,
}: {
  clientId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation(["clients", "common"]);
  const create = useCreateAsset();

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { asset_type: "", name: "", status: "active" },
  });

  React.useEffect(() => {
    if (open) form.reset({ asset_type: "", name: "", status: "active" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const onSubmit = async (values: Values) => {
    const payload: AssetCreate & { client_id: number } = {
      client_id: clientId,
      asset_type: values.asset_type.trim(),
      name: values.name.trim(),
      address: text(values.address),
      commune: text(values.commune),
      region: text(values.region),
      status: values.status,
      built_area_m2: decimal(values.built_area_m2),
      land_area_m2: decimal(values.land_area_m2),
      construction_year: decimal(values.construction_year),
      floors: decimal(values.floors),
      structure: text(values.structure),
      activity: text(values.activity),
      fire_station_distance_km: decimal(values.fire_station_distance_km),
      seismic_zone: text(values.seismic_zone),
    };
    try {
      await create.mutateAsync(payload);
      toast.success(t("clients:assets.created"));
      onOpenChange(false);
    } catch (error) {
      toast.error(serverMessage(error, t("common:toast.error")));
    }
  };

  const field = (
    label: string,
    input: React.ReactNode,
    error?: boolean,
    span?: boolean,
  ) => (
    <div className={span ? "flex flex-col gap-1.5 sm:col-span-2" : "flex flex-col gap-1.5"}>
      <Label className="text-caption text-text-muted">{label}</Label>
      {input}
      {error ? (
        <p className="text-caption text-red-deep">{t("clients:form.errors.required")}</p>
      ) : null}
    </div>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("clients:assets.createTitle")}</DialogTitle>
          <DialogDescription>{t("clients:assets.createSubtitle")}</DialogDescription>
        </DialogHeader>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="grid gap-3.5 sm:grid-cols-2"
        >
          {field(
            t("clients:assets.fields.name"),
            <Input {...form.register("name")} />,
            !!form.formState.errors.name,
            true,
          )}
          {field(
            t("clients:assets.fields.type"),
            <>
              <Input
                list="asset-type-suggestions"
                placeholder="industrial_plant"
                {...form.register("asset_type")}
              />
              <datalist id="asset-type-suggestions">
                {ASSET_TYPE_SUGGESTIONS.map((value) => (
                  <option key={value} value={value} />
                ))}
              </datalist>
            </>,
            !!form.formState.errors.asset_type,
          )}
          {field(
            t("clients:assets.fields.status"),
            <Select
              value={form.watch("status")}
              onValueChange={(value) =>
                form.setValue("status", value as Values["status"])
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ASSET_STATUSES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`clients:assets.status.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>,
          )}
          {field(
            t("clients:assets.fields.address"),
            <Input {...form.register("address")} />,
            false,
            true,
          )}
          {field(t("clients:assets.fields.commune"), <Input {...form.register("commune")} />)}
          {field(t("clients:assets.fields.region"), <Input {...form.register("region")} />)}
          {field(
            t("clients:assets.fields.builtArea"),
            <Input inputMode="decimal" {...form.register("built_area_m2")} />,
          )}
          {field(
            t("clients:assets.fields.landArea"),
            <Input inputMode="decimal" {...form.register("land_area_m2")} />,
          )}
          {field(
            t("clients:assets.fields.constructionYear"),
            <Input inputMode="numeric" {...form.register("construction_year")} />,
          )}
          {field(
            t("clients:assets.fields.floors"),
            <Input inputMode="numeric" {...form.register("floors")} />,
          )}
          {field(
            t("clients:assets.fields.structure"),
            <Input {...form.register("structure")} />,
          )}
          {field(
            t("clients:assets.fields.activity"),
            <Input {...form.register("activity")} />,
          )}
          {field(
            t("clients:assets.fields.fireStation"),
            <Input inputMode="decimal" {...form.register("fire_station_distance_km")} />,
          )}
          {field(
            t("clients:assets.fields.seismicZone"),
            <Input {...form.register("seismic_zone")} />,
          )}

          <DialogFooter className="sm:col-span-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
            >
              {t("common:actions.cancel")}
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? <Loader2 className="animate-spin" /> : null}
              {t("common:actions.create")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function AssetsPanel({ clientId }: { clientId: number }) {
  const { t } = useTranslation(["clients", "common"]);
  const navigate = useNavigate();
  const assets = useClientAssets(clientId, { page_size: 100 });
  const canCreate = useCan("Assets", "Create");
  const [createOpen, setCreateOpen] = React.useState(false);
  const [selected, setSelected] = React.useState<AssetListItem | null>(null);

  const items = assets.data?.items ?? [];

  return (
    <div className="flex flex-col gap-4">
      <FadeUp className="flex items-center justify-between gap-3">
        <p className="text-caption text-text-muted">
          {t("clients:assets.count", { count: assets.data?.total ?? 0 })}
        </p>
        {canCreate.allowed ? (
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus />
            {t("clients:assets.new")}
          </Button>
        ) : (
          <SoonButton
            size="sm"
            label={t("clients:assets.new")}
            reason={t("clients:permissions.noCreate")}
            icon={<Plus />}
          />
        )}
      </FadeUp>

      {assets.isLoading ? (
        <div className="grid gap-3 md:grid-cols-2">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-28 w-full rounded-card" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <Card className="p-10 text-center text-body text-text-muted">
          {t("clients:assets.empty")}
        </Card>
      ) : (
        <Stagger className="grid gap-3 md:grid-cols-2">
          {items.map((asset) => (
            <FadeUp key={asset.id}>
              <Card
                interactive
                role="button"
                tabIndex={0}
                onClick={() => setSelected(asset)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelected(asset);
                  }
                }}
                className="cursor-pointer p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2.5">
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-[color-mix(in_srgb,var(--teal)_12%,transparent)] text-teal-deep">
                      <Building2 className="h-[18px] w-[18px]" strokeWidth={1.75} />
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-label font-semibold text-text-primary">
                        {asset.name}
                      </p>
                      <p className="truncate text-caption text-text-muted">
                        {t(`clients:assets.types.${asset.asset_type}`, {
                          defaultValue: asset.asset_type,
                        })}
                      </p>
                    </div>
                  </div>
                  <AssetStatusBadge status={asset.status} />
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-text-muted">
                  {asset.commune ? (
                    <span className="inline-flex items-center gap-1">
                      <MapPin className="h-3.5 w-3.5" />
                      {asset.commune}
                      {asset.region ? `, ${asset.region}` : ""}
                    </span>
                  ) : null}
                  <span className="inline-flex items-center gap-1">
                    <Briefcase className="h-3.5 w-3.5" />
                    {t("clients:assets.placementsCount", {
                      count: asset.active_placements_count,
                    })}
                  </span>
                  {num(asset.built_area_m2) !== null ? (
                    <span className="font-mono text-mono-sm tabular-nums">
                      {formatNumber(num(asset.built_area_m2))} m²
                    </span>
                  ) : null}
                </div>
              </Card>
            </FadeUp>
          ))}
        </Stagger>
      )}

      <CreateAssetDialog
        clientId={clientId}
        open={createOpen}
        onOpenChange={setCreateOpen}
      />

      <Sheet open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-md">
          {selected ? (
            <>
              <SheetHeader>
                <SheetTitle>{selected.name}</SheetTitle>
                <SheetDescription>
                  {t(`clients:assets.types.${selected.asset_type}`, {
                    defaultValue: selected.asset_type,
                  })}
                </SheetDescription>
              </SheetHeader>
              <div className="mt-5 flex flex-col">
                <Row
                  label={t("clients:assets.fields.status")}
                  value={<AssetStatusBadge status={selected.status} />}
                />
                <Row
                  label={t("clients:assets.fields.address")}
                  value={selected.address || "—"}
                />
                <Row
                  label={t("clients:assets.fields.commune")}
                  value={selected.commune || "—"}
                />
                <Row
                  label={t("clients:assets.fields.region")}
                  value={selected.region || "—"}
                />
                <Row
                  label={t("clients:assets.fields.builtArea")}
                  value={
                    num(selected.built_area_m2) !== null
                      ? `${formatNumber(num(selected.built_area_m2))} m²`
                      : "—"
                  }
                />
                <Row
                  label={t("clients:assets.fields.landArea")}
                  value={
                    num(selected.land_area_m2) !== null
                      ? `${formatNumber(num(selected.land_area_m2))} m²`
                      : "—"
                  }
                />
                <Row
                  label={t("clients:assets.fields.constructionYear")}
                  value={selected.construction_year ?? "—"}
                />
                <Row
                  label={t("clients:assets.fields.activity")}
                  value={selected.activity || "—"}
                />
                <Row
                  label={t("clients:assets.fields.placements")}
                  value={`${selected.active_placements_count} / ${selected.placements_count}`}
                />
              </div>
              <div className="mt-5 flex flex-col gap-2">
                <Button
                  variant="secondary"
                  onClick={() =>
                    navigate(`/placements?asset_id=${selected.id}&new=1`)
                  }
                >
                  <Plus />
                  {t("clients:assets.newPlacement")}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => navigate(`/placements?asset_id=${selected.id}`)}
                >
                  <Briefcase />
                  {t("clients:assets.viewPlacements")}
                </Button>
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}
