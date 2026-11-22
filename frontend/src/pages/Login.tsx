import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { CalendarRange } from "lucide-react";
import { api } from "@/api/client";
import { Button, Card, ErrorBox, Input, Field } from "@/components/ui";

export default function LoginPage() {
  const cfg = useQuery<{ mode: string; demo_users: { email: string; name: string; roles: string[] }[] }>({ queryKey: ["auth-config"], queryFn: () => api("/api/auth/config") });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  const nav = useNavigate();

  async function submit(e?: React.FormEvent, as?: string) {
    e?.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api("/api/auth/login", { body: { email: as ?? email, password } });
      qc.setQueryData(["me"], r.user);
      nav("/");
    } catch (x) {
      setErr(x);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center p-6">
      <Card className="w-full max-w-md p-6">
        <div className="mb-5 flex items-center gap-2">
          <CalendarRange className="h-7 w-7 text-primary" aria-hidden />
          <div>
            <h1 className="text-lg font-semibold">Resource Allocation Hub</h1>
            <p className="text-xs text-muted-foreground">Forecast demand, see who is hot or light, staff projects.</p>
          </div>
        </div>
        {cfg.data?.mode === "oidc" ? (
          <a href="/api/auth/oidc/login"><Button className="w-full">Sign in with Microsoft</Button></a>
        ) : (
          <form onSubmit={submit} className="space-y-3">
            <Field label="Email">
              <Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
            </Field>
            <Field label="Password">
              <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </Field>
            <ErrorBox error={err} />
            <Button type="submit" className="w-full" loading={busy}>Sign in</Button>
          </form>
        )}
        {!!cfg.data?.demo_users.length && (
          <div className="mt-5 border-t border-border pt-4">
            <p className="mb-2 text-xs text-muted-foreground">Demo users (enter the demo password above, then pick one):</p>
            <div className="grid grid-cols-2 gap-2">
              {cfg.data.demo_users.map((u) => (
                <Button key={u.email} variant="outline" size="sm" className="justify-start" disabled={!password} onClick={() => { setEmail(u.email); submit(undefined, u.email); }}>
                  <span className="truncate">{u.name}</span>
                  <span className="ml-auto text-[10px] uppercase text-muted-foreground">{u.roles.join(", ")}</span>
                </Button>
              ))}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
