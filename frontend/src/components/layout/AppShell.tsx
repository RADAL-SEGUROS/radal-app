import { Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

export function AppShell() {
  const location = useLocation();
  return (
    <div className="flex min-h-screen w-full bg-bg-app text-text-primary">
      <Sidebar />
      <main className="flex h-screen min-w-0 flex-1 flex-col overflow-y-auto">
        <Topbar />
        {/* key on pathname so entrance motion replays on route change */}
        <div
          key={location.pathname}
          className="mx-auto flex w-full max-w-[1380px] flex-col gap-[22px] px-7 pb-14 pt-[26px]"
        >
          <Outlet />
        </div>
      </main>
    </div>
  );
}
