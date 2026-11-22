import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AgGridReact } from "ag-grid-react";
import type { CellClassParams, CellKeyDownEvent, CellValueChangedEvent, ColDef, GridApi, ValueFormatterParams } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import { ArrowRightLeft, Download, History, PaintBucket, Plus, Undo2 } from "lucide-react";
import { api, qs } from "@/api/client";
import { ChangeTable } from "@/components/shared";
import { Badge, Button, Card, CardHeader, Dialog, DialogContent, ErrorBox, Field, Input, PageHeader, Select, Spinner } from "@/components/ui";
import { useGroups, useMe, useProjects } from "@/hooks/queries";
import { addWeeks, bandClass, download, fmtWeek, pct, STATUS_LABELS, thisMonday } from "@/lib/utils";

type Row = {
  row_id: string;
  allocation_id: string | null;
  allocation_ids?: string[];
  week_alloc?: Record<string, string>;
  task_name?: string | null;
  person_id: string;
  person_name: string;
  group_name: string | null;
  tech_level: string | null;
  project_id: string | null;
  project_name: string;
  client: string | null;
  state: string;
  confidence?: number;
  billable: boolean;
  editable: boolean;
  source: string | null;
  edited_in_app?: boolean;
  weeks: Record<string, number>;
  total: number;
};
const allocFor = (r: Row, wk: string) => r.week_alloc?.[wk] ?? r.allocation_id!;
const idsOf = (r: Row) => r.allocation_ids ?? (r.allocation_id ? [r.allocation_id] : []);
type Edit = { allocation_id: string; week_start: string; hours: number; old: number };

export default function GridPage() {
  const [sp, setSp] = useSearchParams();
  const me = useMe().data!;
  const groups = useGroups();
  const projects = useProjects();
  const qc = useQueryClient();
  const [start, setStart] = useState(sp.get("from") ?? thisMonday());
  const [weeks, setWeeks] = useState(12);
  const group = sp.get("group") ?? "";
  const project = sp.get("project") ?? "";
  const person = sp.get("person") ?? "";
  const [showReleased, setShowReleased] = useState(false);
  const params = { group, project, person, frm: start, to: addWeeks(start, weeks - 1), show_released: showReleased };
  const q = useQuery<any>({ queryKey: ["grid", params], queryFn: () => api(`/api/allocations/grid${qs(params)}`) });
  const [rows, setRows] = useState<Row[]>([]);
  const [status, setStatus] = useState<Record<string, Record<string, any>>>({});
  const [undo, setUndo] = useState<Edit[][]>([]);
  const [selected, setSelected] = useState<Row | null>(null);
  const [dialog, setDialog] = useState<"bulk" | "reassign" | "add" | "history" | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const gridRef = useRef<AgGridReact<Row>>(null);

  useEffect(() => {
    if (q.data) {
      setRows(q.data.rows.map((r: Row) => ({ ...r, weeks: { ...r.weeks } })));
      setStatus(q.data.person_status);
    }
  }, [q.data]);

  const weekKeys: string[] = q.data?.weeks ?? [];

  const save = useCallback(
    async (edits: Edit[], recordUndo = true, reason?: string) => {
      if (!edits.length) return;
      setSaving(true);
      setErr(null);
      try {
        const r = await api("/api/allocations/weeks", { method: "PATCH", body: { edits: edits.map(({ allocation_id, week_start, hours }) => ({ allocation_id, week_start, hours })), reason } });
        setStatus((s) => ({ ...s, ...r.person_status }));
        if (recordUndo) setUndo((u) => [...u.slice(-49), edits]);
        qc.invalidateQueries({ queryKey: ["alerts"] });
        qc.invalidateQueries({ queryKey: ["dash"] });
        qc.invalidateQueries({ queryKey: ["heatmap"] });
      } catch (e) {
        setErr(e);
        q.refetch();
      } finally {
        setSaving(false);
      }
    },
    [qc, q],
  );

  function applyLocal(edits: { allocation_id: string; week_start: string; hours: number }[]) {
    setRows((rs) =>
      rs.map((r) => {
        const ids = idsOf(r);
        const mine = edits.filter((e) => ids.includes(e.allocation_id) && (r.week_alloc?.[e.week_start] ?? r.allocation_id) === e.allocation_id);
        if (!mine.length) return r;
        const w = { ...r.weeks };
        mine.forEach((e) => (w[e.week_start] = e.hours));
        return { ...r, weeks: w, total: Object.values(w).reduce((a, b) => a + b, 0) };
      }),
    );
  }

  function onCellValueChanged(e: CellValueChangedEvent<Row>) {
    const wk = e.colDef.field?.replace("weeks.", "");
    if (!wk || !e.data.allocation_id) return;
    const v = Number(e.newValue);
    if (Number.isNaN(v) || v < 0 || v > 80) {
      setErr(new Error("Hours must be a number between 0 and 80"));
      q.refetch();
      return;
    }
    const old = Number(e.oldValue ?? 0);
    if (v === old) return;
    const aid = allocFor(e.data, wk);
    applyLocal([{ allocation_id: aid, week_start: wk, hours: v }]);
    save([{ allocation_id: aid, week_start: wk, hours: v, old }]);
  }

  function doUndo() {
    const last = undo[undo.length - 1];
    if (!last) return;
    setUndo((u) => u.slice(0, -1));
    const inv = last.map((e) => ({ ...e, hours: e.old, old: e.hours }));
    applyLocal(inv);
    save(inv, false, "undo");
  }

  // Excel-like copy / paste of ranges (TSV), starting at the focused cell.
  const onPaste = useCallback(
    (ev: ClipboardEvent) => {
      const apiG: GridApi<Row> | undefined = gridRef.current?.api;
      const cell = apiG?.getFocusedCell();
      if (!apiG || !cell || document.activeElement?.tagName === "INPUT") return;
      const text = ev.clipboardData?.getData("text/plain");
      if (!text) return;
      ev.preventDefault();
      const grid = text.replace(/\r/g, "").replace(/\n$/, "").split("\n").map((l) => l.split("\t"));
      const cols = apiG.getAllDisplayedColumns();
      const c0 = cols.findIndex((c) => c.getColId() === cell.column.getColId());
      const edits: Edit[] = [];
      grid.forEach((vals, i) => {
        const node = apiG.getDisplayedRowAtIndex(cell.rowIndex + i);
        const r = node?.data;
        if (!r || !r.editable || !r.allocation_id) return;
        vals.forEach((v, j) => {
          const col = cols[c0 + j];
          const f = col?.getColDef().field;
          if (!f?.startsWith("weeks.")) return;
          const wk = f.slice(6);
          const n = Number(v.trim() || 0);
          if (Number.isNaN(n) || n < 0 || n > 80) return;
          edits.push({ allocation_id: allocFor(r, wk), week_start: wk, hours: n, old: r.weeks[wk] ?? 0 });
        });
      });
      if (edits.length) {
        applyLocal(edits);
        save(edits, true, "paste");
      }
    },
    [save],
  );
  useEffect(() => {
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [onPaste]);

  function onCellKeyDown(e: CellKeyDownEvent<Row>) {
    const ke = e.event as KeyboardEvent | undefined;
    if (!ke) return;
    if ((ke.ctrlKey || ke.metaKey) && ke.key.toLowerCase() === "z") {
      ke.preventDefault();
      doUndo();
    }
    if ((ke.ctrlKey || ke.metaKey) && ke.key.toLowerCase() === "c" && e.data) {
      const f = e.colDef.field ?? "";
      let txt = String(e.value ?? "");
      if (ke.shiftKey && f.startsWith("weeks.")) {
        const from = weekKeys.indexOf(f.slice(6));
        txt = weekKeys.slice(from).map((w) => e.data!.weeks[w] ?? 0).join("\t");
      }
      navigator.clipboard?.writeText(txt);
    }
  }

  const cellClass = useCallback(
    (p: CellClassParams<Row>) => {
      if (p.node.rowPinned) return "";
      const wk = p.colDef.field!.slice(6);
      const st = status[p.data!.person_id]?.[wk];
      const cls = [st ? bandClass(st.status_forecast) : ""];
      if (!p.data!.billable) cls.push("cell-nonbillable");
      return cls.join(" ");
    },
    [status],
  );

  const cols = useMemo<ColDef<Row>[]>(() => {
    const base: ColDef<Row>[] = [
      {
        headerName: "Person",
        field: "person_name",
        pinned: "left",
        width: 190,
        cellRenderer: (p: any) => (p.node.rowPinned ? <b>{p.value}</b> : <Link className="text-primary hover:underline" to={`/people/${p.data.person_id}`}>{p.value}</Link>),
      },
      { headerName: "Lvl", field: "tech_level", pinned: "left", width: 62 },
      {
        headerName: "Project",
        field: "project_name",
        pinned: "left",
        width: 210,
        cellRenderer: (p: any) => p.node.rowPinned ? null : (
          <span className={p.data.billable ? "" : "text-muted-foreground"}>
            {p.data.project_id ? <Link className="hover:underline" to={`/projects/${p.data.project_id}`}>{p.value}</Link> : p.value}
            {!p.data.billable && <span className="ml-1 text-[10px]">(non-billable)</span>}
          </span>
        ),
      },
      {
        headerName: "State",
        field: "state",
        width: 112,
        cellRenderer: (p: any) => p.node.rowPinned ? null : <Badge variant={p.value === "confirmed" ? "green" : p.value === "requested" ? "amber" : p.value === "proposed" ? "blue" : "grey"}>{p.value}{p.data.confidence !== undefined && p.data.confidence < 100 ? ` ${p.data.confidence}%` : ""}</Badge>,
      },
    ];
    const wcols: ColDef<Row>[] = weekKeys.map((w) => ({
      headerName: fmtWeek(w),
      headerTooltip: `Week of ${w}`,
      field: `weeks.${w}` as any,
      width: 78,
      type: "numericColumn",
      editable: (p) => !!p.data?.editable && !p.node.rowPinned,
      valueGetter: (p) => (p.node?.rowPinned ? (p.data as any)?.weeks?.[w] : p.data?.weeks[w] ?? 0),
      valueSetter: (p) => {
        const v = Number(p.newValue);
        if (Number.isNaN(v)) return false;
        p.data.weeks[w] = v;
        return true;
      },
      valueFormatter: (p: ValueFormatterParams) => (p.value === null || p.value === undefined || p.value === "" ? "" : typeof p.value === "number" ? (p.value === 0 ? "·" : `${p.value}`) : String(p.value)),
      cellClass,
      tooltipValueGetter: (p) => {
        if (p.node?.rowPinned || !p.data) return "";
        const st = status[p.data.person_id]?.[w];
        return st ? `${p.data.person_name}: ${pct(st.util_forecast)} forecast (${STATUS_LABELS[st.status_forecast]}), ${pct(st.util_confirmed)} confirmed` : "";
      },
    }));
    return [...base, ...wcols, { headerName: "Total", field: "total", width: 84, pinned: "right", valueFormatter: (p) => (typeof p.value === "number" ? p.value.toFixed(0) : p.value) }];
  }, [weekKeys, cellClass, status]);

  const pinned = useMemo(() => {
    if (!weekKeys.length) return [];
    const hours: Record<string, number> = {};
    const cap: Record<string, number> = {};
    const util: Record<string, string> = {};
    weekKeys.forEach((w) => {
      hours[w] = rows.reduce((a, r) => a + (r.weeks[w] ?? 0), 0);
      cap[w] = Object.values(status).reduce((a: number, s: any) => a + (s[w]?.capacity ?? 0), 0);
      util[w] = cap[w] ? `${Math.round((hours[w] / cap[w]) * 100)}%` : "–";
    });
    const tot = Object.values(hours).reduce((a, b) => a + b, 0);
    return [
      { row_id: "f-hours", person_name: "Total hours", weeks: hours, total: tot },
      { row_id: "f-cap", person_name: "Capacity", weeks: cap, total: Object.values(cap).reduce((a, b) => a + b, 0) },
      { row_id: "f-util", person_name: "Utilization", weeks: util, total: "" },
    ] as any[];
  }, [rows, status, weekKeys]);

  const canAdd = me.roles.includes("admin") || me.gl_group_ids.length > 0;
  const setParam = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n);
  };

  return (
    <div>
      <PageHeader
        title="Allocation grid"
        description="People × projects × weeks. Edit hours inline (Tab/Enter to move, paste ranges from Excel, Ctrl+Z to undo). Cell colour is the person's status band for that week."
        actions={<>
          <Button variant="outline" size="sm" onClick={doUndo} disabled={!undo.length}><Undo2 className="h-4 w-4" /> Undo</Button>
          <Button variant="outline" size="sm" disabled={!selected?.editable} onClick={() => setDialog("bulk")}><PaintBucket className="h-4 w-4" /> Bulk fill</Button>
          <Button variant="outline" size="sm" disabled={!selected?.editable} onClick={() => setDialog("reassign")}><ArrowRightLeft className="h-4 w-4" /> Reassign</Button>
          <Button variant="outline" size="sm" disabled={!selected?.allocation_id} onClick={() => setDialog("history")}><History className="h-4 w-4" /> History</Button>
          {canAdd && <Button size="sm" onClick={() => setDialog("add")}><Plus className="h-4 w-4" /> Add allocation</Button>}
          <Button variant="outline" size="sm" onClick={() => download(`/api/export/grid.xlsx${qs(params)}`)}><Download className="h-4 w-4" /> Excel</Button>
        </>}
      />
      <div className="mb-3 flex flex-wrap items-end gap-2">
        <Field label="Group"><Select className="w-56" value={group} onChange={(e) => setParam("group", e.target.value)}><option value="">My scope</option>{groups.data?.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</Select></Field>
        <Field label="Project"><Select className="w-56" value={project} onChange={(e) => setParam("project", e.target.value)}><option value="">All</option>{projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></Field>
        <Field label="From week"><Input type="date" className="w-40" value={start} onChange={(e) => e.target.value && setStart(e.target.value)} /></Field>
        <Field label="Weeks"><Select className="w-24" value={weeks} onChange={(e) => setWeeks(Number(e.target.value))}>{[4, 8, 12, 16, 26].map((n) => <option key={n} value={n}>{n}</option>)}</Select></Field>
        <label className="flex items-center gap-1.5 pb-2 text-sm"><input type="checkbox" checked={showReleased} onChange={(e) => setShowReleased(e.target.checked)} /> Show released / declined</label>
        {person && <Button variant="ghost" size="sm" onClick={() => setParam("person", "")}>Clear person filter</Button>}
        <div className="ml-auto pb-2 text-xs text-muted-foreground">{saving ? "Saving…" : selected ? `Selected: ${selected.person_name} · ${selected.project_name}` : "Click a row to select it"}</div>
      </div>
      <ErrorBox error={err} />
      <Card className="mt-2">
        {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : (
          <div className="ag-theme-quartz" style={{ height: "calc(100vh - 290px)", minHeight: 420 }}>
            <AgGridReact<Row>
              ref={gridRef}
              rowData={rows}
              columnDefs={cols}
              pinnedBottomRowData={pinned}
              getRowId={(p) => p.data.row_id}
              rowSelection="single"
              onSelectionChanged={(e) => setSelected(e.api.getSelectedRows()[0] ?? null)}
              onCellValueChanged={onCellValueChanged}
              onCellKeyDown={onCellKeyDown}
              singleClickEdit={false}
              stopEditingWhenCellsLoseFocus
              enterNavigatesVertically
              enterNavigatesVerticallyAfterEdit
              tooltipShowDelay={400}
              defaultColDef={{ sortable: true, resizable: true, suppressMovable: true }}
              overlayNoRowsTemplate="No allocations in this scope. Pick a group or project above."
            />
          </div>
        )}
      </Card>
      {dialog === "bulk" && selected && <BulkDialog row={selected} start={start} onClose={() => setDialog(null)} onDone={(e) => { setDialog(null); if (e) setUndo((u) => [...u, e]); q.refetch(); }} />}
      {dialog === "reassign" && selected && <ReassignDialog row={selected} start={start} onClose={() => setDialog(null)} onDone={() => { setDialog(null); setSelected(null); q.refetch(); }} />}
      {dialog === "add" && <AddDialog start={start} onClose={() => setDialog(null)} onDone={() => { setDialog(null); q.refetch(); }} />}
      {dialog === "history" && selected?.allocation_id && <HistoryDialog row={selected} onClose={() => setDialog(null)} />}
    </div>
  );
}

function BulkDialog({ row, start, onClose, onDone }: { row: Row; start: string; onClose: () => void; onDone: (undo?: Edit[]) => void }) {
  const [frm, setFrm] = useState(start);
  const [to, setTo] = useState(addWeeks(start, 6));
  const [hours, setHours] = useState("20");
  const [reason, setReason] = useState("");
  const m = useMutation({
    mutationFn: () => api("/api/allocations/bulk", { body: { allocation_id: row.allocation_id, allocation_ids: idsOf(row), frm, to, hours: Number(hours), reason: reason || null } }),
    onSuccess: () => {
      const edits: Edit[] = Object.keys(row.weeks).filter((w) => w >= frm && w <= to).map((w) => ({ allocation_id: allocFor(row, w), week_start: w, hours: Number(hours), old: row.weeks[w] ?? 0 }));
      onDone(edits);
    },
  });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Bulk fill hours" description={`${row.person_name} on ${row.project_name}. Sets the same hours for every week touching the range.`} footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button loading={m.isPending} onClick={() => m.mutate()}>Apply</Button></>}>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Hours / week"><Input type="number" min={0} max={80} value={hours} onChange={(e) => setHours(e.target.value)} /></Field>
          <Field label="From"><Input type="date" value={frm} onChange={(e) => setFrm(e.target.value)} /></Field>
          <Field label="To"><Input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
          <Field label="Reason" hint="optional" className="col-span-3"><Input value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        </div>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}

function ReassignDialog({ row, start, onClose, onDone }: { row: Row; start: string; onClose: () => void; onDone: () => void }) {
  const [mode, setMode] = useState<"project" | "person">("project");
  const [target, setTarget] = useState("");
  const [frm, setFrm] = useState(start);
  const [to, setTo] = useState(addWeeks(start, 11));
  const [reason, setReason] = useState("");
  const projects = useProjects();
  const people = useQuery<any[]>({ queryKey: ["people"], queryFn: () => api("/api/people") });
  const m = useMutation({
    mutationFn: async () => {
      let out: any = null;
      for (const id of idsOf(row)) out = await api("/api/allocations/reassign", { body: { allocation_id: id, [mode === "project" ? "to_project_id" : "to_person_id"]: target, frm, to, reason: reason || null } });
      return out;
    },
    onSuccess: onDone,
  });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Reassign" description={`Move ${row.person_name}'s hours on ${row.project_name} in one action.`} footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button loading={m.isPending} disabled={!target} onClick={() => m.mutate()}>Move hours</Button></>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Move" className="col-span-2">
            <Select value={mode} onChange={(e) => { setMode(e.target.value as any); setTarget(""); }}>
              <option value="project">{row.person_name}'s hours to another project</option>
              <option value="person">This line to another person (same project)</option>
            </Select>
          </Field>
          <Field label={mode === "project" ? "Target project" : "Target person"} className="col-span-2">
            <Select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">Choose…</option>
              {mode === "project"
                ? projects.data?.filter((p) => p.id !== row.project_id && !["lost", "closed"].includes(p.status)).map((p) => <option key={p.id} value={p.id}>{p.name} ({p.status})</option>)
                : people.data?.filter((p) => p.id !== row.person_id).map((p) => <option key={p.id} value={p.id}>{p.name} · {p.tech_level} · {p.group_name}</option>)}
            </Select>
          </Field>
          <Field label="From week"><Input type="date" value={frm} onChange={(e) => setFrm(e.target.value)} /></Field>
          <Field label="To week"><Input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
          <Field label="Reason" hint="optional" className="col-span-2"><Input value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        </div>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}

function AddDialog({ start, onClose, onDone }: { start: string; onClose: () => void; onDone: () => void }) {
  const me = useMe().data!;
  const projects = useProjects();
  const people = useQuery<any[]>({ queryKey: ["people"], queryFn: () => api("/api/people") });
  const [personId, setPersonId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [hours, setHours] = useState("20");
  const [frm, setFrm] = useState(start);
  const [to, setTo] = useState(addWeeks(start, 7));
  const m = useMutation({ mutationFn: () => api("/api/allocations", { body: { person_id: personId, project_id: projectId, hours_per_week: Number(hours), frm, to } }), onSuccess: onDone });
  const mine = people.data?.filter((p) => me.roles.includes("admin") || p.can_edit) ?? [];
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Add allocation" description="GLs allocate their own people directly. PLs request people from the Availability board instead." footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button loading={m.isPending} disabled={!personId || !projectId} onClick={() => m.mutate()}>Add</Button></>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Person" className="col-span-2"><Select value={personId} onChange={(e) => setPersonId(e.target.value)}><option value="">Choose…</option>{mine.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.tech_level}</option>)}</Select></Field>
          <Field label="Project" className="col-span-2"><Select value={projectId} onChange={(e) => setProjectId(e.target.value)}><option value="">Choose…</option>{projects.data?.filter((p) => !["lost", "closed"].includes(p.status)).map((p) => <option key={p.id} value={p.id}>{p.name} ({p.status})</option>)}</Select></Field>
          <Field label="Hours / week"><Input type="number" min={1} max={80} value={hours} onChange={(e) => setHours(e.target.value)} /></Field>
          <div />
          <Field label="From week"><Input type="date" value={frm} onChange={(e) => setFrm(e.target.value)} /></Field>
          <Field label="To week"><Input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
        </div>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}

function HistoryDialog({ row, onClose }: { row: Row; onClose: () => void }) {
  const ids = idsOf(row);
  const q = useQuery<any[]>({
    queryKey: ["alloc-history", ...ids],
    queryFn: async () => (await Promise.all(ids.map((id) => api<any[]>(`/api/allocations/${id}/history`)))).flat().sort((a, b) => (a.at < b.at ? 1 : -1)),
  });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Change history" description={`${row.person_name} · ${row.project_name}`} className="max-w-4xl">
        <Card><CardHeader title="Every edit: who, when, old value, new value, reason" /><ChangeTable rows={q.data} loading={q.isLoading} /></Card>
      </DialogContent>
    </Dialog>
  );
}
