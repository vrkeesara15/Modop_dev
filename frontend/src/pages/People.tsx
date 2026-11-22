import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { api, qs } from "@/api/client";
import type { PersonRow } from "@/api/types";
import { ErrorOr } from "@/components/shared";
import { Badge, Button, Card, Empty, Input, PageHeader, Select, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useGroups, useMe, useTechLevels } from "@/hooks/queries";
import { download, money, pct } from "@/lib/utils";

export default function PeoplePage() {
  const me = useMe().data!;
  const groups = useGroups();
  const levels = useTechLevels();
  const [f, setF] = useState({ group: "", level: "", q: "" });
  const q = useQuery<PersonRow[]>({ queryKey: ["people", f], queryFn: () => api(`/api/people${qs(f)}`) });
  return (
    <div>
      <PageHeader title="People directory" description="Name, group, GL, Tech level, role family, location, skills and standard hours." actions={<Button variant="outline" size="sm" onClick={() => download("/api/export/people.xlsx")}><Download className="h-4 w-4" /> Excel</Button>} />
      <div className="mb-3 flex flex-wrap gap-2">
        <Input className="w-64" placeholder="Search name or email" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
        <Select className="w-56" value={f.group} onChange={(e) => setF({ ...f, group: e.target.value })}><option value="">All groups</option>{groups.data?.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</Select>
        <Select className="w-32" value={f.level} onChange={(e) => setF({ ...f, level: e.target.value })}><option value="">Any level</option>{levels.data?.map((l) => <option key={l.id} value={l.code}>{l.code}</option>)}</Select>
      </div>
      <Card>
        <ErrorOr q={q}>
          {!q.data?.length ? <Empty>No people.</Empty> : (
            <Table>
              <thead><tr><Th>Name</Th><Th>Group</Th><Th>GL</Th><Th>Level</Th><Th>Role family</Th><Th>Location</Th><Th>Skills</Th><Th>Std h</Th><Th>Now</Th><Th>Next 4 wk</Th>{me.can_see_rates && <Th>Rate</Th>}</tr></thead>
              <tbody>
                {q.data.map((p) => (
                  <tr key={p.id} className="hover:bg-muted/40">
                    <Td><Link className="font-medium text-primary hover:underline" to={`/people/${p.id}`}>{p.name}</Link></Td>
                    <Td className="text-xs">{p.group_name}</Td>
                    <Td className="text-xs">{p.gl_name}</Td>
                    <Td>{p.tech_level}</Td>
                    <Td>{p.role_family}</Td>
                    <Td>{p.location}</Td>
                    <Td className="max-w-[260px]"><div className="flex flex-wrap gap-1">{p.skills.map((s) => <Badge key={s.name}>{s.name} {s.proficiency}</Badge>)}</div></Td>
                    <Td>{p.std_hours_week}</Td>
                    <Td><StatusBadge status={p.status_now} compact /></Td>
                    <Td className="text-xs">{pct(p.util_forecast_4w)} <span className="text-muted-foreground">({pct(p.util_confirmed_4w)} conf.)</span></Td>
                    {me.can_see_rates && <Td className="text-xs">{money(p.bill_rate)}</Td>}
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </ErrorOr>
      </Card>
    </div>
  );
}
