import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download } from "lucide-react";
import { api, qs } from "@/api/client";
import { ErrorOr } from "@/components/shared";
import { Badge, Button, Card, Dialog, DialogContent, Select, Spinner, PageHeader, StatusBadge, Table, Td, Th, Toggle } from "@/components/ui";
import { useGroups } from "@/hooks/queries";
import { addWeeks, bandClass, download, fmtWeek, hrs, pct, STATUS_ICON, STATUS_LABELS, thisMonday } from "@/lib/utils";

export default function HeatmapPage() {
  const [level, setLevel] = useState("group");
  const [mode, setMode] = useState("forecast");
  const [group, setGroup] = useState("");
  const [start, setStart] = useState(thisMonday());
  const [cell, setCell] = useState<{ person?: string; group?: string; week: string; name: string } | null>(null);
  const groups = useGroups();
  const params = { level, mode, group: level === "person" ? group : "", frm: start, to: addWeeks(start, 11) };
  const q = useQuery<any>({ queryKey: ["heatmap", params], queryFn: () => api(`/api/heatmap${qs(params)}`) });

  return (
    <div>
      <PageHeader
        title="Capacity heatmap"
        description="Rows × next 12 weeks, coloured by status band. Click a cell for the detail."
        actions={<>
          <Toggle label="Rows" value={level} onChange={setLevel} options={[{ value: "group", label: "Groups" }, { value: "person", label: "People" }]} />
          <Toggle label="Mode" value={mode} onChange={setMode} options={[{ value: "confirmed", label: "Confirmed" }, { value: "forecast", label: "Forecast" }]} />
          {level === "person" && (
            <Select className="w-56" value={group} onChange={(e) => setGroup(e.target.value)} aria-label="Group">
              <option value="">All groups</option>
              {groups.data?.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </Select>
          )}
          <div className="flex items-center">
            <Button variant="outline" size="icon" aria-label="Previous weeks" onClick={() => setStart(addWeeks(start, -4))}><ChevronLeft className="h-4 w-4" /></Button>
            <Button variant="ghost" size="sm" onClick={() => setStart(thisMonday())}>This week</Button>
            <Button variant="outline" size="icon" aria-label="Next weeks" onClick={() => setStart(addWeeks(start, 4))}><ChevronRight className="h-4 w-4" /></Button>
          </div>
          <Button variant="outline" size="sm" onClick={() => download(`/api/export/heatmap.xlsx${qs(params)}`)}><Download className="h-4 w-4" /> Excel</Button>
        </>}
      />
      <Legend bands={q.data?.bands} />
      <Card className="mt-3 overflow-x-auto">
        <ErrorOr q={q}>
          {q.data && (
            <table className="w-full border-separate border-spacing-0.5 p-2 text-xs">
              <thead>
                <tr>
                  <th className="sticky left-0 bg-card px-2 text-left font-medium text-muted-foreground">{level === "group" ? "Group" : "Person"}</th>
                  {q.data.weeks.map((w: string) => <th key={w} className="min-w-[66px] px-1 font-medium text-muted-foreground">{fmtWeek(w)}</th>)}
                </tr>
              </thead>
              <tbody>
                {q.data.rows.map((r: any) => (
                  <tr key={r.id ?? r.name}>
                    <td className="sticky left-0 whitespace-nowrap bg-card px-2 py-0.5 font-medium">
                      {level === "person" ? <Link className="hover:underline" to={`/people/${r.id}`}>{r.name}</Link> : r.name}
                      {level === "person" && <span className="ml-1 text-[10px] text-muted-foreground">{r.tech_level}</span>}
                    </td>
                    {r.cells.map((c: any) => (
                      <td key={c.week_start} className="p-0">
                        <button
                          className={`w-full rounded px-1 py-1 text-center ${bandClass(c.status)}`}
                          onClick={() => setCell(level === "group" ? { group: r.id, week: c.week_start, name: r.name } : { person: r.id, week: c.week_start, name: r.name })}
                          title={`${r.name}, week of ${fmtWeek(c.week_start)}: ${pct(c.util)} ${STATUS_LABELS[c.status]}`}
                        >
                          <div className="font-semibold">{pct(c.util)}</div>
                          <div className="text-[9px] leading-tight"><span aria-hidden>{STATUS_ICON[c.status]} </span>{STATUS_LABELS[c.status]}</div>
                          {c.driver?.driver && <div className="mt-0.5 text-[9px] font-semibold">{c.driver.driver === "project_spike" ? "Spike" : "Structural"}</div>}
                          {c.at_risk && <div className="text-[9px] font-semibold">At risk</div>}
                        </button>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </ErrorOr>
      </Card>
      <CellDialog cell={cell} onClose={() => setCell(null)} />
    </div>
  );
}

export function Legend({ bands }: { bands?: any }) {
  const b = bands ?? { bench: 50, light: 80, healthy: 100, hot: 115 };
  const items = [
    ["bench", `< ${b.bench}%`],
    ["light", `${b.bench}–${b.light}%`],
    ["healthy", `${b.light}–${b.healthy}%`],
    ["hot", `${b.healthy}–${b.hot}%`],
    ["over", `> ${b.hot}%`],
  ];
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {items.map(([s, r]) => <span key={s} className="flex items-center gap-1"><StatusBadge status={s} /> <span className="text-muted-foreground">{r}</span></span>)}
    </div>
  );
}

function CellDialog({ cell, onClose }: { cell: { person?: string; group?: string; week: string; name: string } | null; onClose: () => void }) {
  const q = useQuery<any>({
    queryKey: ["cell", cell],
    queryFn: () => api(`/api/heatmap/cell${qs({ person: cell?.person, group: cell?.group, week: cell?.week })}`),
    enabled: !!cell,
  });
  return (
    <Dialog open={!!cell} onOpenChange={(v) => !v && onClose()}>
      <DialogContent title={cell ? `${cell.name} · week of ${fmtWeek(cell.week)}` : ""} className="max-w-2xl">
        {q.isLoading || !q.data ? <Spinner /> : cell?.person ? (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2 text-sm">
              <span>Capacity <b>{hrs(q.data.week.capacity)}</b></span>
              <span>Confirmed <b>{hrs(q.data.week.confirmed_hours)}</b></span>
              <span>Weighted forecast <b>{hrs(q.data.week.weighted_forecast_hours)}</b> <span className="text-muted-foreground">(raw {hrs(q.data.week.raw_forecast_hours)})</span></span>
              <span>Billable <b>{hrs(q.data.week.billable_hours)}</b></span>
            </div>
            <div className="flex gap-2">
              <span className="text-xs text-muted-foreground">Confirmed</span><StatusBadge status={q.data.week.status_confirmed} util={q.data.week.util_confirmed} />
              <span className="text-xs text-muted-foreground">Forecast</span><StatusBadge status={q.data.week.status_forecast} util={q.data.week.util_forecast} />
              {q.data.week.at_risk && <Badge variant="amber">At risk</Badge>}
            </div>
            <Table><thead><tr><Th>Project</Th><Th>State</Th><Th>Hours</Th><Th>Billable</Th></tr></thead>
              <tbody>{q.data.allocations.map((a: any, i: number) => <tr key={i} className={a.billable ? "" : "text-muted-foreground"}><Td>{a.project_id ? <Link className="text-primary hover:underline" to={`/projects/${a.project_id}`}>{a.project_name}</Link> : a.project_name}</Td><Td><Badge>{a.state}</Badge></Td><Td>{hrs(a.hours)}</Td><Td>{a.billable ? "Yes" : "No"}</Td></tr>)}</tbody></Table>
            <Link className="text-sm text-primary hover:underline" to={`/people/${cell.person}`}>Open person profile</Link>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <StatusBadge status={q.data.rollup.status_forecast} util={q.data.rollup.util_forecast} />
              <span>{q.data.rollup.headcount} people · capacity {hrs(q.data.rollup.capacity)} · load {hrs(q.data.rollup.confirmed_hours + q.data.rollup.weighted_forecast_hours)}</span>
            </div>
            {q.data.driver?.driver && (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
                {q.data.driver.driver === "project_spike" ? (
                  <><b>Project spike.</b> {q.data.driver.top_project_name} accounts for {Math.round(q.data.driver.top_project_share * 100)}% of the {hrs(q.data.driver.overload_hours)} overload. This is one intense project, not a staffing gap.</>
                ) : (
                  <><b>Structural gap.</b> The {hrs(q.data.driver.overload_hours)} overload is spread across projects (top project {Math.round(q.data.driver.top_project_share * 100)}%). This is a hiring signal.</>
                )}
              </div>
            )}
            <Table><thead><tr><Th>Person</Th><Th>Forecast</Th><Th>Confirmed</Th><Th>Free</Th></tr></thead>
              <tbody>{q.data.people.map((p: any) => <tr key={p.person_id}><Td><Link className="text-primary hover:underline" to={`/people/${p.person_id}`}>{p.name}</Link></Td><Td><StatusBadge status={p.status_forecast} util={p.util_forecast} /></Td><Td><StatusBadge status={p.status_confirmed} util={p.util_confirmed} compact /></Td><Td>{hrs(p.free_hours)}</Td></tr>)}</tbody></Table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
