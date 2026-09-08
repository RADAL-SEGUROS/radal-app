import * as React from "react";
import { Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Menu } from "lucide-react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

/**
 * The application shell (spec v4 §2).
 *
 * Columns: `Sidebar` | (`Topbar` + `<Outlet/>`). The shell deliberately puts
 * NO width constraint and NO remount key on the outlet — standard pages get
 * the reading column and the motion key back from the `StandardPage` layout
 * route; the group family gets its frame from `GroupShell`. The `Sidebar` is
 * the single, mode-aware rail: it swaps to the group tree inside
 * `/groups/:id/**` on its own (the no-remount guarantee is documented there),
 * so this shell never keys it either.
 *
 * Below `lg` the rail becomes a sheet behind one button in the topbar — and
 * because the `Sidebar` is mode-aware, the sheet shows the group tree when the
 * user is inside a group: one sheet, one trigger, one implementation
 * (`GroupShell` no longer owns a second one). The sheet closes on ANY
 * navigation — `location` as the effect dependency, not just `pathname`,
 * because tree links inside a group often change only the query string
 * (`?tab=quotes`) and must still close it.
 */
export function AppShell() {
  const { t } = useTranslation("common");
  const [navOpen, setNavOpen] = React.useState(false);
  const location = useLocation();

  // Close on navigation. Delegation instead of an `onNavigate` threaded
  // through every NavLink: the rail has leaf links, group links, tree links
  // and the "más" section, and one of them would eventually be added without
  // the prop.
  React.useEffect(() => {
    setNavOpen(false);
  }, [location]);

  return (
    <div className="flex min-h-screen w-full bg-bg-app text-text-primary">
      <Sidebar className="sticky top-0 hidden h-screen self-start lg:flex" />

      <main className="flex h-screen min-w-0 flex-1 flex-col overflow-y-auto">
        <Topbar
          navTrigger={
            <Sheet open={navOpen} onOpenChange={setNavOpen}>
              <SheetTrigger asChild>
                <button
                  type="button"
                  aria-label={t("nav.sections.management")}
                  className="grid h-[38px] w-[38px] shrink-0 place-items-center rounded-sm border border-line bg-bone text-ink-3 transition-[background-color,border-color,color,transform] duration-150 ease-out hover:border-line-strong hover:bg-paper-2 hover:text-ink active:scale-[0.98] lg:hidden"
                >
                  <Menu className="h-[18px] w-[18px]" strokeWidth={1.5} />
                </button>
              </SheetTrigger>
              <SheetContent side="left" className="w-[286px] p-0 sm:max-w-[286px]">
                <SheetHeader className="sr-only">
                  <SheetTitle>{t("nav.sections.management")}</SheetTitle>
                </SheetHeader>
                <Sidebar variant="sheet" className="h-full border-r-0" />
              </SheetContent>
            </Sheet>
          }
        />
        <Outlet />
      </main>
    </div>
  );
}
