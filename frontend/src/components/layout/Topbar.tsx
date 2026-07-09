import { LogOut, User as UserIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/providers/AuthProvider";
import { SearchDropdown } from "@/components/common/SearchDropdown";
import { ThemeToggle } from "./ThemeToggle";
import { LanguageToggle } from "./LanguageToggle";
import {
  Avatar,
  AvatarFallback,
  AvatarImage,
} from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { greetingPeriod, weekday } from "@/lib/format";

function initials(name?: string) {
  if (!name) return "?";
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
}

export function Topbar() {
  const { t } = useTranslation("common");
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const period = greetingPeriod();
  const dia = weekday();
  const firstName = user?.nombre?.split(" ")[0] ?? "";

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <header className="flex h-16 shrink-0 items-center gap-4 border-b border-line bg-bg-surface px-4">
      {/* Greeting */}
      <div className="hidden min-w-0 flex-col leading-tight md:flex">
        <span className="truncate font-display text-h3 text-text-primary">
          {t(`greeting.${period}`)}
          {firstName ? `, ${firstName}` : ""}
        </span>
        <span className="truncate text-caption capitalize text-text-muted">
          {dia}
        </span>
      </div>

      {/* Global search */}
      <div className="mx-auto w-full max-w-xl">
        <SearchDropdown />
      </div>

      {/* Right controls */}
      <div className="flex shrink-0 items-center gap-1">
        <ThemeToggle />
        <LanguageToggle />
        <DropdownMenu>
          <DropdownMenuTrigger
            className="ml-1 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={t("user.menu")}
          >
            <Avatar>
              <AvatarImage src={undefined} alt={user?.nombre ?? ""} />
              <AvatarFallback>{initials(user?.nombre)}</AvatarFallback>
            </Avatar>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="flex flex-col">
              <span className="text-body text-text-primary">
                {user?.nombre}
              </span>
              <span className="text-caption font-normal text-text-muted">
                {user?.cargo}
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
