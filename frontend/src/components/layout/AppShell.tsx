import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { DevViewSwitcher } from "./DevViewSwitcher";

export function AppShell() {
  return (
    <div className="flex h-screen w-full overflow-hidden bg-bg-app">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
      <DevViewSwitcher />
    </div>
  );
}
