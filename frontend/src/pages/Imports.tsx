import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileUp, Play, RefreshCw } from "lucide-react";
import { api } from "@/api/client";
import type { ImportRunSummary } from "@/api/types";
import { ErrorOr } from "@/components/shared";
import { Badge, Button, Card, CardBody, CardHeader, Dialog, DialogContent, Empty, ErrorBox, PageHeader, Select, Spinner, Stat, Table, Tabs, TabsContent, TabsList, TabsTrigger, Td, Th } from "@/components/ui";
import { download, fmtDateTime, fmtWeek } from "@/lib/utils";

const RUN_V: Record<string, any> = { succeeded: "green", skipped: "grey", failed: "red", running: "blue" };

export default function ImportsPage() {
  return (
    <div>
      <PageHeader title="Imports" description="Load OpenAir report exports. Each load applies only what changed since the last one; re-running the same file changes nothing." />
      <Tabs defaultValue="upload">
        <TabsList>
          <TabsTrigger value="upload">Upload</TabsTrigger>
          <TabsTrigger value="history">Run history</TabsTrigger>
          <TabsTrigger value="conflicts">Conflicts</TabsTrigger>
          <TabsTrigger value="pending">Pending OpenAir updates</TabsTrigger>
        </TabsList>
        <TabsContent value="upload"><Upload /></TabsContent>
        <TabsContent value="history"><History /></TabsContent>
        <TabsContent value="conflicts"><Conflicts /></TabsContent>
        <TabsContent value="pending"><PendingOpenAir /></TabsContent>
      </Tabs>
    </div>
  );
}

function Upload() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<any>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [summary, setSummary] = useState<ImportRunSummary | null>(null);
  const [fullSnapshot, setFullSnapshot] = useState(true);
  const upload = useMutation({
    mutationFn: (f: File) => {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("auto_run", "true");
      fd.append("full_snapshot", String(fullSnapshot));
      return api("/api/imports/upload", { form: fd });
    },
    onSuccess: (r) => {
      setPreview(r);
      setMapping(r.mapping);
      setSummary(r.run ?? null);
      if (r.run) qc.invalidateQueries();
    },
  });
  const run = useMutation({
    mutationFn: () => api("/api/imports/run", { body: { raw_file_id: preview.raw_file_id, mapping, save_mapping: true, full_snapshot: fullSnapshot } }),
    onSuccess: (r) => { setSummary(r); qc.invalidateQueries(); },
  });
  const pull = useMutation({ mutationFn: () => api("/api/imports/run", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["imports"] }) });
  const needsMapping = preview && !preview.run;
  const missing = preview ? preview.fields.filter((f: any) => f.required && !mapping[f.field]).map((f: any) => f.label) : [];
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Upload an OpenAir report export" description="CSV or XLSX. Column mapping is saved per report type, so the same export loads with no clicks next time." actions={<Button variant="outline" size="sm" onClick={() => pull.mutate()} loading={pull.isPending}><RefreshCw className="h-4 w-4" /> Run scheduled pull now</Button>} />
        <CardBody className="flex flex-wrap items-center gap-3">
          <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) { setSummary(null); upload.mutate(f); } e.target.value = ""; }} />
          <Button onClick={() => fileRef.current?.click()} loading={upload.isPending}><FileUp className="h-4 w-4" /> Choose file…</Button>
          <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={fullSnapshot} onChange={(e) => setFullSnapshot(e.target.checked)} /> File is a full snapshot (bookings missing from it are removed)</label>
          <span className="ml-auto text-xs text-muted-foreground">Sample files:</span>
          <Button variant="link" size="sm" onClick={() => download("/api/imports/samples/openair_export_day1.csv")}>day 1 (yesterday)</Button>
          <Button variant="link" size="sm" onClick={() => download("/api/imports/samples/openair_export_day2.csv")}>day 2 (today)</Button>
        </CardBody>
        {pull.data && <CardBody className="pt-0 text-sm">{pull.data.message ?? `${pull.data.runs.length} run(s) started from the inbox / API.`}</CardBody>}
        <div className="px-4 pb-3"><ErrorBox error={upload.error || run.error || pull.error} /></div>
      </Card>
      {summary && <RunSummary s={summary} />}
      {needsMapping && (
        <Card>
          <CardHeader title={`Map columns: ${preview.file_name}`} description={`${preview.row_count} rows. ${preview.has_saved_mapping ? "Saved mapping applied where the columns match." : "No saved mapping yet; columns were matched by name."}`} actions={<Button onClick={() => run.mutate()} loading={run.isPending} disabled={missing.length > 0}><Play className="h-4 w-4" /> Load file</Button>} />
          {missing.length > 0 && <div className="mx-4 mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">Map the required fields: {missing.join(", ")}. You also need user ID or email, and hours or hours per day.</div>}
          <div className="grid gap-x-6 gap-y-2 p-4 md:grid-cols-2 xl:grid-cols-3">
            {preview.fields.map((f: any) => (
              <label key={f.field} className="flex items-center gap-2 text-sm">
                <span className="w-44 shrink-0">{f.label}{f.required && <span className="text-red-700"> *</span>}</span>
                <Select value={mapping[f.field] ?? ""} onChange={(e) => setMapping({ ...mapping, [f.field]: e.target.value || null })}>
                  <option value="">(not in file)</option>
                  {preview.columns.map((c: string) => <option key={c} value={c}>{c}</option>)}
                </Select>
              </label>
            ))}
          </div>
          <div className="border-t border-border p-4">
            <div className="mb-2 text-xs font-medium text-muted-foreground">First rows</div>
            <Table><thead><tr>{preview.columns.map((c: string) => <Th key={c}>{c}</Th>)}</tr></thead>
              <tbody>{preview.sample_rows.map((r: any, i: number) => <tr key={i}>{preview.columns.map((c: string) => <Td key={c} className="whitespace-nowrap text-xs">{r[c]}</Td>)}</tr>)}</tbody></Table>
          </div>
        </Card>
      )}
    </div>
  );
}

export function RunSummary({ s }: { s: ImportRunSummary }) {
  const [open, setOpen] = useState(false);
  return (
    <Card>
      <CardHeader title={<span>Run summary: {s.file_name} <Badge variant={RUN_V[s.status]} className="ml-1">{s.status}</Badge></span>} description={`${fmtDateTime(s.started_at)} by ${s.triggered_by}`} actions={<>
        {s.rejected > 0 && <Button size="sm" variant="outline" onClick={() => download(`/api/imports/${s.id}/rejects.csv`)}><Download className="h-4 w-4" /> Reject file</Button>}
        {s.status === "succeeded" && <Button size="sm" variant="outline" onClick={() => setOpen(true)}>Show changes</Button>}
      </>} />
      <CardBody>
        {s.status === "skipped" && <p className="mb-3 text-sm text-muted-foreground">{String(s.counts.message ?? "Nothing changed.")}</p>}
        {s.status === "failed" && <pre className="mb-3 whitespace-pre-wrap rounded bg-red-50 p-2 text-xs text-red-800">{s.error}</pre>}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
          <Stat label="Rows read" value={s.rows_read} />
          <Stat label="New" value={s.new} />
          <Stat label="Changed" value={s.changed} />
          <Stat label="Removed" value={s.removed} />
          <Stat label="Rejected" value={s.rejected} />
          <Stat label="Conflicts" value={s.conflicts} sub="in-app edits kept" />
        </div>
        {s.status === "succeeded" && <p className="mt-2 text-xs text-muted-foreground">People new {s.counts.people_new ?? 0}, changed {s.counts.people_changed ?? 0} · Projects new {s.counts.projects_new ?? 0}, changed {s.counts.projects_changed ?? 0}, linked {s.counts.projects_linked ?? 0} · Alerts raised {s.counts.alerts ?? 0}</p>}
      </CardBody>
      {open && <RunDetail id={s.id} onClose={() => setOpen(false)} />}
    </Card>
  );
}

function RunDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery<any>({ queryKey: ["import-run", id], queryFn: () => api(`/api/imports/${id}`) });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Changes applied by this run" className="max-w-4xl">
        {q.isLoading ? <Spinner /> : (
          <>
            {!!q.data?.rejects.length && <div className="mb-3"><div className="mb-1 text-sm font-semibold">Rejected rows</div><Table><thead><tr><Th>Row</Th><Th>Reason</Th></tr></thead><tbody>{q.data.rejects.map((r: any) => <tr key={r.row_no}><Td>{r.row_no}</Td><Td className="text-xs">{r.reason}</Td></tr>)}</tbody></Table></div>}
            <Table><thead><tr><Th>Entity</Th><Th>Key</Th><Th>Field</Th><Th>Old</Th><Th>New</Th></tr></thead>
              <tbody>{q.data?.changes.map((c: any, i: number) => <tr key={i}><Td className="text-xs">{c.entity}</Td><Td className="text-xs">{c.entity_key}</Td><Td className="text-xs">{c.field ?? c.action}</Td><Td className="text-xs text-muted-foreground">{c.old_value}</Td><Td className="text-xs">{c.new_value}</Td></tr>)}</tbody></Table>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function History() {
  const q = useQuery<ImportRunSummary[]>({ queryKey: ["imports"], queryFn: () => api("/api/imports") });
  const [sel, setSel] = useState<ImportRunSummary | null>(null);
  return (
    <div className="space-y-4">
      <Card>
        <ErrorOr q={q}>
          {!q.data?.length ? <Empty>No imports yet.</Empty> : (
            <Table>
              <thead><tr><Th>Started</Th><Th>File</Th><Th>Source</Th><Th>Status</Th><Th>Read</Th><Th>New</Th><Th>Changed</Th><Th>Removed</Th><Th>Rejected</Th><Th>Conflicts</Th><Th>By</Th></tr></thead>
              <tbody>
                {q.data.map((r) => (
                  <tr key={r.id} className="cursor-pointer hover:bg-muted/40" onClick={() => setSel(r)}>
                    <Td className="text-xs">{fmtDateTime(r.started_at)}</Td><Td className="text-xs">{r.file_name}</Td><Td className="text-xs">{r.source}</Td>
                    <Td><Badge variant={RUN_V[r.status]}>{r.status}</Badge></Td>
                    <Td>{r.rows_read}</Td><Td>{r.new}</Td><Td>{r.changed}</Td><Td>{r.removed}</Td><Td>{r.rejected}</Td><Td>{r.conflicts}</Td><Td className="text-xs">{r.triggered_by}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </ErrorOr>
      </Card>
      {sel && <RunSummary s={sel} />}
    </div>
  );
}

function Conflicts() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("open");
  const q = useQuery<any[]>({ queryKey: ["conflicts", status], queryFn: () => api(`/api/imports/conflicts?status=${status}`) });
  const resolve = useMutation({ mutationFn: ({ id, resolution }: { id: string; resolution: string }) => api(`/api/imports/conflicts/${id}`, { method: "PATCH", body: { resolution } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["conflicts"] }) });
  return (
    <Card>
      <CardHeader title="Import conflicts" description="OpenAir changed a value that someone edited in the app. The in-app value was kept; decide here." actions={<Select className="w-36" value={status} onChange={(e) => setStatus(e.target.value)}><option value="open">Open</option><option value="all">All</option></Select>} />
      <ErrorOr q={q}>
        {!q.data?.length ? <Empty>No conflicts.</Empty> : (
          <Table>
            <thead><tr><Th>Person</Th><Th>Project</Th><Th>Booking</Th><Th>Week</Th><Th>Field</Th><Th>In app</Th><Th>OpenAir</Th><Th>Status</Th><Th /></tr></thead>
            <tbody>
              {q.data.map((c) => (
                <tr key={c.id}>
                  <Td>{c.person_name}</Td><Td className="text-xs">{c.project_name}</Td><Td className="text-xs">{c.booking_id}</Td><Td className="text-xs">{c.week_start ? fmtWeek(c.week_start) : "–"}</Td><Td className="text-xs">{c.field}</Td>
                  <Td className="font-medium">{c.app_value}</Td><Td>{c.openair_value}</Td><Td><Badge variant={c.status === "open" ? "amber" : "grey"}>{c.status}</Badge></Td>
                  <Td className="whitespace-nowrap">{c.status === "open" && <><Button size="sm" variant="outline" onClick={() => resolve.mutate({ id: c.id, resolution: "keep_app" })}>Keep app value</Button><Button size="sm" variant="ghost" className="ml-1" onClick={() => resolve.mutate({ id: c.id, resolution: "take_openair" })}>Take OpenAir</Button></>}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </ErrorOr>
      <ErrorBox error={resolve.error} />
    </Card>
  );
}

function PendingOpenAir() {
  const qc = useQueryClient();
  const q = useQuery<any[]>({ queryKey: ["pending-openair"], queryFn: () => api("/api/allocations/pending-openair") });
  const mark = useMutation({ mutationFn: (ids: string[]) => api("/api/allocations/pending-openair/mark-keyed", { body: { ids } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["pending-openair"] }) });
  return (
    <Card>
      <CardHeader title="Pending OpenAir updates" description="In-app changes not yet keyed into OpenAir. Export for whoever keys them in, then mark them done." actions={<>
        <Button size="sm" variant="outline" onClick={() => download("/api/allocations/pending-openair?format=csv")}><Download className="h-4 w-4" /> CSV</Button>
        <Button size="sm" variant="outline" onClick={() => download("/api/export/pending_openair.xlsx")}><Download className="h-4 w-4" /> Excel</Button>
        <Button size="sm" disabled={!q.data?.length} onClick={() => mark.mutate(q.data!.map((r) => r.id))} loading={mark.isPending}>Mark all keyed</Button>
      </>} />
      <ErrorOr q={q}>
        {!q.data?.length ? <Empty>Nothing pending.</Empty> : (
          <Table>
            <thead><tr><Th>Person</Th><Th>OpenAir user</Th><Th>Project</Th><Th>OpenAir project</Th><Th>Booking</Th><Th>Week</Th><Th>App hours</Th><Th>OpenAir hours</Th><Th>State</Th><Th>Changed by</Th></tr></thead>
            <tbody>{q.data.map((r) => <tr key={r.id}><Td>{r.person}</Td><Td className="text-xs">{r.openair_user_id}</Td><Td className="text-xs">{r.project}</Td><Td className="text-xs">{r.openair_project_id ?? "(pipeline)"}</Td><Td className="text-xs">{r.booking_id ?? "new"}</Td><Td className="text-xs">{fmtWeek(r.week_start)}</Td><Td className="font-medium">{r.app_hours}</Td><Td>{r.openair_hours ?? "–"}</Td><Td className="text-xs">{r.state}</Td><Td className="text-xs">{r.changed_by}</Td></tr>)}</tbody>
          </Table>
        )}
      </ErrorOr>
    </Card>
  );
}
