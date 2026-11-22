import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Download, Flag, Send } from "lucide-react";
import { api, qs } from "@/api/client";
import type { AvailabilityPerson } from "@/api/types";
import { ErrorOr, RequestDialog } from "@/components/shared";
import { Badge, Button, Card, Empty, Field, Input, PageHeader, Select, StatusBadge, Table, Td, Th, Toggle } from "@/components/ui";
import { useGroups, useMe, useSettings, useSkills, useTechLevels } from "@/hooks/queries";
import { addWeeks, download, fmtWeek, hrs, money, thisMonday } from "@/lib/utils";

export default function AvailabilityPage() {
  const me = useMe().data!;
  const levels = useTechLevels();
  const groups = useGroups();
  const skills = useSkills();
  const settings = useSettings();
  const [f, setF] = useState({ available_from: thisMonday(), available_to: addWeeks(thisMonday(), 3), level: "", role_family: "", skill: "", group: "", location: "", min_free: "8" });
  const [view, setView] = useState("cards");
  const [req, setReq] = useState<{ p: AvailabilityPerson; kind: "request" | "offer" } | null>(null);
  const q = useQuery<{ weeks: string[]; people: AvailabilityPerson[] }>({ queryKey: ["availability", f], queryFn: () => api(`/api/people${qs(f)}`) });
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));
  const canRequest = me.roles.some((r) => ["pl", "admin"].includes(r));
  const canOffer = (p: AvailabilityPerson) => me.roles.includes("admin") || (p.group_id ? me.gl_group_ids.includes(p.group_id) : false);

  return (
    <div>
      <PageHeader
        title="Availability board"
        description="People with free hours in the chosen weeks. Open to every PL and GL."
        actions={<>
          <Toggle label="View" value={view} onChange={setView} options={[{ value: "cards", label: "Cards" }, { value: "table", label: "Table" }]} />
          <Button variant="outline" size="sm" onClick={() => download(`/api/export/availability.xlsx${qs(f)}`)}><Download className="h-4 w-4" /> Excel</Button>
        </>}
      />
      <Card className="mb-4 p-3">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <Field label="From week"><Input type="date" value={f.available_from} onChange={(e) => set("available_from", e.target.value)} /></Field>
          <Field label="To week"><Input type="date" value={f.available_to} onChange={(e) => set("available_to", e.target.value)} /></Field>
          <Field label="Tech level"><Select value={f.level} onChange={(e) => set("level", e.target.value)}><option value="">Any</option>{levels.data?.map((l) => <option key={l.id} value={l.code}>{l.code}</option>)}</Select></Field>
          <Field label="Role family"><Select value={f.role_family} onChange={(e) => set("role_family", e.target.value)}><option value="">Any</option>{(settings.data?.role_families ?? []).map((r: string) => <option key={r}>{r}</option>)}</Select></Field>
          <Field label="Skill"><Select value={f.skill} onChange={(e) => set("skill", e.target.value)}><option value="">Any</option>{skills.data?.map((s) => <option key={s.id}>{s.name}</option>)}</Select></Field>
          <Field label="Group"><Select value={f.group} onChange={(e) => set("group", e.target.value)}><option value="">Any</option>{groups.data?.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</Select></Field>
          <Field label="Location"><Select value={f.location} onChange={(e) => set("location", e.target.value)}><option value="">Any</option>{(settings.data?.locations ?? []).map((l: string) => <option key={l}>{l}</option>)}</Select></Field>
          <Field label="Free hours ≥" hint="avg / wk"><Input type="number" min={0} value={f.min_free} onChange={(e) => set("min_free", e.target.value)} /></Field>
        </div>
      </Card>
      <ErrorOr q={q}>
        {!q.data?.people.length ? <Card><Empty>Nobody matches. Try widening the filters.</Empty></Card> : view === "cards" ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {q.data.people.map((p) => (
              <Card key={p.person_id} className="flex flex-col p-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <Link to={`/people/${p.person_id}`} className="font-semibold text-primary hover:underline">{p.name}</Link>
                    <div className="text-xs text-muted-foreground">{p.tech_level} · {p.role_family} · {p.group_name} · {p.location}</div>
                  </div>
                  <StatusBadge status={p.status} />
                </div>
                <div className="mt-2 flex flex-wrap gap-1">{p.skills.map((s) => <Badge key={s.name} variant={f.skill && s.name === f.skill ? "blue" : "default"}>{s.name} {"●".repeat(s.proficiency)}</Badge>)}</div>
                <div className="mt-3 flex items-end gap-1" aria-label="Free hours per week">
                  {q.data!.weeks.map((w) => {
                    const v = p.free_by_week[w] ?? 0;
                    return (
                      <div key={w} className="flex flex-1 flex-col items-center" title={`${fmtWeek(w)}: ${v} h free`}>
                        <div className="w-full rounded-t bg-green-500/70" style={{ height: `${Math.min(40, v)}px` }} />
                        <div className="mt-0.5 text-[10px] text-muted-foreground">{fmtWeek(w)}</div>
                        <div className="text-[10px] font-medium">{v}h</div>
                      </div>
                    );
                  })}
                </div>
                {p.flags.map((fl, i) => <div key={i} className="mt-2 flex items-center gap-1 text-xs text-amber-800"><Flag className="h-3 w-3" /> Available from {fmtWeek(fl.from_date)} for {fl.hours_per_week} h/wk {fl.note ? `· ${fl.note}` : ""}</div>)}
                <div className="mt-3 flex items-center justify-between border-t border-border pt-3">
                  <div className="text-xs"><b>{hrs(p.free_hours_avg)}</b> free/wk avg{p.bill_rate !== undefined && <span className="ml-2 text-muted-foreground">{money(p.bill_rate)}</span>}</div>
                  <div className="flex gap-1">
                    {canOffer(p) && <Button size="sm" variant="outline" onClick={() => setReq({ p, kind: "offer" })}>Offer</Button>}
                    {canRequest && <Button size="sm" onClick={() => setReq({ p, kind: "request" })}><Send className="h-3.5 w-3.5" /> Request</Button>}
                  </div>
                </div>
              </Card>
            ))}
          </div>
        ) : (
          <Card>
            <Table>
              <thead><tr><Th>Person</Th><Th>Level</Th><Th>Role</Th><Th>Group</Th><Th>Skills</Th>{q.data.weeks.map((w) => <Th key={w}>{fmtWeek(w)}</Th>)}<Th>Avg free</Th>{me.can_see_rates && <Th>Rate</Th>}<Th /></tr></thead>
              <tbody>
                {q.data.people.map((p) => (
                  <tr key={p.person_id}>
                    <Td><Link className="text-primary hover:underline" to={`/people/${p.person_id}`}>{p.name}</Link></Td>
                    <Td>{p.tech_level}</Td><Td>{p.role_family}</Td><Td className="text-xs">{p.group_name}</Td>
                    <Td className="text-xs">{p.skills.map((s) => `${s.name}(${s.proficiency})`).join(", ")}</Td>
                    {q.data!.weeks.map((w) => <Td key={w}>{p.free_by_week[w]}</Td>)}
                    <Td className="font-medium">{hrs(p.free_hours_avg)}</Td>
                    {me.can_see_rates && <Td>{money(p.bill_rate)}</Td>}
                    <Td>{canRequest && <Button size="sm" onClick={() => setReq({ p, kind: "request" })}>Request</Button>}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        )}
      </ErrorOr>
      {req && <RequestDialog open onOpenChange={(v) => !v && setReq(null)} person={req.p} kind={req.kind} from={f.available_from} to={f.available_to} />}
    </div>
  );
}
