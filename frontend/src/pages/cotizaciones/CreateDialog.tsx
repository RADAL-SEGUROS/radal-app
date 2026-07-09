import * as React from "react";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
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
import type {
  CotizacionCreate,
  Paginated,
  Prioridad,
  Ref,
} from "./types";

const PRIORIDADES: Prioridad[] = ["baja", "media", "alta"];

/** Build the zod schema with localized required-field messages. */
function buildSchema(req: string) {
  return z
    .object({
      cliente_id: z.string().min(1, req),
      ramo_id: z.string().min(1, req),
      bien_asegurar: z.string().trim().min(1, req),
      valor_declarado: z.coerce.number().positive(),
      fecha_envio: z.string().min(1, req),
      fecha_vence: z.string().min(1, req),
      prioridad: z.enum(["baja", "media", "alta"]),
    })
    .refine((v) => v.fecha_vence >= v.fecha_envio, {
      path: ["fecha_vence"],
      params: { code: "vence_before_envio" },
    });
}

type FormValues = z.infer<ReturnType<typeof buildSchema>>;

async function fetchRefs(path: string): Promise<Ref[]> {
  const { data } = await api.get<Paginated<Ref> | Ref[]>(path);
  return Array.isArray(data) ? data : data.items;
}

export function CreateDialog() {
  const { t } = useTranslation("cotizaciones");
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
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      cliente_id: "",
      ramo_id: "",
      bien_asegurar: "",
      valor_declarado: undefined as unknown as number,
      fecha_envio: today,
      fecha_vence: "",
      prioridad: "media",
    },
  });

  const clientesQuery = useQuery({
    queryKey: ["clientes", "refs"],
    queryFn: () => fetchRefs("/clientes"),
    enabled: open,
  });

  const ramosQuery = useQuery({
    queryKey: ["ramos"],
    queryFn: () => fetchRefs("/ramos"),
    enabled: open,
  });

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const body: CotizacionCreate = {
        cliente_id: Number(values.cliente_id),
        ramo_id: Number(values.ramo_id),
        bien_asegurar: values.bien_asegurar.trim(),
        valor_declarado: values.valor_declarado,
        fecha_envio: values.fecha_envio,
        fecha_vence: values.fecha_vence,
        prioridad: values.prioridad,
        estado: "pendiente",
      };
      const { data } = await api.post("/cotizaciones", body);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cotizaciones"] });
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
        <Button variant="primary" className="font-semibold">
          <Plus className="h-4 w-4" />
          {t("actions.nueva")}
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
                <Select value={field.value} onValueChange={field.onChange}>
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

          {/* Ramo */}
          <Field label={t("form.ramo")} error={errors.ramo_id?.message}>
            <Controller
              control={control}
              name="ramo_id"
              render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger>
                    <SelectValue placeholder={t("form.ramoPlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {(ramosQuery.data ?? []).map((r) => (
                      <SelectItem key={r.id} value={String(r.id)}>
                        {r.nombre}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
          </Field>

          {/* Bien a asegurar */}
          <Field
            label={t("form.bienAsegurar")}
            error={errors.bien_asegurar?.message}
          >
            <Input
              {...register("bien_asegurar")}
              placeholder={t("form.bienAsegurarPlaceholder")}
            />
          </Field>

          {/* Valor declarado */}
          <Field
            label={t("form.valorDeclarado")}
            error={
              errors.valor_declarado ? t("form.errors.valorPositivo") : undefined
            }
          >
            <Input
              type="number"
              step="0.01"
              min="0"
              inputMode="decimal"
              className="font-mono tabular-nums"
              {...register("valor_declarado")}
              placeholder="0"
            />
          </Field>

          {/* Fechas */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label={t("form.fechaEnvio")}
              error={errors.fecha_envio?.message}
            >
              <Input type="date" {...register("fecha_envio")} />
            </Field>
            <Field
              label={t("form.fechaVence")}
              error={
                errors.fecha_vence
                  ? errors.fecha_vence.message || t("form.errors.venceOrden")
                  : undefined
              }
            >
              <Input type="date" {...register("fecha_vence")} />
            </Field>
          </div>

          {/* Prioridad */}
          <Field label={t("form.prioridad")}>
            <Controller
              control={control}
              name="prioridad"
              render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PRIORIDADES.map((p) => (
                      <SelectItem key={p} value={p}>
                        {t(`prioridad.${p}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
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
            <Button
              type="submit"
              variant="primary"
              disabled={mutation.isPending}
            >
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
