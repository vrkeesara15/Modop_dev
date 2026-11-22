import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, Save, Trash2 } from "lucide-react";
import { api, qs } from "@/api/client";
import { ChangeTable, ErrorOr } from "@/components/shared";
import { Badge, Button, Card, CardBody, CardHeader, Dialog, DialogContent, ErrorBox, Field, Input, PageHeader, Select, Table, Tabs, TabsContent, TabsList, TabsTrigger, Td, Th } from "@/components/ui";
import { useGroups, useMe, useTechLevels } from "@/hooks/queries";
import { download } from "@/lib/utils";

export default function AdminPage() {
  const me = useMe().data!;
  const isAdmin = me.roles.includes("admin");
  return (
    <div>
      <PageHeader title="Admin settings" description="Users and roles, groups, thresholds, confidence steps, rate card and the audit log." />
      <Tabs defaultValue="settings">
        <TabsList>
          <TabsTrigger value="settings">Thresholds & settings</TabsTrigger>
          {isAdmin && <TabsTrigger value="users">Users & roles</TabsTrigger>}
          {isAdmin && <TabsTrigger value="groups">Groups</TabsTrigger>}
          {isAdmin && <TabsTrigger value="rates">Rate card</TabsTrigger>}
          <TabsTrigger value="audit">Audit log</TabsTrigger>
        </TabsList>
        <TabsContent value="settings"><SettingsTab isAdmin={isAdmin} /></TabsContent>
        {isAdmin && <TabsContent value="users"><UsersTab /></TabsContent>}
        {isAdmin && <TabsContent value="groups"><GroupsTab /></TabsContent>}
        {isAdmin && <TabsContent value="rates"><RatesTab /></TabsContent>}
        <TabsContent value="audit"><AuditTab /></TabsContent>
      </Tabs>
    </div>
  );
}

function SettingsTab({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const q = useQuery<any>({ queryKey: ["settings"], queryFn: () => api("/api/admin/settings") });
  const [s, setS] = useState<any>(null);
  useEffect(() => { if (q.data) setS(structuredClone(q.data)); }, [q.data]);
  const save = useMutation({
    mutationFn: () => {
      const body: any = { bands: s.bands };
      if (isAdmin) Object.assign(body, { confidence_steps: s.confidence_steps, std_hours_week: Number(s.std_hours_week), visibility_mode: s.visibility_mode, soft_booking_weight: Number(s.soft_booking_weight), spike_threshold: Number(s.spike_threshold), unfilled_alert_days: Number(s.unfilled_alert_days), rolloff_alert_days: Number(s.rolloff_alert_days), role_families: s.role_families, locations: s.locations });
      return api("/api/admin/settings", { method: "PUT", body });
    },
    onSuccess: () => qc.invalidateQueries(),
  });
  if (!s) return <ErrorOr q={q}>{null}</ErrorOr>;
  const b = s.bands;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Utilization status bands" description="Utilization = (confirmed + weighted forecast hours) / capacity" actions={<Button size="sm" onClick={() => save.mutate()} loading={save.isPending}><Save className="h-4 w-4" /> Save</Button>} />
        <CardBody className="grid gap-3 md:grid-cols-4">
          {[["bench", "Bench below"], ["light", "Light below"], ["healthy", "Healthy up to"], ["hot", "Hot up to (then Over-allocated)"]].map(([k, label]) => (
            <Field key={k} label={`${label} (%)`}><Input type="number" value={b[k]} onChange={(e) => setS({ ...s, bands: { ...b, [k]: Number(e.target.value) } })} /></Field>
          ))}
        </CardBody>
        <div className="px-4 pb-3"><ErrorBox error={save.error} />{save.isSuccess && <span className="text-sm text-green-700">Saved. Statuses and alerts were recomputed.</span>}</div>
      </Card>
      {isAdmin && (
        <>
          <Card>
            <CardHeader title="Confidence steps" description="The five values are fixed (so the math stays simple); labels are editable." />
            <CardBody className="grid gap-3 md:grid-cols-5">
              {s.confidence_steps.map((c: any, i: number) => (
                <Field key={c.value} label={`${c.value}%`}><Input value={c.label} onChange={(e) => setS({ ...s, confidence_steps: s.confidence_steps.map((x: any, j: number) => (j === i ? { ...x, label: e.target.value } : x)) })} /></Field>
              ))}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="General" />
            <CardBody className="grid gap-3 md:grid-cols-4">
              <Field label="Standard hours / week"><Input type="number" value={s.std_hours_week} onChange={(e) => setS({ ...s, std_hours_week: e.target.value })} /></Field>
              <Field label="Visibility of hours and rates"><Select value={s.visibility_mode} onChange={(e) => setS({ ...s, visibility_mode: e.target.value })}><option value="open">open (all PLs and GLs)</option><option value="scoped">scoped (project PL, person's GL, SL, Admin)</option></Select></Field>
              <Field label="Soft-booking weight" hint="0-1"><Input type="number" step="0.05" min={0} max={1} value={s.soft_booking_weight} onChange={(e) => setS({ ...s, soft_booking_weight: e.target.value })} /></Field>
              <Field label="Project-spike threshold" hint="share of overload"><Input type="number" step="0.05" min={0} max={1} value={s.spike_threshold} onChange={(e) => setS({ ...s, spike_threshold: e.target.value })} /></Field>
              <Field label="Alert: unfilled demand (days before start)"><Input type="number" value={s.unfilled_alert_days} onChange={(e) => setS({ ...s, unfilled_alert_days: e.target.value })} /></Field>
              <Field label="Alert: roll-off window (days)"><Input type="number" value={s.rolloff_alert_days} onChange={(e) => setS({ ...s, rolloff_alert_days: e.target.value })} /></Field>
              <Field label="Role families" hint="comma separated" className="md:col-span-2"><Input value={s.role_families.join(", ")} onChange={(e) => setS({ ...s, role_families: e.target.value.split(",").map((x: string) => x.trim()).filter(Boolean) })} /></Field>
              <Field label="Locations" hint="comma separated" className="md:col-span-2"><Input value={(s.locations ?? []).join(", ")} onChange={(e) => setS({ ...s, locations: e.target.value.split(",").map((x: string) => x.trim()).filter(Boolean) })} /></Field>
            </CardBody>
          </Card>
        </>
      )}
    </div>
  );
}

function UsersTab() {
  const qc = useQueryClient();
  const groups = useGroups();
  const q = useQuery<any[]>({ queryKey: ["admin-users"], queryFn: () => api("/api/admin/users") });
  const [edit, setEdit] = useState<any>(null);
  return (
    <Card>
      <CardHeader title="Users and roles" description="One person can hold several roles; a GL can also be a PL." actions={<Button size="sm" onClick={() => setEdit({ email: "", name: "", password: "", is_active: true, roles: [] })}><Plus className="h-4 w-4" /> New user</Button>} />
      <ErrorOr q={q}>
        <Table>
          <thead><tr><Th>Name</Th><Th>Email</Th><Th>Roles</Th><Th>GL of</Th><Th>SL of</Th><Th>Active</Th><Th /></tr></thead>
          <tbody>{q.data?.map((u) => <tr key={u.id}><Td>{u.name}</Td><Td className="text-xs">{u.email}</Td><Td>{u.roles.map((r: any, i: number) => <Badge key={i} className="mr-1">{r.role}{r.scope_group ? ` · ${r.scope_group}` : ""}</Badge>)}</Td><Td className="text-xs">{u.gl_of.join(", ")}</Td><Td className="text-xs">{u.sl_of.length ? `${u.sl_of.length} groups` : ""}</Td><Td>{u.is_active ? "Yes" : <Badge variant="grey">disabled</Badge>}</Td><Td><Button size="sm" variant="ghost" onClick={() => setEdit({ ...u, password: "" })}>Edit</Button></Td></tr>)}</tbody>
        </Table>
      </ErrorOr>
      {edit && <UserDialog u={edit} groups={groups.data ?? []} onClose={() => { setEdit(null); qc.invalidateQueries({ queryKey: ["admin-users"] }); }} />}
    </Card>
  );
}

function UserDialog({ u, groups, onClose }: { u: any; groups: any[]; onClose: () => void }) {
  const [f, setF] = useState({ ...u, roles: u.roles.map((r: any) => ({ role: r.role, scope_group_id: r.scope_group_id })) });
  const m = useMutation({
    mutationFn: () => (u.id ? api(`/api/admin/users/${u.id}`, { method: "PUT", body: { name: f.name, is_active: f.is_active, roles: f.roles, password: f.password || undefined } }) : api("/api/admin/users", { body: { email: f.email, name: f.name, password: f.password || null, is_active: f.is_active, roles: f.roles } })),
    onSuccess: onClose,
  });
  const toggle = (role: string) => setF({ ...f, roles: f.roles.some((r: any) => r.role === role) ? f.roles.filter((r: any) => r.role !== role) : [...f.roles, { role, scope_group_id: null }] });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title={u.id ? `Edit ${u.name}` : "New user"} footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button loading={m.isPending} onClick={() => m.mutate()}>Save</Button></>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Email"><Input value={f.email} disabled={!!u.id} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Name"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label={u.id ? "New password" : "Password"} hint="local login only"><Input type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
          <label className="flex items-center gap-2 pt-5 text-sm"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active</label>
          <div className="col-span-2">
            <div className="mb-1 text-xs font-medium text-muted-foreground">Roles</div>
            <div className="flex flex-wrap gap-3 text-sm">{["admin", "sl", "gl", "pl", "member"].map((r) => <label key={r} className="flex items-center gap-1"><input type="checkbox" checked={f.roles.some((x: any) => x.role === r)} onChange={() => toggle(r)} /> {r.toUpperCase()}</label>)}</div>
          </div>
          {f.roles.some((r: any) => r.role === "gl") && (
            <Field label="GL scope group" hint="or set the GL on the group" className="col-span-2">
              <Select value={f.roles.find((r: any) => r.role === "gl")?.scope_group_id ?? ""} onChange={(e) => setF({ ...f, roles: f.roles.map((r: any) => (r.role === "gl" ? { ...r, scope_group_id: e.target.value || null } : r)) })}>
                <option value="">(none)</option>{groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </Select>
            </Field>
          )}
        </div>
        <div className="mt-3"><ErrorBox error={m.error} /></div>
      </DialogContent>
    </Dialog>
  );
}

function GroupsTab() {
  const qc = useQueryClient();
  const groups = useGroups();
  const users = useQuery<any[]>({ queryKey: ["admin-users"], queryFn: () => api("/api/admin/users") });
  const [edit, setEdit] = useState<any>(null);
  const save = useMutation({
    mutationFn: (g: any) => (g.id ? api(`/api/admin/groups/${g.id}`, { method: "PUT", body: g }) : api("/api/admin/groups", { body: g })),
    onSuccess: () => { setEdit(null); qc.invalidateQueries({ queryKey: ["groups"] }); },
  });
  return (
    <Card>
      <CardHeader title="Groups" description="GL and SL assignment" actions={<Button size="sm" onClick={() => setEdit({ name: "", gl_user_id: "", sl_user_id: "", parent_group_id: "" })}><Plus className="h-4 w-4" /> New group</Button>} />
      <Table>
        <thead><tr><Th>Group</Th><Th>GL</Th><Th>SL</Th><Th>Headcount</Th><Th /></tr></thead>
        <tbody>{groups.data?.map((g) => <tr key={g.id}><Td>{g.name}</Td><Td>{g.gl_name}</Td><Td>{g.sl_name}</Td><Td>{g.headcount}</Td><Td><Button size="sm" variant="ghost" onClick={() => setEdit({ ...g })}>Edit</Button></Td></tr>)}</tbody>
      </Table>
      {edit && (
        <Dialog open onOpenChange={(v) => !v && setEdit(null)}>
          <DialogContent title={edit.id ? "Edit group" : "New group"} footer={<><Button variant="outline" onClick={() => setEdit(null)}>Cancel</Button><Button loading={save.isPending} onClick={() => save.mutate({ id: edit.id, name: edit.name, gl_user_id: edit.gl_user_id || null, sl_user_id: edit.sl_user_id || null, parent_group_id: edit.parent_group_id || null })}>Save</Button></>}>
            <div className="grid gap-3">
              <Field label="Name"><Input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <Field label="Group Leader"><Select value={edit.gl_user_id ?? ""} onChange={(e) => setEdit({ ...edit, gl_user_id: e.target.value })}><option value="">(none)</option>{users.data?.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</Select></Field>
              <Field label="Senior Leader"><Select value={edit.sl_user_id ?? ""} onChange={(e) => setEdit({ ...edit, sl_user_id: e.target.value })}><option value="">(none)</option>{users.data?.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</Select></Field>
              <Field label="Parent group"><Select value={edit.parent_group_id ?? ""} onChange={(e) => setEdit({ ...edit, parent_group_id: e.target.value })}><option value="">(none)</option>{groups.data?.filter((g) => g.id !== edit.id).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</Select></Field>
            </div>
            <div className="mt-3"><ErrorBox error={save.error} /></div>
          </DialogContent>
        </Dialog>
      )}
    </Card>
  );
}

function RatesTab() {
  const qc = useQueryClient();
  const levels = useTechLevels();
  const q = useQuery<any[]>({ queryKey: ["rates"], queryFn: () => api("/api/admin/rate-cards") });
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => { if (q.data) setRows(q.data.map((r) => ({ ...r }))); }, [q.data]);
  const save = useMutation({ mutationFn: () => api("/api/admin/rate-cards", { method: "PUT", body: rows.map((r) => ({ ...r, rate_usd_hr: Number(r.rate_usd_hr), role_family: r.role_family || null, effective_to: r.effective_to || null })) }), onSuccess: () => qc.invalidateQueries() });
  const [lvl, setLvl] = useState({ code: "", rank: "" });
  const addLevel = useMutation({ mutationFn: () => api("/api/admin/tech-levels", { body: { code: lvl.code, rank: Number(lvl.rank) } }), onSuccess: () => { setLvl({ code: "", rank: "" }); qc.invalidateQueries({ queryKey: ["tech-levels"] }); } });
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Rate card" description="Bill rate by Tech level (and optionally role family), effective-dated. A role-specific row wins over the generic one." actions={<>
          <Button size="sm" variant="outline" onClick={() => setRows([...rows, { tech_level_id: levels.data?.[0]?.id, role_family: "", rate_usd_hr: 100, effective_from: new Date().toISOString().slice(0, 10), effective_to: "" }])}><Plus className="h-4 w-4" /> Row</Button>
          <Button size="sm" onClick={() => save.mutate()} loading={save.isPending}><Save className="h-4 w-4" /> Save</Button>
        </>} />
        <Table>
          <thead><tr><Th>Tech level</Th><Th>Role family</Th><Th>USD / hour</Th><Th>Effective from</Th><Th>Effective to</Th><Th /></tr></thead>
          <tbody>
            {rows.map((r, i) => {
              const up = (k: string, v: any) => setRows(rows.map((x, j) => (j === i ? { ...x, [k]: v } : x)));
              return (
                <tr key={r.id ?? `n${i}`}>
                  <Td><Select value={r.tech_level_id} onChange={(e) => up("tech_level_id", e.target.value)}>{levels.data?.map((l) => <option key={l.id} value={l.id}>{l.code}</option>)}</Select></Td>
                  <Td><Input value={r.role_family ?? ""} placeholder="(all)" onChange={(e) => up("role_family", e.target.value)} /></Td>
                  <Td><Input type="number" value={r.rate_usd_hr} onChange={(e) => up("rate_usd_hr", e.target.value)} /></Td>
                  <Td><Input type="date" value={r.effective_from} onChange={(e) => up("effective_from", e.target.value)} /></Td>
                  <Td><Input type="date" value={r.effective_to ?? ""} onChange={(e) => up("effective_to", e.target.value)} /></Td>
                  <Td><Button size="icon" variant="ghost" aria-label="Remove row" onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></Button></Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
        <div className="p-3"><ErrorBox error={save.error} /></div>
      </Card>
      <Card>
        <CardHeader title="Tech levels" description="As defined in OpenAir" />
        <CardBody className="flex flex-wrap items-end gap-2">
          {levels.data?.map((l) => <Badge key={l.id}>{l.code} · rank {l.rank}</Badge>)}
          <Input className="w-24" placeholder="T8" value={lvl.code} onChange={(e) => setLvl({ ...lvl, code: e.target.value })} />
          <Input className="w-24" placeholder="rank" type="number" value={lvl.rank} onChange={(e) => setLvl({ ...lvl, rank: e.target.value })} />
          <Button size="sm" variant="outline" disabled={!lvl.code || !lvl.rank} onClick={() => addLevel.mutate()}>Add level</Button>
          <ErrorBox error={addLevel.error} />
        </CardBody>
      </Card>
    </div>
  );
}

function AuditTab() {
  const [f, setF] = useState({ entity: "", actor: "", q: "", frm: "", to: "" });
  const q = useQuery<any[]>({ queryKey: ["audit", f], queryFn: () => api(`/api/audit${qs({ ...f, limit: 500 })}`) });
  return (
    <Card>
      <CardHeader title="Audit log" description="Every edit and import, attributable to a user or an import run." actions={<Button size="sm" variant="outline" onClick={() => download(`/api/export/audit.xlsx${qs(f)}`)}><Download className="h-4 w-4" /> Excel</Button>} />
      <CardBody className="grid gap-2 md:grid-cols-5">
        <Field label="Entity"><Select value={f.entity} onChange={(e) => setF({ ...f, entity: e.target.value })}><option value="">All</option>{["allocation", "allocation_week", "person", "person_skill", "project", "demand_line", "request", "setting", "rate_card", "user", "user_roles", "group", "column_mapping", "import_conflict", "availability_flag", "capacity_exception"].map((e) => <option key={e}>{e}</option>)}</Select></Field>
        <Field label="Actor"><Input value={f.actor} onChange={(e) => setF({ ...f, actor: e.target.value })} placeholder="email or import" /></Field>
        <Field label="Contains"><Input value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} /></Field>
        <Field label="From"><Input type="date" value={f.frm} onChange={(e) => setF({ ...f, frm: e.target.value })} /></Field>
        <Field label="To"><Input type="date" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} /></Field>
      </CardBody>
      <ChangeTable rows={q.data} loading={q.isLoading} />
    </Card>
  );
}
