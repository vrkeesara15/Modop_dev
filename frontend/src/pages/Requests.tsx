import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { api } from "@/api/client";
import { ErrorOr } from "@/components/shared";
import { Badge, Button, Card, Dialog, DialogContent, Empty, ErrorBox, Field, Input, PageHeader, Table, Tabs, TabsContent, TabsList, TabsTrigger, Td, Th } from "@/components/ui";
import { useMe } from "@/hooks/queries";
import { fmtDateTime, fmtWeek } from "@/lib/utils";

const SV: Record<string, any> = { pending: "amber", accepted: "green", declined: "red", confirmed: "green", cancelled: "grey" };

export default function RequestsPage() {
  const me = useMe().data!;
  return (
    <div>
      <PageHeader title="Requests inbox" description="A match is Confirmed only when the person's GL and the project's PL have both accepted. Either can decline with a reason. Admin can override." />
      <Tabs defaultValue="inbox">
        <TabsList>
          <TabsTrigger value="inbox">Waiting for me</TabsTrigger>
          <TabsTrigger value="sent">Sent by me</TabsTrigger>
          <TabsTrigger value="all">All involving me</TabsTrigger>
        </TabsList>
        {["inbox", "sent", "all"].map((b) => <TabsContent key={b} value={b}><RequestList box={b} isAdmin={me.roles.includes("admin")} /></TabsContent>)}
      </Tabs>
    </div>
  );
}

function RequestList({ box, isAdmin }: { box: string; isAdmin: boolean }) {
  const qc = useQueryClient();
  const q = useQuery<any[]>({ queryKey: ["requests", box], queryFn: () => api(`/api/requests?box=${box}`) });
  const [decline, setDecline] = useState<any>(null);
  const act = useMutation({
    mutationFn: ({ id, action, reason }: { id: string; action: string; reason?: string }) => api(`/api/requests/${id}`, { method: "PATCH", body: { action, reason } }),
    onSuccess: () => { qc.invalidateQueries(); setDecline(null); },
  });
  return (
    <Card>
      <ErrorOr q={q}>
        {!q.data?.length ? <Empty>{box === "inbox" ? "Nothing waiting for you." : "No requests."}</Empty> : (
          <Table>
            <thead><tr><Th>Type</Th><Th>Person</Th><Th>Project</Th><Th>Hours</Th><Th>Weeks</Th><Th>From</Th><Th>GL</Th><Th>PL</Th><Th>Status</Th><Th /></tr></thead>
            <tbody>
              {q.data.map((r) => (
                <tr key={r.id}>
                  <Td><Badge variant={r.kind === "offer" ? "purple" : "blue"}>{r.kind}</Badge></Td>
                  <Td><Link className="text-primary hover:underline" to={`/people/${r.person_id}`}>{r.person_name}</Link><div className="text-xs text-muted-foreground">{r.tech_level} · {r.group_name}</div></Td>
                  <Td><Link className="text-primary hover:underline" to={`/projects/${r.project_id}`}>{r.project_name}</Link><div className="text-xs text-muted-foreground">{r.client}</div></Td>
                  <Td>{r.hours_per_week} h/wk</Td>
                  <Td className="whitespace-nowrap text-xs">{fmtWeek(r.start_week)} – {fmtWeek(r.end_week)}</Td>
                  <Td className="text-xs">{r.created_by}<div className="text-muted-foreground">{fmtDateTime(r.created_at)}</div>{r.note && <div className="italic">“{r.note}”</div>}</Td>
                  <Td className="text-xs">{r.gl_name}<div><Badge variant={SV[r.gl_status]}>{r.gl_status}</Badge></div></Td>
                  <Td className="text-xs">{r.pl_name}<div><Badge variant={SV[r.pl_status]}>{r.pl_status}</Badge></div></Td>
                  <Td><Badge variant={SV[r.status]}>{r.status}</Badge>{r.reason && <div className="text-xs text-muted-foreground">{r.reason}</div>}</Td>
                  <Td className="whitespace-nowrap">
                    {(r.can_act_as_gl || r.can_act_as_pl) && <>
                      <Button size="sm" onClick={() => act.mutate({ id: r.id, action: "accept" })} loading={act.isPending}><Check className="h-3.5 w-3.5" /> Accept</Button>
                      <Button size="sm" variant="outline" className="ml-1" onClick={() => setDecline(r)}><X className="h-3.5 w-3.5" /> Decline</Button>
                    </>}
                    {isAdmin && r.status === "pending" && <Button size="sm" variant="ghost" className="ml-1" onClick={() => act.mutate({ id: r.id, action: "override", reason: "Admin override" })}>Override</Button>}
                    {r.can_cancel && !(r.can_act_as_gl || r.can_act_as_pl) && <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: r.id, action: "cancel", reason: "Withdrawn" })}>Withdraw</Button>}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
        <ErrorBox error={act.error} />
      </ErrorOr>
      {decline && <DeclineDialog r={decline} onClose={() => setDecline(null)} onSubmit={(reason) => act.mutate({ id: decline.id, action: "decline", reason })} busy={act.isPending} />}
    </Card>
  );
}

function DeclineDialog({ r, onClose, onSubmit, busy }: { r: any; onClose: () => void; onSubmit: (reason: string) => void; busy: boolean }) {
  const [reason, setReason] = useState("");
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title={`Decline ${r.person_name} for ${r.project_name}`} footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button variant="destructive" disabled={!reason.trim()} loading={busy} onClick={() => onSubmit(reason)}>Decline</Button></>}>
        <Field label="Reason (required)"><Input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. committed to another client that month" /></Field>
      </DialogContent>
    </Dialog>
  );
}
