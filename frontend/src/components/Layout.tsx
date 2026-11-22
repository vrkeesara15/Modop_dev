import { NavLink, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, CalendarRange, FolderKanban, Grid3x3, Home, Inbox, LayoutGrid, LogOut, Settings, Upload, Users, UserSearch } from "lucide-react";
import type { Me } from "@/api/types";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";

const ROLE_LABEL: Record<string, string> = { admin: "Admin", sl: "Senior Leader", gl: "Group Leader", pl: "Project Leader", member: "Team member" };

export default function Layout({ me, children }: { me: Me; children: React.ReactNode }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const r = (...x: string[]) => me.roles.some((y) => x.includes(y));
  const alerts = useQuery<{ unread: number }>({ queryKey: ["alerts"], queryFn: () => api("/api/alerts"), refetchInterval: 60_000 });
  const inbox = useQuery<any[]>({ queryKey: ["requests", "inbox"], queryFn: () => api("/api/requests?box=inbox"), enabled: r("gl", "pl", "admin"), refetchInterval: 60_000 });

  const items = [
    { to: "/", label: "Home", icon: Home, show: true },
    { to: "/heatmap", label: "Capacity heatmap", icon: Grid3x3, show: r("gl", "sl", "admin", "pl") },
    { to: "/grid", label: "Allocation grid", icon: LayoutGrid, show: r("gl", "pl", "admin", "sl") },
    { to: "/availability", label: "Availability board", icon: UserSearch, show: r("pl", "gl", "sl", "admin") },
    { to: "/projects", label: "Projects & demand", icon: FolderKanban, show: r("pl", "gl", "sl", "admin") },
    { to: "/people", label: "People", icon: Users, show: true },
    { to: "/requests", label: "Requests inbox", icon: Inbox, show: r("gl", "pl", "admin"), count: inbox.data?.length },
    { to: "/imports", label: "Imports", icon: Upload, show: r("admin") },
    { to: "/admin", label: "Admin settings", icon: Settings, show: r("admin", "sl") },
  ];

  async function logout() {
    await api("/api/auth/logout", { method: "POST" });
    qc.clear();
    nav("/login");
    window.location.reload();
  }

  return (
    <div className="flex h-full">
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-card">
        <div className="flex items-center gap-2 px-4 py-4">
          <CalendarRange className="h-6 w-6 text-primary" aria-hidden />
          <div>
            <div className="text-sm font-semibold leading-tight">Resource Allocation Hub</div>
            <div className="text-[11px] text-muted-foreground">Forecast · allocate · staff</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-2" aria-label="Main">
          {items
            .filter((i) => i.show)
            .map((i) => (
              <NavLink key={i.to} to={i.to} end={i.to === "/"} className={({ isActive }) => cn("flex items-center gap-2.5 rounded-md px-3 py-2 text-sm", isActive ? "bg-primary/10 font-medium text-primary" : "text-foreground/80 hover:bg-muted")}>
                <i.icon className="h-4 w-4" aria-hidden />
                <span className="flex-1">{i.label}</span>
                {!!i.count && <span className="rounded-full bg-primary px-1.5 text-[11px] font-semibold text-white">{i.count}</span>}
              </NavLink>
            ))}
        </nav>
        <div className="border-t border-border p-3 text-xs">
          <div className="font-medium">{me.name}</div>
          <div className="text-muted-foreground">{me.email}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {me.roles.map((x) => (
              <span key={x} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">{ROLE_LABEL[x] ?? x}</span>
            ))}
          </div>
          <button onClick={logout} className="mt-2 inline-flex items-center gap-1 text-muted-foreground hover:text-foreground">
            <LogOut className="h-3.5 w-3.5" /> Sign out
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 items-center justify-end gap-3 border-b border-border bg-card px-5">
          <NavLink to="/#alerts" className="relative rounded p-1.5 hover:bg-muted" aria-label={`Alerts (${alerts.data?.unread ?? 0} unread)`}>
            <Bell className="h-5 w-5" />
            {!!alerts.data?.unread && <span className="absolute -right-1 -top-1 rounded-full bg-red-600 px-1.5 text-[10px] font-semibold text-white">{alerts.data.unread}</span>}
          </NavLink>
        </header>
        <main className="min-w-0 flex-1 overflow-auto p-5">{children}</main>
      </div>
    </div>
  );
}
