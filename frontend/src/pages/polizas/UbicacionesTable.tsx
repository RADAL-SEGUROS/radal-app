import { useTranslation } from "react-i18next";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatUF, formatPct } from "@/lib/format";
import type { UbicacionPoliza } from "./types";

interface Props {
  ubicaciones: UbicacionPoliza[];
}

/** Bloque 3 — Ubicaciones. Dirección, Suma, %. Total top-right. */
export function UbicacionesTable({ ubicaciones }: Props) {
  const { t } = useTranslation("polizas");
  const totalSuma = (ubicaciones ?? []).reduce(
    (acc, u) => acc + (u.suma_asegurada_uf ?? 0),
    0,
  );

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle>{t("detail.ubicaciones.title")}</CardTitle>
          <CardDescription>{t("detail.ubicaciones.subtitle")}</CardDescription>
        </div>
        <div className="text-right">
          <p className="text-label text-text-muted">
            {t("detail.ubicaciones.total")}
          </p>
          <p className="font-mono text-mono text-text-primary">
            {formatUF(totalSuma)}
          </p>
        </div>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        {(ubicaciones ?? []).length === 0 ? (
          <p className="px-5 pb-5 text-body text-text-muted">
            {t("detail.ubicaciones.empty")}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>{t("detail.ubicaciones.nombre")}</TableHead>
                <TableHead>{t("detail.ubicaciones.direccion")}</TableHead>
                <TableHead className="text-right">
                  {t("detail.ubicaciones.suma")}
                </TableHead>
                <TableHead className="text-right">
                  {t("detail.ubicaciones.porcentaje")}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ubicaciones.map((u, i) => (
                <TableRow key={u.id ?? i} className="hover:bg-transparent">
                  <TableCell className="text-body text-text-primary">
                    {u.nombre}
                  </TableCell>
                  <TableCell className="text-body text-text-secondary">
                    {u.direccion || "—"}
                  </TableCell>
                  <TableCell className="text-right font-mono text-mono text-text-primary">
                    {formatUF(u.suma_asegurada_uf)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-mono text-text-secondary">
                    {formatPct(u.porcentaje)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
