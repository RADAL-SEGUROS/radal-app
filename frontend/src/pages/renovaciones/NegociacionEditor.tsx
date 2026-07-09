import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import api from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface NegociacionEditorProps {
  renovacionId: number;
  value: string;
}

/**
 * Editable "Estado de negociación" narrative. Save button is bottom-right and
 * PATCHes /renovaciones/{id}.estado_negociacion_texto.
 */
export function NegociacionEditor({ renovacionId, value }: NegociacionEditorProps) {
  const { t } = useTranslation("renovaciones");
  const queryClient = useQueryClient();
  const [texto, setTexto] = React.useState(value ?? "");

  React.useEffect(() => {
    setTexto(value ?? "");
  }, [value]);

  const mutation = useMutation({
    mutationFn: async (nextValue: string) => {
      const res = await api.patch(`/renovaciones/${renovacionId}`, {
        estado_negociacion_texto: nextValue,
      });
      return res.data;
    },
    onSuccess: () => {
      toast.success(t("detail.negociacion.saved"));
      queryClient.invalidateQueries({ queryKey: ["renovacion", renovacionId] });
      queryClient.invalidateQueries({ queryKey: ["renovaciones"] });
    },
    onError: () => {
      toast.error(t("detail.negociacion.error"));
    },
  });

  const dirty = (texto ?? "") !== (value ?? "");

  return (
    <Card>
      <CardHeader className="gap-1">
        <CardTitle>{t("detail.negociacion.title")}</CardTitle>
        <p className="text-caption text-text-muted">
          {t("detail.negociacion.hint")}
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <textarea
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          placeholder={t("detail.negociacion.placeholder")}
          rows={6}
          className={cn(
            "w-full resize-y rounded-md border border-line bg-bg-surface px-3 py-2 text-body text-text-primary",
            "placeholder:text-text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        />
        <div className="flex justify-end">
          <Button
            variant="primary"
            size="sm"
            disabled={!dirty || mutation.isPending}
            onClick={() => mutation.mutate(texto)}
          >
            {mutation.isPending
              ? t("detail.negociacion.saving")
              : t("detail.negociacion.save")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
