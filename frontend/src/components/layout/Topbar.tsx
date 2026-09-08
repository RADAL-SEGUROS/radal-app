import * as React from "react";
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

export interface TopbarProps {
  /** The mobile nav trigger + its sheet, owned by `AppShell`. Rendered first so
      the hamburger sits where a phone user reaches for it, and absent above
      `lg` where the rail is always on screen. */
  navTrigger?: React.ReactNode;
}

export function Topbar({ navTrigger }: TopbarProps = {}) {
  const { t, i18n } = useTranslation("common");
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();

  const lang = i18n.language?.startsWith("en") ? "en" : "es";

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  // Signal icon-button recipe: hairline border does the ring, hover swaps the
  // fill, press = scale(0.98). `transform` is named because Tailwind's
  // `scale-*` utilities apply through the transform property.
  const iconBtn =
    "grid h-[38px] w-[38px] place-items-center rounded-sm border border-line bg-bone text-ink-3 transition-[background-color,border-color,color,transform] duration-150 ease-out hover:border-line-strong hover:bg-paper-2 hover:text-ink active:scale-[0.98]";

  return (
    <header className="sticky top-0 z-30 flex h-[var(--topbar-h)] shrink-0 items-center gap-[18px] border-b border-line bg-paper px-4 py-3 sm:px-[26px]">
      {navTrigger}

      {/* Centered global search */}
      <div className="flex min-w-0 flex-1 justify-center">
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
          className="inline-flex h-[38px] items-center gap-1.5 rounded-sm border border-line bg-bone px-[11px] text-[12px] font-medium text-ink-3 transition-[background-color,border-color,color,transform] duration-150 ease-out hover:border-line-strong hover:bg-paper-2 hover:text-ink active:scale-[0.98]"
        >
          <Languages className="h-4 w-4" strokeWidth={1.75} />
          {lang === "es" ? "ES" : "EN"}
        </button>

        <span className="mx-0.5 h-[22px] w-px bg-line" />

        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label={t("user.menu")}
            className="grid h-[38px] w-[38px] place-items-center rounded-sm bg-brand-soft text-[13px] font-medium text-brand-deep outline-none transition-[background-color,transform] duration-150 ease-out active:scale-[0.98] focus-visible:ring-2 focus-visible:ring-brand-ring"
          >
            {initials(user?.full_name)}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="flex flex-col">
              <span className="text-body text-ink">
                {user?.full_name}
              </span>
              <span className="text-caption font-normal text-ink-3">
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
