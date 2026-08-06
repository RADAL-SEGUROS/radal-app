import { LogOut, Moon, Sun, Languages, User as UserIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/providers/AuthProvider";
import { useTheme } from "@/providers/ThemeProvider";
import { SearchDropdown } from "@/components/common/SearchDropdown";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

function initials(name?: string) {
  if (!name) return "?";
  return (
    name
      .split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase())
      .join("") || "?"
  );
}

export function Topbar() {
  const { t, i18n } = useTranslation("common");
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();

  const lang = i18n.language?.startsWith("en") ? "en" : "es";

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const iconBtn =
    "grid h-[38px] w-[38px] place-items-center rounded-[10px] border border-line bg-bg-surface text-text-tertiary transition-colors duration-150 hover:border-[color-mix(in_srgb,var(--teal)_40%,var(--line))] hover:text-teal-deep";

  return (
    <header className="sticky top-0 z-30 flex items-center gap-[18px] border-b border-line bg-[color-mix(in_srgb,var(--paper)_80%,transparent)] px-[26px] py-3 backdrop-blur-[12px] transition-colors duration-300">
      {/* Centered global search */}
      <div className="flex flex-1 justify-center">
        <div className="w-full max-w-[540px]">
          <SearchDropdown />
        </div>
      </div>

      {/* Right controls */}
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={toggleTheme}
          aria-label={t("theme.toggle")}
          title={t("theme.toggle")}
          className={iconBtn}
        >
          {theme === "dark" ? (
            <Sun className="h-[18px] w-[18px]" strokeWidth={1.75} />
          ) : (
            <Moon className="h-[18px] w-[18px]" strokeWidth={1.75} />
          )}
        </button>

        <button
          type="button"
          onClick={() => void i18n.changeLanguage(lang === "es" ? "en" : "es")}
          aria-label={t("language.toggle")}
          title={t("language.toggle")}
          className="inline-flex h-[38px] items-center gap-1.5 rounded-[10px] border border-line bg-bg-surface px-[11px] text-[12.5px] font-semibold text-text-tertiary transition-colors duration-150 hover:border-[color-mix(in_srgb,var(--teal)_40%,var(--line))] hover:text-teal-deep"
        >
          <Languages className="h-4 w-4" strokeWidth={1.75} />
          {lang === "es" ? "ES" : "EN"}
        </button>

        <span className="mx-0.5 h-[22px] w-px bg-line" />

        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label={t("user.menu")}
            className="grid h-[38px] w-[38px] place-items-center rounded-[10px] border border-line bg-gradient-to-br from-teal to-blue font-display text-[13px] font-medium text-white outline-none transition-transform duration-150 hover:-translate-y-px focus-visible:ring-2 focus-visible:ring-ring"
          >
            {initials(user?.full_name)}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="flex flex-col">
              <span className="text-body text-text-primary">
                {user?.full_name}
              </span>
              <span className="text-caption font-normal text-text-muted">
                {user?.job_title ?? user?.role}
              </span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem disabled>
              <UserIcon className="h-4 w-4" />
              {t("user.profile")}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={handleLogout}>
              <LogOut className="h-4 w-4" />
              {t("actions.logout")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
