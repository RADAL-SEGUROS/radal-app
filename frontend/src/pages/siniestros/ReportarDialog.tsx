import * as React from "react";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import type { AxiosError } from "axios";

import api from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { siniestrosKeys } from "./api";

interface ClienteOption {
  id: number;
  nombre: string;
}

interface PolizaOption {
  id: number;
  numero_poliza: string;
  cliente?: { id: number; nombre: string } | null;
}

interface Paginated<T> {
  items: T[];
}

async function fetchList<T>(path: string): Promise<T[]> {
  const { data } = await api.get<Paginated<T> | T[]>(path, {
    params: { page_size: 200 },
  });
  return Array.isArray(data) ? data : data.items;
}

/** Build the zod schema with localized required-field messages. */
function buildSchema(req: string) {
  return z.object({
    cliente_id: z.string().min(1, req),
    poliza_id: z.string().min(1, req),
    tipo: z.string().trim().min(1, req),
    fecha_evento: z.string().min(1, req),
    monto_estimado: z
      .string()
      .optional()
      .refine((v) => !v || Number(v) >= 0, { params: { code: "positivo" } }),
    descripcion: z.string().trim().min(1, req),
  });
}

type FormValues = z.infer<ReturnType<typeof buildSchema>>;

export function ReportarDialog({
  triggerVariant = "primary",
}: {
  triggerVariant?: "primary" | "secondary";
}) {
  const { t } = useTranslation("siniestros");
  const { t: tc } = useTranslation("common");
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);

  const requiredMsg = t("form.errors.required");
  const schema = React.useMemo(() => buildSchema(requiredMsg), [requiredMsg]);
  const today = React.useMemo(() => new Date().toISOString().slice(0, 10), []);

  const {
    control,
    register,
    handleSubmit,
    reset,
    watch,
    setValue,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      cliente_id: "",
      poliza_id: "",
      tipo: "",
      fecha_evento: today,
      monto_estimado: "",
      descripcion: "",
    },
  });

  const clientesQuery = useQuery({
    queryKey: ["clientes", "refs"],
    queryFn: () => fetchList<ClienteOption>("/clientes"),
    enabled: open,
  });

  const polizasQuery = useQuery({
    queryKey: ["polizas", "refs"],
    queryFn: () => fetchList<PolizaOption>("/polizas"),
    enabled: open,
  });

  const clienteId = watch("cliente_id");

  // Only show pólizas belonging to the chosen cliente (when known).
  const polizasFiltradas = React.useMemo(() => {
    const all = polizasQuery.data ?? [];
    if (!clienteId) return all;
    const filtered = all.filter(
      (p) => String(p.cliente?.id ?? "") === clienteId,
    );
    return filtered.length ? filtered : all;
  }, [polizasQuery.data, clienteId]);

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const body = {
        cliente_id: Number(values.cliente_id),
        poliza_id: Number(values.poliza_id),
        tipo: values.tipo.trim(),
        fecha_evento: values.fecha_evento,
        monto_estimado:
          values.monto_estimado && values.monto_estimado !== ""
            ? Number(values.monto_estimado)
            : null,
        descripcion: values.descripcion.trim(),
        estado: "reportado" as const,
      };
      const { data } = await api.post("/siniestros", body);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: siniestrosKeys.all });
      toast.success(t("form.success"));
      reset();
      setOpen(false);
    },
    onError: (err: AxiosError<{ detail?: string }>) => {
      toast.error(err.response?.data?.detail ?? t("form.error"));
    },
  });

  const onOpenChange = (next: boolean) => {
    if (!next) reset();
    setOpen(next);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button variant={triggerVariant} className="font-semibold">
          <ShieldAlert className="h-4 w-4" />
          {t("actions.reportar")}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("form.title")}</DialogTitle>
          <DialogDescription>{t("form.subtitle")}</DialogDescription>
        </DialogHeader>

        <form
          onSubmit={handleSubmit((v) => mutation.mutate(v))}
          className="grid gap-4"
        >
          {/* Cliente */}
          <Field label={t("form.cliente")} error={errors.cliente_id?.message}>
            <Controller
              control={control}
              name="cliente_id"
              render={({ field }) => (
                <Select
                  value={field.value}
                  onValueChange={(v) => {
                    field.onChange(v);
                    setValue("poliza_id", "");
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t("form.clientePlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {(clientesQuery.data ?? []).map((c) => (
                      <SelectItem key={c.id} value={String(c.id)}>
                        {c.nombre}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
          </Field>

          {/* Póliza */}
          <Field label={t("form.poliza")} error={errors.poliza_id?.message}>
            <Controller
              control={control}
              name="poliza_id"
              render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger>
                    <SelectValue placeholder={t("form.polizaPlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {polizasFiltradas.map((p) => (
                      <SelectItem key={p.id} value={String(p.id)}>
                        {p.numero_poliza}
                        {p.cliente ? ` · ${p.cliente.nombre}` : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
          </Field>

          {/* Tipo + Fecha evento */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label={t("form.tipo")} error={errors.tipo?.message}>
              <Input
                {...register("tipo")}
                placeholder={t("form.tipoPlaceholder")}
              />
            </Field>
            <Field
              label={t("form.fechaEvento")}
              error={errors.fecha_evento?.message}
            >
              <Input type="date" max={today} {...register("fecha_evento")} />
            </Field>
          </div>

          {/* Monto estimado */}
          <Field
            label={t("form.montoEstimado")}
            error={errors.monto_estimado ? t("form.errors.montoPositivo") : undefined}
          >
            <Input
              type="number"
              step="0.01"
              min="0"
              inputMode="decimal"
              className="font-mono tabular-nums"
              {...register("monto_estimado")}
              placeholder="0"
            />
          </Field>

          {/* Descripción */}
          <Field
            label={t("form.descripcion")}
            error={errors.descripcion?.message}
          >
            <textarea
              {...register("descripcion")}
              rows={3}
              placeholder={t("form.descripcionPlaceholder")}
              className="flex w-full rounded-md border border-line bg-bg-surface px-3 py-2 text-body text-text-primary placeholder:text-text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--bg-app)]"
            />
          </Field>

          <DialogFooter className="mt-2 gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
            >
              {tc("actions.cancel")}
            </Button>
            <Button type="submit" variant="primary" disabled={mutation.isPending}>
              {mutation.isPending ? t("form.saving") : t("form.submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  error,
  children,
}: {
  label: React.ReactNode;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      {children}
      {error ? (
        <p className={cn("text-caption text-signal-danger")}>{error}</p>
      ) : null}
    </div>
  );
}
