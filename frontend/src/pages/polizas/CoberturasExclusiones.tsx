import { useTranslation } from "react-i18next";
import { Check, X } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface Props {
  coberturas: string[];
  exclusiones: string[];
}

function ItemList({
  items,
  tone,
  emptyLabel,
}: {
  items: string[];
  tone: "cobertura" | "exclusion";
  emptyLabel: string;
}) {
  if (!items || items.length === 0) {
    return <p className="text-body text-text-muted">{emptyLabel}</p>;
  }
  const Icon = tone === "cobertura" ? Check : X;
  return (
    <ul className="space-y-2.5">
      {items.map((item, i) => (
        <li key={i} className="flex items-start gap-2.5">
          <Icon
            className={cn(
              "mt-0.5 h-4 w-4 shrink-0",
              tone === "cobertura" ? "text-lime" : "text-signal-danger",
            )}
          />
          <span className="text-body text-text-primary">{item}</span>
        </li>
      ))}
    </ul>
  );
}

/** Bloque 5 — Coberturas vs Exclusiones, two columns. */
export function CoberturasExclusiones({ coberturas, exclusiones }: Props) {
  const { t } = useTranslation("polizas");
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>{t("detail.coberturas.coberturas")}</CardTitle>
        </CardHeader>
        <CardContent>
          <ItemList
            items={coberturas}
            tone="cobertura"
            emptyLabel={t("detail.coberturas.sinCoberturas")}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("detail.coberturas.exclusiones")}</CardTitle>
        </CardHeader>
        <CardContent>
          <ItemList
            items={exclusiones}
            tone="exclusion"
            emptyLabel={t("detail.coberturas.sinExclusiones")}
          />
        </CardContent>
      </Card>
    </div>
  );
}
