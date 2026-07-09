import * as React from "react";
import { Eye } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

export type DevView = "radal" | "aseguradora" | "asegurado";

const VIEWS: DevView[] = ["radal", "aseguradora", "asegurado"];

/**
 * DEV-ONLY floating perspective switcher (Radal / Aseguradora / Asegurado).
 * Renders nothing outside `import.meta.env.DEV`. Only the corredora (Radal)
 * slice is implemented this pass; the others are placeholders for future
 * profile screens.
 */
export function DevViewSwitcher() {
  const { t } = useTranslation("common");
  const [view, setView] = React.useState<DevView>("radal");

  if (!import.meta.env.DEV) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[60] flex items-center gap-1 rounded-full border border-line bg-bg-surface p-1 shadow-lg">
      <span className="px-2 text-text-muted">
        <Eye className="h-4 w-4" />
      </span>
      {VIEWS.map((v) => (
        <button
          key={v}
          type="button"
          onClick={() => setView(v)}
          className={cn(
            "rounded-full px-3 py-1 text-caption font-medium transition-colors",
            view === v
              ? "bg-teal text-white"
              : "text-text-secondary hover:bg-bg-recessed",
          )}
        >
          {t(`devView.${v}`)}
        </button>
      ))}
    </div>
  );
}
