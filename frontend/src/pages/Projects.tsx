import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, Plus } from "lucide-react";
import { api, qs } from "@/api/client";
import type { Project } from "@/api/types";
import { ErrorOr } from "@/components/shared";
import { Badge, Button, Card, ConfidenceBadge, Dialog, DialogContent, Empty, ErrorBox, Field, Input, PageHeader, Select, Table, Td, Th, Toggle } from "@/components/ui";
import { useMe } from "@/hooks/queries";
import { download, fmtDate } from "@/lib/utils";

export const STATUS_VARIANT: Record<string, any> = { active: "green", pipeline: "blue", on_hold: "amber", lost: "grey", closed: "grey" };

export default function ProjectsPage() {
  const me = useMe().data!;
  const [scope, setScope] = useState(me.roles.includes("pl") ? "mine" : "all");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const q = useQuery<Project[]>({ queryKey: ["projects", scope, status], queryFn: () => api(`/api/projects${qs({ mine: scope === "mine", status })}`) });
  const rows = (q.data ?? []).filter((p) => !search || `${p.name} ${p.client}`.toLowerCase().includes(search.toLowerCase()));
  return (
    <div>
      <PageHeader
        title="Projects & demand"
        description="OpenAir projects plus pipeline projects created here before they exist in OpenAir."
        actions={<>
          <Toggle label="Scope" value={scope} onChange={setScope} options={[{ value: "mine", label: "My projects" }, { value: "all", label: "All" }]} />
          <Button variant="outline" size="sm" onClick={() => download("/api/export/projects.xlsx")}><Download className="h-4 w-4" /> Excel</Button>
          {me.roles.some((r) => ["pl", "admin", "gl", "sl"].includes(r)) && <Button size="sm" onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New pipeline project</Button>}
        </>}
      />
      <div className="mb-3 flex gap-2">
        <Input className="w-64" placeholder="Search name or client" value={search} onChange={(e) => setSearch(e.target.value)} />
        <Select className="w-44" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {["active", "pipeline", "on_hold", "lost", "closed"].map((s) => <option key={s}>{s}</option>)}
        </Select>
      </div>
      <Card>
        <ErrorOr q={q}>
          {!rows.length ? <Empty>No projects.</Empty> : (
            <Table>
              <thead><tr><Th>Project</Th><Th>Client</Th><Th>PL</Th><Th>Status</Th><Th>Start</Th><Th>End</Th><Th>Billable</Th><Th>OpenAir ID</Th><Th>Demand</Th><Th>People</Th></tr></thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id} className="hover:bg-muted/40">
                    <Td><Link className="font-medium text-primary hover:underline" to={`/projects/${p.id}`}>{p.name}</Link></Td>
                    <Td>{p.client}</Td>
                    <Td className="text-xs">{p.pl_name}</Td>
                    <Td><Badge variant={STATUS_VARIANT[p.status]}>{p.status.replace("_", " ")}</Badge></Td>
                    <Td className="text-xs">{fmtDate(p.start_date)}</Td>
                    <Td className="text-xs">{fmtDate(p.end_date)}</Td>
                    <Td>{p.billable ? "Yes" : <span className="text-muted-foreground">No</span>}</Td>
                    <Td className="text-xs">{p.openair_project_id ?? <Badge variant="purple">app only</Badge>}</Td>
                    <Td className="text-xs">{p.demand_lines ? <span>{p.demand_lines} lines{p.open_lines ? `, ${p.open_lines} open` : ""} {p.min_confidence !== null && p.min_confidence !== undefined && p.min_confidence < 100 && <ConfidenceBadge value={p.min_confidence} />}</span> : "–"}</Td>
                    <Td>{p.people}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </ErrorOr>
      </Card>
      {creating && <NewProjectDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function NewProjectDialog({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [f, setF] = useState({ name: "", client: "", status: "pipeline", billable: true, start_date: "", end_date: "", openair_project_id: "" });
  const m = useMutation({
    mutationFn: () => api("/api/projects", { body: { ...f, start_date: f.start_date || null, end_date: f.end_date || null, openair_project_id: f.openair_project_id || null } }),
    onSuccess: (p: any) => nav(`/projects/${p.id}`),
  });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="New pipeline project" description="Links automatically when a later OpenAir import contains this OpenAir ID (or the same name)." footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button loading={m.isPending} disabled={!f.name} onClick={() => m.mutate()}>Create</Button></>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name" className="col-span-2"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus /></Field>
          <Field label="Client"><Input value={f.client} onChange={(e) => setF({ ...f, client: e.target.value })} /></Field>
          <Field label="Status"><Select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="pipeline">pipeline</option><option value="active">active</option></Select></Field>
          <Field label="Start"><Input type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></Field>
          <Field label="End"><Input type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></Field>
          <Field label="OpenAir project ID" hint="if known"><Input value={f.openair_project_id} onChange={(e) => setF({ ...f, openair_project_id: e.target.value })} /></Field>
          <label className="flex items-center gap-2 pt-5 text-sm"><input type="checkbox" checked={f.billable} onChange={(e) => setF({ ...f, billable: e.target.checked })} /> Billable</label>
        </div>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}
