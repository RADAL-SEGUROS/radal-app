/**
 * The export control: XLSX / PDF for a table, PNG for a chart.
 *
 * It always exports **the view the broker is looking at** — the same filters,
 * not the whole tenant — so what lands in the file matches what is on screen.
 * The server re-runs those filters over every matching row, because a 50-row
 * page is not an export.
 *
 * Failures are shown, never swallowed: the server answers 422 above its row
 * cap, and that becomes a Spanish toast rather than a button that appears to do
 * nothing (rule 3 — no dead buttons).
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Download, FileSpreadsheet, FileText, Image as ImageIcon, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DisabledHint } from "@/components/common/kit";
import {
  exportChartPng,
  exportTable,
  type ExportEntity,
  type ExportFormat,
} from "@/lib/exports";

function serverMessage(error: unknown, fallback: string): string {
  const response = (error as { response?: { data?: unknown } })?.response;
  const detail = (response?.data as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return fallback;
}

export function ExportMenu({
  entity,
  filters,
  groupBy,
  filenameStem,
  /** When set, a "PNG" item captures this element's chart. */
  chartRef,
  disabledHint,
  size = "sm",
}: {
  entity: ExportEntity;
  filters: Record<string, unknown>;
  groupBy?: string | null;
  filenameStem?: string;
  chartRef?: React.RefObject<HTMLElement | null>;
  /** Non-null renders the trigger disabled with this reason. */
  disabledHint?: string | null;
  size?: "sm" | "default";
}) {
  const { t } = useTranslation("analytics");
  const [busy, setBusy] = React.useState<ExportFormat | "png" | null>(null);

  const run = async (format: ExportFormat) => {
    setBusy(format);
    try {
      await exportTable(entity, { format, filters, group_by: groupBy ?? null }, filenameStem);
      toast.success(t("export.done"));
    } catch (error) {
      toast.error(serverMessage(error, t("export.failed")));
    } finally {
      setBusy(null);
    }
  };

  const runPng = async () => {
    setBusy("png");
    try {
      const ok = await exportChartPng(
        chartRef?.current ?? null,
        `${filenameStem || entity}-${new Date().toISOString().slice(0, 10)}`,
      );
      if (ok) toast.success(t("export.done"));
      else toast.error(t("export.noChart"));
    } catch {
      toast.error(t("export.failed"));
    } finally {
      setBusy(null);
    }
  };

  const trigger = (
    <Button variant="secondary" size={size} disabled={Boolean(disabledHint) || busy !== null}>
      {busy ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (
        <Download className="h-4 w-4" />
      )}
      {t("export.action")}
    </Button>
  );

  if (disabledHint) {
    return <DisabledHint hint={disabledHint}>{trigger}</DisabledHint>;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[190px]">
        <DropdownMenuItem onSelect={() => void run("xlsx")}>
          <FileSpreadsheet className="h-3.5 w-3.5 text-ink-3" />
          <span className="flex-1">{t("export.xlsx")}</span>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void run("pdf")}>
          <FileText className="h-3.5 w-3.5 text-ink-3" />
          <span className="flex-1">{t("export.pdf")}</span>
        </DropdownMenuItem>
        {chartRef ? (
          <DropdownMenuItem onSelect={() => void runPng()}>
            <ImageIcon className="h-3.5 w-3.5 text-ink-3" />
            <span className="flex-1">{t("export.png")}</span>
          </DropdownMenuItem>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** PNG-only variant for a single chart card's overflow action. */
export function ChartPngButton({
  chartRef,
  filename,
  label,
}: {
  chartRef: React.RefObject<HTMLElement | null>;
  filename: string;
  label?: string;
}) {
  const { t } = useTranslation("analytics");
  const [busy, setBusy] = React.useState(false);

  return (
    <Button
      variant="ghost"
      size="sm"
      disabled={busy}
      title={label ?? t("export.png")}
      onClick={async () => {
        setBusy(true);
        try {
          const ok = await exportChartPng(chartRef.current, filename);
          if (!ok) toast.error(t("export.noChart"));
        } catch {
          toast.error(t("export.failed"));
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <ImageIcon className="h-3.5 w-3.5" />
      )}
    </Button>
  );
}
