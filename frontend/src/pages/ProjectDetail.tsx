import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";
import { api } from "@/api/client";
import type { DemandLine } from "@/api/types";
import { ErrorOr, RequestDialog } from "@/components/shared";
import { Badge, Button, Card, CardHeader, ConfidenceBadge, Dialog, DialogContent, Empty, ErrorBox, Field, Input, PageHeader, Select, Spinner, Table, Td, Textarea, Th } from "@/components/ui";
import { useMe, useSettings, useSkills, useTechLevels } from "@/hooks/queries";
import { addWeeks, cn, fmtDate, fmtWeek, hrs, money, thisMonday } from "@/lib/utils";
import { STATUS_VARIANT } from "./Projects";

export default function ProjectDetailPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const me = useMe().data!;
  const q = useQuery<any>({ queryKey: ["project", id], queryFn: () => api(`/api/projects/${id}`) });
  const [edit, setEdit] = useState<DemandLine | "new" | null>(null);
  const [release, setRelease] = useState<{ line?: DemandLine; prompt: { count: number; people: { name: string }[] } } | null>(null);
  const [lost, setLost] = useState(false);
  const [matchFor, setMatchFor] = useState<DemandLine | null>(null);
  const [projEdit, setProjEdit] = useState(false);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["project", id] });
    qc.invalidateQueries({ queryKey: ["alerts"] });
    qc.invalidateQueries({ queryKey: ["dash"] });
    qc.invalidateQueries({ queryKey: ["heatmap"] });
  };
  const setConf = useMutation({
    mutationFn: ({ line, confidence }: { line: DemandLine; confidence: number }) => api(`/api/demand/${line.id}`, { method: "PATCH", body: { confidence } }),
    onSuccess: (r: DemandLine, v) => {
      refresh();
      if (r.release_prompt && r.release_prompt.count > 0) setRelease({ line: v.line, prompt: r.release_prompt });
    },
  });
  const del = useMutation({ mutationFn: (l: DemandLine) => api(`/api/demand/${l.id}`, { method: "DELETE" }), onSuccess: refresh });
  const p = q.data;
  const canEdit = p?.can_edit;
  const weeks: string[] = p ? Array.from(new Set(p.demand.flatMap((d: DemandLine) => Object.keys(d.fill_by_week)))).sort() as string[] : [];

  return (
    <ErrorOr q={q}>
      {p && (
        <div className="space-y-4">
          <PageHeader
            title={p.name}
            description={<span className="flex flex-wrap items-center gap-2">{p.client} · PL {p.pl_name ?? "–"} · <Badge variant={STATUS_VARIANT[p.status]}>{p.status.replace("_", " ")}</Badge> {p.billable ? <Badge variant="green">Billable</Badge> : <Badge variant="grey">Non-billable</Badge>} {p.openair_project_id ? <span>OpenAir {p.openair_project_id}</span> : <Badge variant="purple">Pipeline, not yet in OpenAir</Badge>} · {fmtDate(p.start_date)} – {fmtDate(p.end_date)}</span>}
            actions={canEdit && <>
              <Button variant="outline" size="sm" onClick={() => setProjEdit(true)}><Pencil className="h-4 w-4" /> Edit project</Button>
              {p.status !== "lost" && <Button variant="destructive" size="sm" onClick={() => setLost(true)}><Ban className="h-4 w-4" /> Mark lost</Button>}
              <Button size="sm" onClick={() => setEdit("new")}><Plus className="h-4 w-4" /> Add demand line</Button>
            </>}
          />
          <Card>
            <CardHeader title="Demand lines" description="Raw and confidence-weighted hours are both shown. Changing confidence to 0% offers to release the people attached." />
            {!p.demand.length ? <Empty>No demand lines yet.</Empty> : (
              <Table>
                <thead><tr><Th>Need</Th><Th>Skills</Th><Th>Hours / wk</Th><Th>Weeks</Th><Th>Confidence</Th><Th>Named person</Th><Th>Fill</Th>{me.can_see_rates && <Th>Rate</Th>}<Th /></tr></thead>
                <tbody>
                  {p.demand.map((d: DemandLine) => (
                    <tr key={d.id} className={cn(d.status === "cancelled" && "opacity-50")}>
                      <Td><div className="font-medium">{d.role_family ?? "Any role"}</div><div className="text-xs text-muted-foreground">{d.min_tech_level ? `${d.min_tech_level} or above` : "any level"}</div></Td>
                      <Td className="text-xs">{d.skills.join(", ") || "–"}</Td>
                      <Td><div>{hrs(d.hours_per_week)}</div><div className="text-xs text-muted-foreground">weighted {hrs(d.weighted_hours_per_week)}</div></Td>
                      <Td className="whitespace-nowrap text-xs">{fmtWeek(d.start_week)} – {fmtWeek(d.end_week)}</Td>
                      <Td>
                        {canEdit && d.status !== "cancelled" ? (
                          <Select aria-label="Confidence" className="h-8 w-44 text-xs" value={d.confidence} onChange={(e) => setConf.mutate({ line: d, confidence: Number(e.target.value) })}>
                            {[100, 75, 50, 25, 0].map((c) => <option key={c} value={c}>{c}% {({ 100: "Signed / active", 75: "Verbal yes, SOW pending", 50: "Proposal out", 25: "Early pipeline", 0: "Lost / on hold" } as any)[c]}</option>)}
                          </Select>
                        ) : <ConfidenceBadge value={d.confidence} />}
                      </Td>
                      <Td className="text-xs">{d.requested_person_name ? <Link className="text-primary hover:underline" to={`/people/${d.requested_person_id}`}>{d.requested_person_name}</Link> : <span className="text-muted-foreground">open</span>}{d.people_attached ? <div className="text-muted-foreground">{d.people_attached} attached</div> : null}</Td>
                      <Td><Badge variant={d.status === "filled" ? "green" : d.status === "partly" ? "amber" : d.status === "cancelled" ? "grey" : "red"}>{d.status}</Badge></Td>
                      {me.can_see_rates && <Td className="text-xs">{money(d.requested_rate)}</Td>}
                      <Td className="whitespace-nowrap">
                        {d.status !== "cancelled" && d.status !== "filled" && <Button size="sm" variant="ghost" onClick={() => setMatchFor(d)}><Sparkles className="h-3.5 w-3.5" /> Matches</Button>}
                        {canEdit && d.status !== "cancelled" && <><Button size="icon" variant="ghost" aria-label="Edit line" onClick={() => setEdit(d)}><Pencil className="h-3.5 w-3.5" /></Button><Button size="icon" variant="ghost" aria-label="Cancel line" onClick={() => del.mutate(d)}><Trash2 className="h-3.5 w-3.5" /></Button></>}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
            <ErrorBox error={setConf.error || del.error} />
          </Card>
          {!!weeks.length && (
            <Card>
              <CardHeader title="Fill status per week" description="Confirmed hours against required hours; pending = requested or proposed" />
              <div className="overflow-x-auto p-3">
                <table className="text-xs">
                  <thead><tr><th className="px-2 text-left text-muted-foreground">Line</th>{weeks.map((w) => <th key={w} className="px-1 text-muted-foreground">{fmtWeek(w)}</th>)}</tr></thead>
                  <tbody>
                    {p.demand.filter((d: DemandLine) => d.status !== "cancelled").map((d: DemandLine) => (
                      <tr key={d.id}>
                        <td className="whitespace-nowrap px-2 py-1">{d.role_family} {d.min_tech_level} · {d.confidence}%</td>
                        {weeks.map((w) => {
                          const f = d.fill_by_week[w];
                          if (!f) return <td key={w} />;
                          const cls = f.unfilled <= 0 ? "band-healthy" : f.confirmed > 0 || f.pending > 0 ? "band-light" : "band-bench";
                          return <td key={w} className="p-0.5"><div className={`rounded px-1.5 py-1 text-center ${cls}`} title={`required ${f.required}, confirmed ${f.confirmed}, pending ${f.pending}`}>{f.confirmed}/{f.required}{f.pending ? <span className="block text-[9px]">+{f.pending} pending</span> : null}</div></td>;
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
          <Card>
            <CardHeader title="People on this project" actions={<Link className="text-sm text-primary hover:underline" to={`/grid?project=${p.id}`}>Open in allocation grid</Link>} />
            {!p.allocations.length ? <Empty>Nobody allocated yet.</Empty> : (
              <Table>
                <thead><tr><Th>Person</Th><Th>Level</Th><Th>State</Th><Th>Total hours</Th><Th>Weeks</Th></tr></thead>
                <tbody>{p.allocations.map((a: any) => <tr key={a.id}><Td><Link className="text-primary hover:underline" to={`/people/${a.person_id}`}>{a.person_name}</Link></Td><Td>{a.tech_level}</Td><Td><Badge variant={a.state === "confirmed" ? "green" : a.state === "released" || a.state === "declined" ? "grey" : "blue"}>{a.state}</Badge></Td><Td>{hrs(a.total_hours)}</Td><Td className="text-xs">{a.first_week ? `${fmtWeek(a.first_week)} – ${fmtWeek(a.last_week)}` : "–"}</Td></tr>)}</tbody>
              </Table>
            )}
          </Card>
          {edit && <DemandDialog projectId={p.id} line={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={(r) => { setEdit(null); refresh(); if (r?.release_prompt?.count) setRelease({ line: r, prompt: r.release_prompt }); }} />}
          {release && <ReleaseDialog projectId={p.id} state={release} onClose={() => { setRelease(null); refresh(); }} />}
          {lost && <LostDialog project={p} onClose={() => { setLost(false); refresh(); }} />}
          {matchFor && <MatchesDialog line={matchFor} projectId={p.id} onClose={() => setMatchFor(null)} />}
          {projEdit && <ProjectEditDialog project={p} onClose={() => { setProjEdit(false); refresh(); }} />}
        </div>
      )}
    </ErrorOr>
  );
}

function DemandDialog({ projectId, line, onClose, onSaved }: { projectId: string; line: DemandLine | null; onClose: () => void; onSaved: (r: any) => void }) {
  const levels = useTechLevels();
  const skills = useSkills();
  const settings = useSettings();
  const people = useQuery<any[]>({ queryKey: ["people"], queryFn: () => api("/api/people") });
  const [f, setF] = useState({
    role_family: line?.role_family ?? "",
    min_tech_level_id: line?.min_tech_level_id ?? "",
    skills: line?.skills.join(", ") ?? "",
    hours_per_week: String(line?.hours_per_week ?? 20),
    start_week: line?.start_week ?? addWeeks(thisMonday(), 2),
    end_week: line?.end_week ?? addWeeks(thisMonday(), 9),
    confidence: String(line?.confidence ?? 50),
    requested_person_id: line?.requested_person_id ?? "",
    notes: line?.notes ?? "",
  });
  const body = { ...f, hours_per_week: Number(f.hours_per_week), confidence: Number(f.confidence), skills: f.skills.split(",").map((s) => s.trim()).filter(Boolean), min_tech_level_id: f.min_tech_level_id || null, requested_person_id: f.requested_person_id || null, role_family: f.role_family || null, notes: f.notes || null };
  const m = useMutation({ mutationFn: () => (line ? api(`/api/demand/${line.id}`, { method: "PATCH", body }) : api(`/api/projects/${projectId}/demand`, { body })), onSuccess: onSaved });
  const weighted = (Number(f.hours_per_week) * Number(f.confidence)) / 100;
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title={line ? "Edit demand line" : "Add demand line"} footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button loading={m.isPending} onClick={() => m.mutate()}>Save</Button></>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Role family"><Select value={f.role_family} onChange={(e) => setF({ ...f, role_family: e.target.value })}><option value="">Any</option>{(settings.data?.role_families ?? []).map((r: string) => <option key={r}>{r}</option>)}</Select></Field>
          <Field label="Minimum Tech level"><Select value={f.min_tech_level_id} onChange={(e) => setF({ ...f, min_tech_level_id: e.target.value })}><option value="">Any</option>{levels.data?.map((l) => <option key={l.id} value={l.id}>{l.code}</option>)}</Select></Field>
          <Field label="Skills" hint="comma separated" className="col-span-2"><Input list="skill-list" value={f.skills} onChange={(e) => setF({ ...f, skills: e.target.value })} placeholder="SQL, Python" /><datalist id="skill-list">{skills.data?.map((s) => <option key={s.id} value={s.name} />)}</datalist></Field>
          <Field label="Hours / week"><Input type="number" min={1} max={80} value={f.hours_per_week} onChange={(e) => setF({ ...f, hours_per_week: e.target.value })} /></Field>
          <Field label="Confidence" hint={`weighted ${weighted} h/wk`}><Select value={f.confidence} onChange={(e) => setF({ ...f, confidence: e.target.value })}>{[100, 75, 50, 25, 0].map((c) => <option key={c} value={c}>{c}%</option>)}</Select></Field>
          <Field label="Start week"><Input type="date" value={f.start_week} onChange={(e) => setF({ ...f, start_week: e.target.value })} /></Field>
          <Field label="End week"><Input type="date" value={f.end_week} onChange={(e) => setF({ ...f, end_week: e.target.value })} /></Field>
          <Field label="Named person" hint="soft request, optional" className="col-span-2"><Select value={f.requested_person_id} onChange={(e) => setF({ ...f, requested_person_id: e.target.value })}><option value="">Leave open</option>{people.data?.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.tech_level} · {p.role_family}</option>)}</Select></Field>
          <Field label="Notes" className="col-span-2"><Textarea value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        </div>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}

function ReleaseDialog({ state, onClose }: { projectId: string; state: { line?: DemandLine; prompt: { count: number; people: { name: string }[] } }; onClose: () => void }) {
  const m = useMutation({ mutationFn: () => api(`/api/demand/${state.line!.id}/release`, { method: "POST" }), onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title={`Release ${state.prompt.count} ${state.prompt.count === 1 ? "person" : "people"}?`} description="The line is now at 0% (lost / on hold). Releasing frees their hours and notifies their GLs." footer={<><Button variant="outline" onClick={onClose}>Not now</Button><Button loading={m.isPending} onClick={() => m.mutate()}>Release</Button></>}>
        <ul className="list-disc pl-5 text-sm">{state.prompt.people.map((p) => <li key={p.name}>{p.name}</li>)}</ul>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}

function LostDialog({ project, onClose }: { project: any; onClose: () => void }) {
  const attached = new Set(project.allocations.filter((a: any) => ["proposed", "requested", "confirmed"].includes(a.state)).map((a: any) => a.person_name));
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<any>(null);
  const m = useMutation({ mutationFn: (release: boolean) => api(`/api/projects/${project.id}/lost?release=${release}${reason ? `&reason=${encodeURIComponent(reason)}` : ""}`, { method: "POST" }), onSuccess: setResult });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title={`Mark ${project.name} lost?`} description="Every demand line goes to 0%." footer={result ? <Button onClick={onClose}>Close</Button> : <><Button variant="outline" onClick={() => m.mutate(false)} loading={m.isPending}>Mark lost, keep people</Button><Button variant="destructive" onClick={() => m.mutate(true)} loading={m.isPending}>Release {attached.size} {attached.size === 1 ? "person" : "people"}</Button></>}>
        {result ? <p className="text-sm">Done. Released: {result.released.map((r: any) => r.name).join(", ") || "nobody"}. Their GLs have been alerted.</p> : (
          <>
            <p className="mb-2 text-sm">Release {attached.size} {attached.size === 1 ? "person" : "people"}? {Array.from(attached).join(", ")}</p>
            <Field label="Reason" hint="optional"><Input value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
          </>
        )}
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}

function MatchesDialog({ line, projectId, onClose }: { line: DemandLine; projectId: string; onClose: () => void }) {
  const me = useMe().data!;
  const q = useQuery<any[]>({ queryKey: ["matches", line.id], queryFn: () => api(`/api/demand/${line.id}/matches`) });
  const [req, setReq] = useState<any>(null);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Suggested matches" description={`Top 5 for ${line.role_family ?? "any role"} ${line.min_tech_level ?? ""}+ ${line.skills.join(", ")}, ranked by level fit, skill match, free hours and project continuity. People decide.`} className="max-w-3xl">
        {q.isLoading ? <Spinner /> : !q.data?.length ? <Empty>Nobody with free hours matches.</Empty> : (
          <Table>
            <thead><tr><Th>Score</Th><Th>Person</Th><Th>Level</Th><Th>Skills</Th><Th>Free / wk</Th>{me.can_see_rates && <Th>Rate</Th>}<Th /></tr></thead>
            <tbody>
              {q.data.map((m) => (
                <tr key={m.person_id}>
                  <Td className="font-semibold">{m.score}</Td>
                  <Td><Link className="text-primary hover:underline" to={`/people/${m.person_id}`}>{m.name}</Link><div className="text-xs text-muted-foreground">{m.group_name}{m.continuity && " · knows this client"}</div></Td>
                  <Td>{m.tech_level}</Td>
                  <Td className="text-xs">{m.skills.map((s: any) => <span key={s.name} className={cn("mr-1", m.skill_hits.includes(s.name) && "font-semibold text-green-800")}>{s.name}({s.proficiency})</span>)}</Td>
                  <Td>{hrs(m.free_hours_avg)}</Td>
                  {me.can_see_rates && <Td className="text-xs">{money(m.bill_rate)}{m.margin_warning ? <div className="text-amber-800">+${m.margin_warning}/h margin warning</div> : null}</Td>}
                  <Td><Button size="sm" onClick={() => setReq(m)}>Request</Button></Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
        {req && <RequestDialog open onOpenChange={(v) => !v && setReq(null)} person={req} defaultProjectId={projectId} defaultDemand={line} />}
      </DialogContent>
    </Dialog>
  );
}

function ProjectEditDialog({ project, onClose }: { project: any; onClose: () => void }) {
  const [f, setF] = useState({ name: project.name, client: project.client ?? "", status: project.status, billable: project.billable, start_date: project.start_date ?? "", end_date: project.end_date ?? "", openair_project_id: project.openair_project_id ?? "" });
  const m = useMutation({ mutationFn: () => api(`/api/projects/${project.id}`, { method: "PATCH", body: { ...f, start_date: f.start_date || null, end_date: f.end_date || null, openair_project_id: f.openair_project_id || null } }), onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Edit project" footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button loading={m.isPending} onClick={() => m.mutate()}>Save</Button></>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name" className="col-span-2"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Client"><Input value={f.client} onChange={(e) => setF({ ...f, client: e.target.value })} /></Field>
          <Field label="Status"><Select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{["active", "pipeline", "on_hold", "lost", "closed"].map((s) => <option key={s}>{s}</option>)}</Select></Field>
          <Field label="Start"><Input type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></Field>
          <Field label="End"><Input type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></Field>
          <Field label="OpenAir project ID"><Input value={f.openair_project_id} onChange={(e) => setF({ ...f, openair_project_id: e.target.value })} /></Field>
          <label className="flex items-center gap-2 pt-5 text-sm"><input type="checkbox" checked={f.billable} onChange={(e) => setF({ ...f, billable: e.target.checked })} /> Billable</label>
        </div>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}
