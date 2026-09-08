import { Download, FileSpreadsheet, FileText, FileType } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export type ExportFormat = "word" | "excel" | "pdf";

interface ExportButtonProps {
  /** Optional real handler; when absent, shows a stub toast. */
  onExport?: (format: ExportFormat) => void;
  disabled?: boolean;
}

/**
 * Stub export menu (Word / Excel / PDF). Module agents wire `onExport`
 * to real generators later.
 */
export function ExportButton({ onExport, disabled }: ExportButtonProps) {
  const { t } = useTranslation("common");

  const handle = (format: ExportFormat) => {
    if (onExport) {
      onExport(format);
    } else {
      toast(t("state.underConstruction"), {
        description: t(`export.${format}`),
      });
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" size="sm" disabled={disabled}>
          <Download className="h-4 w-4" />
          {t("export.label")}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => handle("word")}>
          <FileText className="h-4 w-4 text-ink-3" />
          {t("export.word")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handle("excel")}>
          <FileSpreadsheet className="h-4 w-4 text-ink-3" />
          {t("export.excel")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handle("pdf")}>
          <FileType className="h-4 w-4 text-ink-3" />
          {t("export.pdf")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
