import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCheck, Info, OctagonAlert } from "lucide-react";
import { api } from "@/api/client";
import type { Alert, Change, DemandLine, Project } from "@/api/types";
import { Badge, Button, Card, CardHeader, Dialog, DialogContent, Empty, ErrorBox, Field, Input, Select, Spinner, Table, Td, Th, Textarea } from "@/components/ui";
import { addWeeks, cn, fmtDateTime, fmtWeek, thisMonday } from "@/lib/utils";
import { useMe } from "@/hooks/queries";

const SEV_ICON = { critical: OctagonAlert, warning: AlertTriangle, info: Info };
const SEV_CLS = { critical: "text-red-700", warning: "text-amber-700", info: "text-blue-700" };

export function AlertFeed({ limit = 50 }: { limit?: number }) {
  const qc = useQueryClient();
  const q = useQuery<{ unread: number; items: Alert[] }>({ queryKey: ["alerts"], queryFn: () => api("/api/alerts") });
  const mark = useMutation({
    mutationFn: (v: { id?: string; status: string; all?: boolean }) => (v.all ? api("/api/alerts", { method: "PATCH", body: { all: true, status: v.status } }) : api(`/api/alerts/${v.id}`, { method: "PATCH", body: { status: v.status } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });
  return (
    <Card id="alerts">
      <CardHeader
        title={<span>Alert feed {!!q.data?.unread && <Badge variant="red" className="ml-1">{q.data.unread} new</Badge>}</span>}
        description="Generated after each import and each edit"
        actions={<Button size="sm" variant="ghost" onClick={() => mark.mutate({ all: true, status: "seen" })}><CheckCheck className="h-4 w-4" /> Mark all seen</Button>}
      />
      {q.isLoading ? <Spinner /> : !q.data?.items.length ? <Empty>No alerts. All quiet.</Empty> : (
        <ul className="max-h-[480px] divide-y divide-border overflow-auto">
          {q.data.items.slice(0, limit).map((a) => {
            const Icon = SEV_ICON[a.severity] ?? Info;
            return (
              <li key={a.id} className={cn("flex gap-3 px-4 py-2.5", a.status === "new" && "bg-blue-50/50")}>
                <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", SEV_CLS[a.severity])} aria-label={a.severity} />
                <div className="min-w-0 flex-1">
                  <div className="text-sm">{a.message}</div>
                  <div className="mt-0.5 flex flex-wrap gap-3 text-xs text-muted-foreground">
                    <span>{fmtDateTime(a.created_at)}</span>
                    {a.person_id && <Link className="text-primary hover:underline" to={`/people/${a.person_id}`}>{a.person_name}</Link>}
                    {a.project_id && <Link className="text-primary hover:underline" to={`/projects/${a.project_id}`}>{a.project_name}</Link>}
                    <span className="uppercase">{a.type.replace(/_/g, " ")}</span>
                  </div>
                </div>
                <div className="flex shrink-0 items-start gap-1">
                  {a.status === "new" && <Button size="sm" variant="ghost" onClick={() => mark.mutate({ id: a.id, status: "seen" })}>Seen</Button>}
                  <Button size="sm" variant="ghost" onClick={() => mark.mutate({ id: a.id, status: "done" })}>Done</Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

/** AVL-4: "Request" a person (PL -> GL) or "Offer" a person (GL -> PL). */
export function RequestDialog({ open, onOpenChange, person, kind = "request", defaultProjectId, defaultDemand, from, to }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  person: { person_id: string; name: string; free_hours_avg?: number } | null;
  kind?: "request" | "offer";
  defaultProjectId?: string;
  defaultDemand?: DemandLine | null;
  from?: string;
  to?: string;
}) {
  const me = useMe().data;
  const qc = useQueryClient();
  const projects = useQuery<Project[]>({ queryKey: ["projects"], queryFn: () => api("/api/projects"), enabled: open });
  const [projectId, setProjectId] = useState(defaultProjectId ?? "");
  const [demandId, setDemandId] = useState(defaultDemand?.id ?? "");
  const [hours, setHours] = useState(String(defaultDemand?.hours_per_week ?? Math.min(20, Math.round(person?.free_hours_avg ?? 20)) ?? 20));
  const [start, setStart] = useState(defaultDemand?.start_week ?? from ?? thisMonday());
  const [end, setEnd] = useState(defaultDemand?.end_week ?? to ?? addWeeks(thisMonday(), 7));
  const [note, setNote] = useState("");
  const pid = projectId || defaultProjectId || "";
  const demand = useQuery<DemandLine[]>({ queryKey: ["demand", pid], queryFn: () => api(`/api/projects/${pid}/demand`), enabled: open && !!pid });
  const send = useMutation({
    mutationFn: () => api("/api/requests", { body: { kind, person_id: person!.person_id, project_id: pid, demand_line_id: demandId || null, hours_per_week: Number(hours), start_week: start, end_week: end, note: note || null } }),
    onSuccess: () => {
      qc.invalidateQueries();
      onOpenChange(false);
    },
  });
  const mine = (projects.data ?? []).filter((p) => (kind === "request" ? me?.pl_project_ids.includes(p.id) || me?.roles.includes("admin") : true) && !["lost", "closed"].includes(p.status));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={kind === "request" ? `Request ${person?.name ?? ""}` : `Offer ${person?.name ?? ""}`}
        description={kind === "request" ? "Sends a request to the person's GL. It becomes Confirmed when the GL and the project's PL have both accepted." : "Offers the person to a project's PL."}
        footer={<><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button onClick={() => send.mutate()} loading={send.isPending} disabled={!pid || !Number(hours)}>{kind === "request" ? "Send request" : "Send offer"}</Button></>}
      >
        <div className="grid grid-cols-2 gap-3">
          <Field label="Project" className="col-span-2">
            <Select value={pid} onChange={(e) => { setProjectId(e.target.value); setDemandId(""); }}>
              <option value="">Choose a project…</option>
              {mine.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.client ?? ""} ({p.status})</option>)}
            </Select>
          </Field>
          <Field label="Demand line" hint="optional" className="col-span-2">
            <Select value={demandId} onChange={(e) => {
              setDemandId(e.target.value);
              const d = demand.data?.find((x) => x.id === e.target.value);
              if (d) { setHours(String(d.hours_per_week)); setStart(d.start_week); setEnd(d.end_week); }
            }}>
              <option value="">No specific line</option>
              {(demand.data ?? []).filter((d) => d.status !== "cancelled").map((d) => <option key={d.id} value={d.id}>{d.role_family ?? "Any role"} {d.min_tech_level ?? ""} · {d.hours_per_week} h/wk · {fmtWeek(d.start_week)}–{fmtWeek(d.end_week)} · {d.confidence}% ({d.status})</option>)}
            </Select>
          </Field>
          <Field label="Hours / week"><Input type="number" min={1} max={80} value={hours} onChange={(e) => setHours(e.target.value)} /></Field>
          <div />
          <Field label="From week"><Input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
          <Field label="To week"><Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
          <Field label="Note" className="col-span-2"><Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why this person, anything the GL should know" /></Field>
        </div>
        <div className="mt-3"><ErrorBox error={send.error} /></div>
      </DialogContent>
    </Dialog>
  );
}

export function ChangeTable({ rows, loading }: { rows?: Change[]; loading?: boolean }) {
  if (loading) return <Spinner />;
  if (!rows?.length) return <Empty>No changes recorded yet.</Empty>;
  return (
    <Table>
      <thead>
        <tr><Th>When</Th><Th>Who</Th><Th>What</Th><Th>Field</Th><Th>Old</Th><Th>New</Th><Th>Reason</Th></tr>
      </thead>
      <tbody>
        {rows.map((c) => (
          <tr key={c.id}>
            <Td className="whitespace-nowrap text-xs">{fmtDateTime(c.at)}</Td>
            <Td className="text-xs">{c.actor.startsWith("import:") ? <Badge variant="purple">import</Badge> : c.actor}</Td>
            <Td className="text-xs">{c.entity.replace(/_/g, " ")}{c.entity_key ? <span className="ml-1 text-muted-foreground">{c.entity_key}</span> : null}</Td>
            <Td className="text-xs">{c.field ?? c.action}</Td>
            <Td className="max-w-[180px] truncate text-xs text-muted-foreground" title={c.old_value ?? ""}>{c.old_value ?? "–"}</Td>
            <Td className="max-w-[220px] truncate text-xs" title={c.new_value ?? ""}>{c.new_value ?? "–"}</Td>
            <Td className="max-w-[200px] truncate text-xs text-muted-foreground" title={c.reason ?? ""}>{c.reason ?? ""}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

export function ErrorOr({ q, children }: { q: { isLoading: boolean; error: unknown }; children: React.ReactNode }) {
  if (q.isLoading) return <Spinner />;
  if (q.error) return <div className="p-4"><ErrorBox error={q.error} /></div>;
  return <>{children}</>;
}
