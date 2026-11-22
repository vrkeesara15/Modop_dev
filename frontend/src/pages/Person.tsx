import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bar, CartesianGrid, Legend, Line, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis, ReferenceLine } from "recharts";
import { Flag, Pencil, Plus, Send, Trash2, X } from "lucide-react";
import { api } from "@/api/client";
import { ChangeTable, ErrorOr, RequestDialog } from "@/components/shared";
import { Badge, Button, Card, CardBody, CardHeader, Dialog, DialogContent, Empty, ErrorBox, Field, Input, PageHeader, Select, Stat, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useGroups, useMe, useSettings, useTechLevels } from "@/hooks/queries";
import { bandClass, fmtDate, fmtWeek, hrs, money, pct, STATUS_LABELS, thisMonday } from "@/lib/utils";

const COLORS = ["#1d4ed8", "#16a34a", "#f59e0b", "#9333ea", "#0891b2", "#dc2626", "#65a30d", "#db2777"];

export default function PersonPage() {
  const { id } = useParams();
  const me = useMe().data!;
  const qc = useQueryClient();
  const person = useQuery<any>({ queryKey: ["person", id], queryFn: () => api(`/api/people/${id}`) });
  const tl = useQuery<any>({ queryKey: ["timeline", id], queryFn: () => api(`/api/people/${id}/timeline`) });
  const hist = useQuery<any[]>({ queryKey: ["person-history", id], queryFn: () => api(`/api/people/${id}/history`) });
  const [dlg, setDlg] = useState<"skills" | "flag" | "pto" | "profile" | "request" | "offer" | null>(null);
  const refresh = () => qc.invalidateQueries({ predicate: (q) => ["person", "timeline", "person-history", "alerts"].includes(String(q.queryKey[0])) });
  const delFlag = useMutation({ mutationFn: (fid: string) => api(`/api/people/${id}/availability/${fid}`, { method: "DELETE" }), onSuccess: refresh });
  const delPto = useMutation({ mutationFn: (eid: string) => api(`/api/people/${id}/capacity-exceptions/${eid}`, { method: "DELETE" }), onSuccess: refresh });
  const p = person.data;
  const canEdit = p?.can_edit;
  const projects: any[] = tl.data?.projects ?? [];
  const chart = (tl.data?.weeks ?? []).map((w: any) => {
    const row: any = { week: fmtWeek(w.week_start), Capacity: w.capacity, _cur: w.week_start === tl.data.current_week };
    projects.forEach((pr) => (row[pr.project.name + (!["confirmed", "other"].includes(pr.state) ? ` (${pr.state})` : "")] = pr.weeks[w.week_start] ?? 0));
    return row;
  });
  return (
    <ErrorOr q={person}>
      {p && (
        <div className="space-y-4">
          <PageHeader
            title={p.name}
            description={<span>{p.tech_level} · {p.role_family} · {p.group_name} (GL {p.gl_name ?? "–"}) · {p.location ?? "–"} · {p.std_hours_week} h/week {p.bill_rate !== undefined && <span className="ml-1">· {money(p.bill_rate)}</span>} {p.openair_user_id && <span className="ml-1 text-xs">OpenAir #{p.openair_user_id}</span>}</span>}
            actions={<>
              {canEdit && <Button variant="outline" size="sm" onClick={() => setDlg("profile")}><Pencil className="h-4 w-4" /> Edit profile</Button>}
              {canEdit && <Button variant="outline" size="sm" onClick={() => setDlg("flag")}><Flag className="h-4 w-4" /> Mark available</Button>}
              {canEdit && <Button variant="outline" size="sm" onClick={() => setDlg("pto")}><Plus className="h-4 w-4" /> PTO / holiday</Button>}
              {canEdit && <Button variant="outline" size="sm" onClick={() => setDlg("offer")}>Offer to a project</Button>}
              {me.roles.some((r) => ["pl", "admin"].includes(r)) && <Button size="sm" onClick={() => setDlg("request")}><Send className="h-4 w-4" /> Request</Button>}
              <Link to={`/grid?person=${p.id}`}><Button variant="outline" size="sm">Open in grid</Button></Link>
            </>}
          />
          <ErrorOr q={tl}>
            {tl.data && (
              <>
                <div className="grid gap-3 md:grid-cols-4">
                  <Stat label="This week" value={<StatusBadge status={tl.data.weeks.find((w: any) => w.week_start === tl.data.current_week)?.status_forecast} util={tl.data.weeks.find((w: any) => w.week_start === tl.data.current_week)?.util_forecast} />} sub="Forecast (confirmed + weighted pipeline)" />
                  <Stat label="Confirmed this week" value={pct(tl.data.weeks.find((w: any) => w.week_start === tl.data.current_week)?.util_confirmed)} />
                  <Stat label="Billable hours (16 wk)" value={hrs(tl.data.billable_split.billable_hours)} />
                  <Stat label="Non-billable hours (16 wk)" value={hrs(tl.data.billable_split.non_billable_hours)} sub="Internal, pre-sales, training" />
                </div>
                <Card>
                  <CardHeader title="Weekly allocation timeline" description="4 weeks back, 12 forward. Bars are hours per project; the line is capacity." />
                  <CardBody>
                    <div className="h-64">
                      <ResponsiveContainer>
                        <ComposedChart data={chart}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="week" fontSize={11} />
                          <YAxis unit="h" fontSize={11} />
                          <Tooltip />
                          <Legend wrapperStyle={{ fontSize: 11 }} />
                          {projects.map((pr, i) => <Bar key={pr.allocation_id} dataKey={pr.project.name + (!["confirmed", "other"].includes(pr.state) ? ` (${pr.state})` : "")} stackId="h" fill={pr.billable ? COLORS[i % COLORS.length] : "#9ca3af"} fillOpacity={pr.state === "confirmed" ? 1 : 0.45} />)}
                          <Line dataKey="Capacity" stroke="#111827" strokeDasharray="4 3" dot={false} />
                          <ReferenceLine x={fmtWeek(tl.data.current_week)} stroke="#1d4ed8" label={{ value: "now", fontSize: 10 }} />
                        </ComposedChart>
                      </ResponsiveContainer>
                    </div>
                    <div className="mt-3 overflow-x-auto">
                      <table className="text-xs">
                        <tbody>
                          <tr><td className="pr-2 text-muted-foreground">Forecast</td>{tl.data.weeks.map((w: any) => <td key={w.week_start} className="p-0.5"><div className={`min-w-[52px] rounded px-1 py-0.5 text-center ${bandClass(w.status_forecast)}`} title={STATUS_LABELS[w.status_forecast]}>{pct(w.util_forecast)}</div></td>)}</tr>
                          <tr><td className="pr-2 text-muted-foreground">Confirmed</td>{tl.data.weeks.map((w: any) => <td key={w.week_start} className="p-0.5"><div className={`rounded px-1 py-0.5 text-center ${bandClass(w.status_confirmed)}`}>{pct(w.util_confirmed)}</div></td>)}</tr>
                          <tr><td className="pr-2 text-muted-foreground">Free h</td>{tl.data.weeks.map((w: any) => <td key={w.week_start} className="p-0.5 text-center">{w.free_hours}</td>)}</tr>
                        </tbody>
                      </table>
                    </div>
                  </CardBody>
                </Card>
                <Card>
                  <CardHeader title="Projects" />
                  {!projects.length ? <Empty>No allocations.</Empty> : (
                    <Table><thead><tr><Th>Project</Th><Th>State</Th><Th>Confidence</Th><Th>Billable</Th><Th>Source</Th><Th>Hours (16 wk)</Th></tr></thead>
                      <tbody>{projects.map((pr) => <tr key={pr.allocation_id} className={pr.billable ? "" : "text-muted-foreground"}><Td>{pr.project.id ? <Link className="text-primary hover:underline" to={`/projects/${pr.project.id}`}>{pr.project.name}</Link> : pr.project.name} <span className="text-xs text-muted-foreground">{pr.task_name}</span></Td><Td><Badge variant={pr.state === "confirmed" ? "green" : pr.state === "released" ? "grey" : "blue"}>{pr.state}</Badge></Td><Td>{pr.confidence == null ? "–" : `${pr.confidence}%`}</Td><Td>{pr.billable ? "Yes" : "No"}</Td><Td className="text-xs">{pr.source}</Td><Td>{hrs(pr.total_hours)}</Td></tr>)}</tbody></Table>
                  )}
                </Card>
              </>
            )}
          </ErrorOr>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card>
              <CardHeader title="Skills" actions={canEdit && <Button size="sm" variant="ghost" onClick={() => setDlg("skills")}><Pencil className="h-3.5 w-3.5" /> Edit</Button>} />
              <CardBody className="flex flex-wrap gap-1.5">{p.skills.length ? p.skills.map((s: any) => <Badge key={s.name} variant="blue">{s.name} · {["", "Basic", "Working", "Expert"][s.proficiency]}</Badge>) : <span className="text-sm text-muted-foreground">No skills recorded.</span>}</CardBody>
            </Card>
            <Card>
              <CardHeader title="Availability flags" description='"Available from <date> for <hours>/week"' />
              <CardBody>{p.availability_flags.length ? p.availability_flags.map((f: any) => <div key={f.id} className="flex items-center justify-between py-1 text-sm"><span>From {fmtDate(f.from_date)} · {f.hours_per_week} h/wk {f.note && <span className="text-muted-foreground">· {f.note}</span>}</span>{canEdit && <Button size="icon" variant="ghost" aria-label="Remove flag" onClick={() => delFlag.mutate(f.id)}><X className="h-3.5 w-3.5" /></Button>}</div>) : <span className="text-sm text-muted-foreground">None.</span>}</CardBody>
            </Card>
            <Card>
              <CardHeader title="PTO and holidays" description="Reduce capacity" />
              <CardBody>{p.capacity_exceptions.length ? p.capacity_exceptions.map((c: any) => <div key={c.id} className="flex items-center justify-between py-0.5 text-sm"><span>{fmtDate(c.date)} · {c.hours_off} h · {c.reason}</span>{canEdit && <Button size="icon" variant="ghost" aria-label="Remove" onClick={() => delPto.mutate(c.id)}><Trash2 className="h-3.5 w-3.5" /></Button>}</div>) : <span className="text-sm text-muted-foreground">None upcoming.</span>}</CardBody>
            </Card>
          </div>
          <Card>
            <CardHeader title="Change history" description="Imports and in-app edits for this person and their allocations" />
            <ChangeTable rows={hist.data} loading={hist.isLoading} />
          </Card>
          {dlg === "skills" && <SkillsDialog person={p} onClose={() => { setDlg(null); refresh(); }} />}
          {dlg === "flag" && <FlagDialog id={p.id} onClose={() => { setDlg(null); refresh(); }} />}
          {dlg === "pto" && <PtoDialog id={p.id} onClose={() => { setDlg(null); refresh(); }} />}
          {dlg === "profile" && <ProfileDialog person={p} onClose={() => { setDlg(null); refresh(); }} />}
          {(dlg === "request" || dlg === "offer") && <RequestDialog open kind={dlg} onOpenChange={(v) => !v && setDlg(null)} person={{ person_id: p.id, name: p.name }} />}
        </div>
      )}
    </ErrorOr>
  );
}

function SkillsDialog({ person, onClose }: { person: any; onClose: () => void }) {
  const [rows, setRows] = useState<{ name: string; proficiency: number }[]>(person.skills.map((s: any) => ({ name: s.name, proficiency: s.proficiency })));
  const [n, setN] = useState("");
  const skills = useQuery<any[]>({ queryKey: ["skills"], queryFn: () => api("/api/skills") });
  const m = useMutation({ mutationFn: () => api(`/api/people/${person.id}/skills`, { method: "PUT", body: rows }), onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Edit skills" description="Proficiency 1 = basic, 2 = working, 3 = expert" footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button loading={m.isPending} onClick={() => m.mutate()}>Save</Button></>}>
        <div className="space-y-2">
          {rows.map((r, i) => (
            <div key={r.name} className="flex items-center gap-2">
              <span className="flex-1 text-sm">{r.name}</span>
              <Select className="w-32" value={r.proficiency} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, proficiency: Number(e.target.value) } : x)))}>
                <option value={1}>1 Basic</option><option value={2}>2 Working</option><option value={3}>3 Expert</option>
              </Select>
              <Button size="icon" variant="ghost" aria-label={`Remove ${r.name}`} onClick={() => setRows(rows.filter((_, j) => j !== i))}><X className="h-4 w-4" /></Button>
            </div>
          ))}
          <div className="flex gap-2 pt-2">
            <Input list="all-skills" placeholder="Add a skill" value={n} onChange={(e) => setN(e.target.value)} />
            <datalist id="all-skills">{skills.data?.map((s) => <option key={s.id} value={s.name} />)}</datalist>
            <Button variant="outline" disabled={!n.trim() || rows.some((r) => r.name.toLowerCase() === n.trim().toLowerCase())} onClick={() => { setRows([...rows, { name: n.trim(), proficiency: 2 }]); setN(""); }}>Add</Button>
          </div>
        </div>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}

function FlagDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const [f, setF] = useState({ from_date: thisMonday(), hours_per_week: "20", note: "" });
  const m = useMutation({ mutationFn: () => api(`/api/people/${id}/availability`, { body: { ...f, hours_per_week: Number(f.hours_per_week), note: f.note || null } }), onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Mark available" description="Shows on the availability board for every PL." footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button loading={m.isPending} onClick={() => m.mutate()}>Save</Button></>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Available from"><Input type="date" value={f.from_date} onChange={(e) => setF({ ...f, from_date: e.target.value })} /></Field>
          <Field label="Hours / week"><Input type="number" min={1} max={80} value={f.hours_per_week} onChange={(e) => setF({ ...f, hours_per_week: e.target.value })} /></Field>
          <Field label="Note" className="col-span-2"><Input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}

function PtoDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const [f, setF] = useState({ date: thisMonday(), days: "1", hours_off: "8", reason: "PTO" });
  const m = useMutation({ mutationFn: () => api(`/api/people/${id}/capacity-exceptions`, { body: { date: f.date, days: Number(f.days), hours_off: Number(f.hours_off), reason: f.reason } }), onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Add PTO or holiday" footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button loading={m.isPending} onClick={() => m.mutate()}>Save</Button></>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="First day"><Input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
          <Field label="Working days"><Input type="number" min={1} max={60} value={f.days} onChange={(e) => setF({ ...f, days: e.target.value })} /></Field>
          <Field label="Hours off per day"><Input type="number" min={1} max={24} value={f.hours_off} onChange={(e) => setF({ ...f, hours_off: e.target.value })} /></Field>
          <Field label="Reason"><Select value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })}><option>PTO</option><option>Holiday</option><option>Sick</option><option>Training</option></Select></Field>
        </div>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}

function ProfileDialog({ person, onClose }: { person: any; onClose: () => void }) {
  const levels = useTechLevels();
  const groups = useGroups();
  const settings = useSettings();
  const [f, setF] = useState({ role_family: person.role_family ?? "", location: person.location ?? "", std_hours_week: String(person.std_hours_week), group_id: person.group_id ?? "", tech_level_id: person.tech_level_id ?? "", reason: "" });
  const m = useMutation({ mutationFn: () => api(`/api/people/${person.id}`, { method: "PATCH", body: { ...f, std_hours_week: Number(f.std_hours_week), reason: f.reason || null } }), onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Edit profile" description="Name, email and OpenAir fields come from the daily import." footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button loading={m.isPending} onClick={() => m.mutate()}>Save</Button></>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Role family"><Select value={f.role_family} onChange={(e) => setF({ ...f, role_family: e.target.value })}>{[...(settings.data?.role_families ?? []), "Unassigned"].map((r: string) => <option key={r}>{r}</option>)}</Select></Field>
          <Field label="Location"><Input list="locs" value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} /><datalist id="locs">{(settings.data?.locations ?? []).map((l: string) => <option key={l} value={l} />)}</datalist></Field>
          <Field label="Standard hours / week"><Input type="number" min={0} max={80} value={f.std_hours_week} onChange={(e) => setF({ ...f, std_hours_week: e.target.value })} /></Field>
          <Field label="Tech level"><Select value={f.tech_level_id} onChange={(e) => setF({ ...f, tech_level_id: e.target.value })}>{levels.data?.map((l) => <option key={l.id} value={l.id}>{l.code}</option>)}</Select></Field>
          <Field label="Group" className="col-span-2"><Select value={f.group_id} onChange={(e) => setF({ ...f, group_id: e.target.value })}>{groups.data?.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</Select></Field>
          <Field label="Reason" hint="optional" className="col-span-2"><Input value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
        </div>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}


