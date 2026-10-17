/**
 * C2 · Registrar gestión (with the optional "Promete pagar el") and
 * C1.4 · PAC rechazado.
 *
 * A gestión WITH a promise date goes to `POST …/promise` (the cuota sleeps
 * "En acuerdo" until that day); WITHOUT one it is a plain cuota note through
 * `/notes` — the same table the server writes for its own gestiones, so both
 * feed "Última gestión" on the board.
 */
import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { addDays, format } from "date-fns";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ErrorBanner } from "@/components/common/kit";
import {
  useCreateInstallmentNote,
  useMarkDirectDebitRejected,
  useRegisterPaymentPromise,
  type BoardInstallmentRow,
} from "@/api/collections";
import { Field, todaySantiago } from "@/components/collections/shared";
import { cn } from "@/lib/utils";

function plusDays(isoDay: string, days: number): string {
  return format(addDays(new Date(`${isoDay}T12:00:00`), days), "yyyy-MM-dd");
}

export function GestionForm({
  row,
  onDone,
  onCancel,
  promiseFirst = false,
}: {
  row: BoardInstallmentRow;
  onDone: () => void;
  onCancel: () => void;
  /** Opened from "Promesa de pago": the date field is the point. */
  promiseFirst?: boolean;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const promise = useRegisterPaymentPromise(row.id);
  const note = useCreateInstallmentNote(row.id);
  const today = todaySantiago();

  const schema = React.useMemo(
    () =>
      z
        .object({
          body: z.string().trim().min(1, t("collections:form.required")).max(4000),
          promise_until: z.string(),
        })
        .superRefine((v, ctx) => {
          if (promiseFirst && !v.promise_until) {
            ctx.addIssue({ code: "custom", path: ["promise_until"], message: t("collections:form.required") });
          }
          if (v.promise_until && v.promise_until < today) {
            ctx.addIssue({ code: "custom", path: ["promise_until"], message: t("collections:gestion.promisePast") });
          }
        }),
    [t, today, promiseFirst],
  );
  type Values = z.infer<typeof schema>;
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { body: "", promise_until: "" },
  });
  const { errors } = form.formState;
  const promiseUntil = form.watch("promise_until");
  const pending = promise.isPending || note.isPending;

  const chips: { label: string; value: string }[] = [
    { label: t("collections:gestion.chipTomorrow"), value: plusDays(today, 1) },
    { label: t("collections:gestion.chip3Days"), value: plusDays(today, 3) },
    { label: t("collections:gestion.chipWeek"), value: plusDays(today, 7) },
  ];

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      if (values.promise_until) {
        await promise.mutateAsync({ promise_until: values.promise_until, note: values.body.trim() });
        toast.success(t("collections:gestion.savedPromise"));
      } else {
        await note.mutateAsync(values.body.trim());
        toast.success(t("collections:gestion.saved"));
      }
      onDone();
    } catch {
      // ErrorBanner shows the server's reason.
    }
  });

  return (
    <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-4" noValidate>
      {row.last_note ? (
        <div className="rounded-lg bg-paper-2 px-3 py-2.5 text-caption text-ink-2">
          <span className="font-medium text-ink">{t("collections:gestion.previous")}</span>{" "}
          {row.last_note.body}
        </div>
      ) : null}
      <Field label={t("collections:gestion.what")} htmlFor="gestion-body" error={errors.body?.message}>
        <Textarea
          id="gestion-body"
          rows={2}
          placeholder={t("collections:gestion.whatPlaceholder")}
          {...form.register("body")}
        />
      </Field>
      <Field
        label={t("collections:gestion.promiseUntil")}
        htmlFor="gestion-promise"
        error={errors.promise_until?.message}
        hint={t("collections:gestion.promiseHint")}
      >
        <Input id="gestion-promise" type="date" min={today} {...form.register("promise_until")} />
        <div className="flex flex-wrap gap-1.5">
          {chips.map((chip) => (
            <button
              key={chip.value}
              type="button"
              onClick={() => form.setValue("promise_until", chip.value, { shouldValidate: true })}
              className={cn(
                "min-h-8 rounded-full px-3 py-1 text-caption transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.96]",
                promiseUntil === chip.value
                  ? "bg-brand-soft font-medium text-brand-deep"
                  : "border border-dashed border-line text-ink-2 hover:text-ink",
              )}
            >
              {chip.label}
            </button>
          ))}
          {promiseUntil ? (
            <button
              type="button"
              onClick={() => form.setValue("promise_until", "")}
              className="min-h-8 px-2 text-caption text-ink-3 underline-offset-2 hover:underline"
            >
              {t("collections:gestion.noPromise")}
            </button>
          ) : null}
        </div>
      </Field>

      <ErrorBanner error={promise.error ?? note.error} />

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>
          {t("common:actions.cancel")}
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {promiseUntil ? t("collections:gestion.submitPromise") : t("collections:gestion.submit")}
        </Button>
      </div>
    </form>
  );
}

export function DebitRejectedForm({
  row,
  onDone,
  onCancel,
}: {
  row: BoardInstallmentRow;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const mutation = useMarkDirectDebitRejected(row.id);
  const today = todaySantiago();
  const [rejectedOn, setRejectedOn] = React.useState(today);
  const [noteText, setNoteText] = React.useState("");
  const invalid = !rejectedOn || rejectedOn > today;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (invalid) return;
    try {
      await mutation.mutateAsync({ rejected_on: rejectedOn, note: noteText.trim() || null });
      toast.success(t("collections:debit.saved"));
      onDone();
    } catch {
      // ErrorBanner.
    }
  };

  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-4" noValidate>
      <p className="text-caption text-ink-3">{t("collections:debit.description")}</p>
      <Field
        label={t("collections:debit.rejectedOn")}
        htmlFor="debit-date"
        error={rejectedOn > today ? t("collections:form.noFutureDate") : null}
      >
        <Input
          id="debit-date"
          type="date"
          max={today}
          value={rejectedOn}
          onChange={(e) => setRejectedOn(e.target.value)}
        />
      </Field>
      <Field label={t("collections:debit.note")} htmlFor="debit-note">
        <Textarea id="debit-note" rows={2} value={noteText} onChange={(e) => setNoteText(e.target.value)} />
      </Field>
      <ErrorBanner error={mutation.error} />
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={mutation.isPending}>
          {t("common:actions.cancel")}
        </Button>
        <Button type="submit" variant="destructive" disabled={mutation.isPending || invalid}>
          {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {t("collections:debit.submit")}
        </Button>
      </div>
    </form>
  );
}
