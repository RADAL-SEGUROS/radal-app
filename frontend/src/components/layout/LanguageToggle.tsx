import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function LanguageToggle() {
  const { t, i18n } = useTranslation("common");
  const current = i18n.language?.startsWith("en") ? "en" : "es";

  const change = (lng: "es" | "en") => {
    void i18n.changeLanguage(lng);
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("language.toggle")}
          title={t("language.toggle")}
        >
          <Languages className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem
          onClick={() => change("es")}
          className={current === "es" ? "font-semibold text-brand-deep" : ""}
        >
          {t("language.es")}
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={() => change("en")}
          className={current === "en" ? "font-semibold text-brand-deep" : ""}
        >
          {t("language.en")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
