import * as React from "react";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ClipboardCheck, Info } from "lucide-react";
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
import type { Paginated, Ref, SolicitudCreate, Urgencia } from "./types";

const URGENCIAS: Urgencia[] = ["baja", "media", "alta"];

interface ActivoRef extends Ref {
  tipo_activo?: string | null;
}

/** Build the zod schema with localized required-field messages. */
function buildSchema(req: string) {
  return z.object({
    cliente_id: z.string().min(1, req),
    activo_id: z.string().min(1, req),
    motivo: z.string().trim().min(1, req),
    urgencia: z.enum(["baja", "media", "alta"]),
    fecha_objetivo: z.string().min(1, req),
  });
}

type FormValues = z.infer<ReturnType<typeof buildSchema>>;

async function fetchRefs(path: string): Promise<Ref[]> {
  const { data } = await api.get<Paginated<Ref> | Ref[]>(path);
  return Array.isArray(data) ? data : data.items;
}

export function SolicitarDialog() {
  const { t } = useTranslation("inspecciones");
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
      activo_id: "",
      motivo: "",
      urgencia: "media",
      fecha_objetivo: "",
    },
  });

  const clienteId = watch("cliente_id");

  const clientesQuery = useQuery({
    queryKey: ["clientes", "refs"],
    queryFn: () => fetchRefs("/clientes"),
    enabled: open,
  });

  const activosQuery = useQuery({
    queryKey: ["clientes", clienteId, "activos"],
    queryFn: async () => {
      const { data } = await api.get<Paginated<ActivoRef> | ActivoRef[]>(
        `/clientes/${clienteId}/activos`,
      );
      return Array.isArray(data) ? data : data.items;
    },
    enabled: open && Boolean(clienteId),
  });

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const body: SolicitudCreate = {
        activo_id: Number(values.activo_id),
        motivo: values.motivo.trim(),
        urgencia: values.urgencia,
        fecha_objetivo: values.fecha_objetivo,
      };
      const { data } = await api.post("/inspecciones/solicitudes", body);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inspecciones"] });
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
          <ClipboardCheck className="h-4 w-4" />
          {t("actions.solicitar")}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("form.title")}</DialogTitle>
          <DialogDescription>{t("form.subtitle")}</DialogDescription>
        </DialogHeader>

        <div className="flex items-start gap-2 rounded-md border border-line bg-bg-recessed/60 p-3">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-teal" />
          <p className="text-caption text-text-secondary">{t("form.ramoNote")}</p>
        </div>

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
                    setValue("activo_id", "");
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

          {/* Activo */}
          <Field label={t("form.activo")} error={errors.activo_id?.message}>
            <Controller
              control={control}
              name="activo_id"
              render={({ field }) => (
                <Select
                  value={field.value}
                  onValueChange={field.onChange}
                  disabled={!clienteId || activosQuery.isLoading}
                >
                  <SelectTrigger>
                    <SelectValue
                      placeholder={
                        !clienteId
                          ? t("form.activoDisabled")
                          : t("form.activoPlaceholder")
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {(activosQuery.data ?? []).map((a) => (
                      <SelectItem key={a.id} value={String(a.id)}>
                        {a.nombre}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
            {clienteId &&
            !activosQuery.isLoading &&
            (activosQuery.data ?? []).length === 0 ? (
              <p className="text-caption text-text-muted">
                {t("form.activoEmpty")}
              </p>
            ) : null}
          </Field>

          {/* Motivo */}
          <Field label={t("form.motivo")} error={errors.motivo?.message}>
            <textarea
              {...register("motivo")}
              rows={3}
              placeholder={t("form.motivoPlaceholder")}
              className={cn(
                "w-full resize-y rounded-md border border-line bg-bg-surface px-3 py-2 text-body text-text-primary",
                "placeholder:text-text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {/* Urgencia */}
            <Field label={t("form.urgencia")}>
              <Controller
                control={control}
                name="urgencia"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {URGENCIAS.map((u) => (
                        <SelectItem key={u} value={u}>
                          {t(`urgencia.${u}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </Field>

            {/* Fecha objetivo */}
            <Field
              label={t("form.fechaObjetivo")}
              error={errors.fecha_objetivo?.message}
            >
              <Input type="date" min={today} {...register("fecha_objetivo")} />
            </Field>
          </div>

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
      {error ? <p className="text-caption text-signal-danger">{error}</p> : null}
    </div>
  );
}
