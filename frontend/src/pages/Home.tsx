import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Download } from "lucide-react";
import { api } from "@/api/client";
import { AlertFeed, ErrorOr } from "@/components/shared";
import { Badge, Button, Card, CardBody, CardHeader, ConfidenceBadge, Empty, PageHeader, Stat, StatusBadge, Table, Tabs, TabsContent, TabsList, TabsTrigger, Td, Th } from "@/components/ui";
import { hasRole, useMe } from "@/hooks/queries";
import { bandClass, download, fmtWeek, pct, STATUS_LABELS } from "@/lib/utils";

export default function HomePage() {
  const me = useMe().data!;
  const tabs = [
    hasRole(me, "gl", "admin", "sl") && { v: "gl", label: "Group Leader" },
    hasRole(me, "pl", "admin", "sl") && { v: "pl", label: "Project Leader" },
    hasRole(me, "sl", "admin", "gl", "pl") && { v: "lead", label: "Leadership" },
  ].filter(Boolean) as { v: string; label: string }[];
  const def = me.roles.includes("gl") ? "gl" : me.roles.includes("pl") ? "pl" : me.roles.includes("sl") ? "lead" : tabs[0]?.v;
  return (
    <div>
      <PageHeader title={`Hello, ${me.name.split(" ")[0]}`} description="Your dashboard and alert feed" />
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="min-w-0">
          {tabs.length ? (
            <Tabs defaultValue={def}>
              <TabsList>{tabs.map((t) => <TabsTrigger key={t.v} value={t.v}>{t.label}</TabsTrigger>)}</TabsList>
              {tabs.some((t) => t.v === "gl") && <TabsContent value="gl"><GlDashboard /></TabsContent>}
              {tabs.some((t) => t.v === "pl") && <TabsContent value="pl"><PlDashboard /></TabsContent>}
              {tabs.some((t) => t.v === "lead") && <TabsContent value="lead"><LeadershipDashboard /></TabsContent>}
            </Tabs>
          ) : <MemberHome />}
        </div>
        <div><AlertFeed /></div>
      </div>
    </div>
  );
}

function MemberHome() {
  const me = useMe().data!;
  return <Card><CardBody>{me.person_id ? <Link className="text-primary underline" to={`/people/${me.person_id}`}>See your allocations and upcoming work</Link> : "No person record is linked to your account yet."}</CardBody></Card>;
}

function DriverText({ d }: { d: any }) {
  if (!d?.driver) return null;
  return d.driver === "project_spike" ? (
    <Badge variant="amber" title={`${Math.round(d.top_project_share * 100)}% of the overload`}>Project spike: {d.top_project_name}</Badge>
  ) : (
    <Badge variant="red" title="Hot across many projects">Structural gap (hiring signal)</Badge>
  );
}

function GlDashboard() {
  const q = useQuery<any>({ queryKey: ["dash", "gl"], queryFn: () => api("/api/dashboard/gl") });
  const d = q.data;
  return (
    <ErrorOr q={q}>
      {d && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="text-sm text-muted-foreground">{d.groups.map((g: any) => g.name).join(", ")} · {d.headcount} people</div>
            <Button size="sm" variant="outline" onClick={() => download("/api/export/gl.xlsx")}><Download className="h-4 w-4" /> Excel</Button>
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {["bench", "light", "healthy", "hot", "over"].map((s) => (
              <Card key={s} className={`px-4 py-3 ${bandClass(s)}`}>
                <div className="text-xs font-medium">{STATUS_LABELS[s]}</div>
                <div className="text-2xl font-semibold">{d.bands_this_week[s] ?? 0}</div>
                <div className="text-[11px] opacity-80">people this week</div>
              </Card>
            ))}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Stat label="Pending requests for my people" value={d.pending_requests} sub={<Link className="text-primary" to="/requests">Open the inbox</Link>} />
            <Stat label="Open import conflicts" value={d.open_conflicts} sub="OpenAir changes vs in-app edits" />
          </div>
          {d.trend.map((g: any) => (
            <Card key={g.group_id}>
              <CardHeader title={`${g.group_name}: forecast utilization, next 12 weeks`} description="Hot weeks show what drives them (project spike vs structural gap)" />
              <CardBody>
                <div className="h-48">
                  <ResponsiveContainer>
                    <LineChart data={g.weeks.map((w: any) => ({ week: fmtWeek(w.week_start), Forecast: w.util_forecast, Confirmed: w.util_confirmed }))}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="week" fontSize={11} />
                      <YAxis unit="%" fontSize={11} domain={[0, "auto"]} />
                      <Tooltip />
                      <Legend />
                      <ReferenceLine y={100} stroke="#b45309" strokeDasharray="4 4" />
                      <Line dataKey="Forecast" stroke="#1d4ed8" strokeWidth={2} />
                      <Line dataKey="Confirmed" stroke="#16a34a" strokeDasharray="5 3" />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {g.weeks.filter((w: any) => w.driver?.driver).map((w: any) => (
                    <span key={w.week_start} className="flex items-center gap-1 text-xs"><StatusBadge status={w.status_forecast} util={w.util_forecast} compact /> {fmtWeek(w.week_start)} <DriverText d={w.driver} /></span>
                  ))}
                </div>
              </CardBody>
            </Card>
          ))}
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Running hot" description="Peak in the next 4 weeks" />
              {d.hot.length ? (
                <Table><thead><tr><Th>Person</Th><Th>Peak</Th><Th>Week</Th><Th>Top project</Th></tr></thead>
                  <tbody>{d.hot.map((p: any) => <tr key={p.person_id}><Td><Link className="text-primary hover:underline" to={`/people/${p.person_id}`}>{p.name}</Link> <span className="text-xs text-muted-foreground">{p.tech_level}</span></Td><Td><StatusBadge status={p.status} util={p.peak_util} /></Td><Td>{fmtWeek(p.peak_week)}</Td><Td className="text-xs">{p.top_project}</Td></tr>)}</tbody></Table>
              ) : <Empty>Nobody is hot.</Empty>}
            </Card>
            <Card>
              <CardHeader title="Light or on the bench" description="Average of the next 4 weeks" />
              {d.light.length ? (
                <Table><thead><tr><Th>Person</Th><Th>Avg</Th><Th>Free (4 wk)</Th><Th /></tr></thead>
                  <tbody>{d.light.map((p: any) => <tr key={p.person_id}><Td><Link className="text-primary hover:underline" to={`/people/${p.person_id}`}>{p.name}</Link> <span className="text-xs text-muted-foreground">{p.tech_level} · {p.role_family}</span></Td><Td><StatusBadge status={p.status} util={p.avg_util} /></Td><Td>{p.free_hours_4w} h</Td><Td><Link className="text-xs text-primary" to={`/people/${p.person_id}`}>Mark available</Link></Td></tr>)}</tbody></Table>
              ) : <Empty>Nobody is light.</Empty>}
            </Card>
            <Card>
              <CardHeader title="Upcoming roll-offs" description="Next 4 weeks" />
              {d.rolloffs.length ? (
                <Table><thead><tr><Th>Person</Th><Th>Project</Th><Th>Last week</Th><Th>Next work</Th></tr></thead>
                  <tbody>{d.rolloffs.map((r: any, i: number) => <tr key={i}><Td><Link className="text-primary hover:underline" to={`/people/${r.person_id}`}>{r.name}</Link></Td><Td className="text-xs">{r.project}</Td><Td>{fmtWeek(r.last_week)}</Td><Td>{r.has_next_work ? <Badge variant="green">Has next work</Badge> : <Badge variant="red">None yet</Badge>}</Td></tr>)}</tbody></Table>
              ) : <Empty>No roll-offs in the next 4 weeks.</Empty>}
            </Card>
            <Card>
              <CardHeader title="At risk" description="Healthy on Forecast but Light on Confirmed" />
              {d.at_risk.length ? (
                <Table><thead><tr><Th>Person</Th><Th>Week</Th><Th>Confirmed</Th><Th>Forecast</Th></tr></thead>
                  <tbody>{d.at_risk.map((r: any) => <tr key={r.person_id}><Td><Link className="text-primary hover:underline" to={`/people/${r.person_id}`}>{r.name}</Link></Td><Td>{fmtWeek(r.week)}</Td><Td>{pct(r.util_confirmed)}</Td><Td>{pct(r.util_forecast)}</Td></tr>)}</tbody></Table>
              ) : <Empty>No at-risk people.</Empty>}
            </Card>
          </div>
        </div>
      )}
    </ErrorOr>
  );
}

function PlDashboard() {
  const q = useQuery<any>({ queryKey: ["dash", "pl"], queryFn: () => api("/api/dashboard/pl") });
  const d = q.data;
  return (
    <ErrorOr q={q}>
      {d && (
        <div className="space-y-4">
          <div className="flex justify-end"><Button size="sm" variant="outline" onClick={() => download("/api/export/pl.xlsx")}><Download className="h-4 w-4" /> Excel</Button></div>
          <div className="grid gap-3 md:grid-cols-3">
            <Stat label="My projects" value={d.projects.length} />
            <Stat label="At-risk demand lines" value={d.at_risk_lines.length} sub="Low confidence or unconfirmed" />
            <Stat label="Pending requests" value={d.pending_requests} sub={<Link className="text-primary" to="/requests">Open the inbox</Link>} />
          </div>
          <Card>
            <CardHeader title="Unfilled demand by week" description="Required hours not yet covered by confirmed allocations (raw and confidence-weighted)" />
            <CardBody>
              <div className="h-52">
                <ResponsiveContainer>
                  <BarChart data={d.unfilled_by_week.map((w: any) => ({ week: fmtWeek(w.week_start), Confirmed: w.confirmed, Unfilled: w.unfilled, "Weighted unfilled": w.weighted_unfilled }))}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="week" fontSize={11} />
                    <YAxis unit="h" fontSize={11} />
                    <Tooltip />
                    <Legend />
                    <Bar dataKey="Confirmed" stackId="a" fill="#16a34a" />
                    <Bar dataKey="Unfilled" stackId="a" fill="#f59e0b" />
                    <Bar dataKey="Weighted unfilled" fill="#93c5fd" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="My projects" />
            <Table><thead><tr><Th>Project</Th><Th>Client</Th><Th>Status</Th><Th>Lines</Th><Th>At risk</Th><Th>Unfilled h</Th></tr></thead>
              <tbody>{d.projects.map((p: any) => <tr key={p.id}><Td><Link className="text-primary hover:underline" to={`/projects/${p.id}`}>{p.name}</Link></Td><Td>{p.client}</Td><Td><Badge variant={p.status === "active" ? "green" : p.status === "pipeline" ? "blue" : "grey"}>{p.status}</Badge></Td><Td>{p.lines}</Td><Td>{p.at_risk}</Td><Td>{p.unfilled_hours}</Td></tr>)}</tbody></Table>
          </Card>
          <Card>
            <CardHeader title="At-risk demand lines" />
            {d.at_risk_lines.length ? (
              <Table><thead><tr><Th>Project</Th><Th>Need</Th><Th>Weeks</Th><Th>Confidence</Th><Th>Fill</Th><Th>Why</Th></tr></thead>
                <tbody>{d.at_risk_lines.map((l: any) => <tr key={l.demand_line_id}><Td><Link className="text-primary hover:underline" to={`/projects/${l.project_id}`}>{l.project_name}</Link></Td><Td className="text-xs">{l.role_family} {l.min_tech_level}+ · {l.hours_per_week} h/wk {l.skills?.length ? `· ${l.skills.join(", ")}` : ""}</Td><Td className="text-xs">{fmtWeek(l.start_week)}–{fmtWeek(l.end_week)}</Td><Td><ConfidenceBadge value={l.confidence} /></Td><Td><Badge>{l.status}</Badge></Td><Td className="text-xs">{l.why}</Td></tr>)}</tbody></Table>
            ) : <Empty>No at-risk lines.</Empty>}
          </Card>
        </div>
      )}
    </ErrorOr>
  );
}

function LeadershipDashboard() {
  const q = useQuery<any>({ queryKey: ["dash", "lead"], queryFn: () => api("/api/dashboard/leadership") });
  const d = q.data;
  return (
    <ErrorOr q={q}>
      {d && (
        <div className="space-y-4">
          <div className="flex justify-end"><Button size="sm" variant="outline" onClick={() => download("/api/export/leadership.xlsx")}><Download className="h-4 w-4" /> Excel</Button></div>
          <div className="grid gap-3 md:grid-cols-3">
            <Stat label="Company billable utilization" value={pct(d.company_billable_util)} sub="Next 12 weeks, confirmed billable hours / capacity" />
            <Stat label="People" value={d.headcount} />
            <Stat label="Groups" value={d.groups.length} />
          </div>
          <Card>
            <CardHeader title="Company heatmap: group × week" description="Forecast utilization (confirmed + weighted pipeline)" />
            <div className="overflow-x-auto p-3">
              <table className="text-xs">
                <thead><tr><th className="px-2 text-left font-medium text-muted-foreground">Group</th>{d.weeks.map((w: string) => <th key={w} className="px-1 font-medium text-muted-foreground">{fmtWeek(w)}</th>)}</tr></thead>
                <tbody>{d.groups.map((g: any) => <tr key={g.group_id}><td className="whitespace-nowrap px-2 py-1 font-medium">{g.group_name}</td>{g.weeks.map((w: any) => <td key={w.week_start} className="p-0.5"><div className={`rounded px-1.5 py-1 text-center ${bandClass(w.status_forecast)}`} title={`${STATUS_LABELS[w.status_forecast]}${w.driver?.driver ? ` · ${w.driver.driver === "project_spike" ? "project spike: " + w.driver.top_project_name : "structural gap"}` : ""}`}>{pct(w.util_forecast)}<div className="text-[9px]">{STATUS_LABELS[w.status_forecast]}</div></div></td>)}</tr>)}</tbody>
              </table>
            </div>
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Billable utilization by group" />
              <CardBody><div className="h-56"><ResponsiveContainer>
                <BarChart data={d.billable_by_group.map((g: any) => ({ group: g.group_name.replace(/^Group /, ""), Billable: g.billable_util, Confirmed: g.confirmed_util }))}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="group" fontSize={10} interval={0} /><YAxis unit="%" fontSize={11} /><Tooltip /><Legend />
                  <Bar dataKey="Confirmed" fill="#94a3b8" /><Bar dataKey="Billable" fill="#1d4ed8" />
                </BarChart></ResponsiveContainer></div></CardBody>
            </Card>
            <Card>
              <CardHeader title="Supply vs weighted demand by Tech level" description="12 weeks, hours. Demand = confirmed + weighted pipeline + weighted open demand" />
              <CardBody><div className="h-56"><ResponsiveContainer>
                <BarChart data={d.supply_demand.map((r: any) => ({ level: r.tech_level, Supply: r.supply_hours, "Weighted demand": r.weighted_demand_hours }))}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="level" fontSize={11} /><YAxis fontSize={11} /><Tooltip /><Legend />
                  <Bar dataKey="Supply" fill="#16a34a" /><Bar dataKey="Weighted demand" fill="#f59e0b" />
                </BarChart></ResponsiveContainer></div>
                <Table><thead><tr><Th>Level</Th><Th>Supply h</Th><Th>Weighted demand h</Th><Th>Gap h</Th></tr></thead>
                  <tbody>{d.supply_demand.map((r: any) => <tr key={r.tech_level}><Td>{r.tech_level}</Td><Td>{r.supply_hours}</Td><Td>{r.weighted_demand_hours}</Td><Td className={r.gap_hours < 0 ? "font-semibold text-red-700" : ""}>{r.gap_hours}</Td></tr>)}</tbody></Table>
              </CardBody>
            </Card>
          </div>
        </div>
      )}
    </ErrorOr>
  );
}
