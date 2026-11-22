import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const STATUS_LABELS: Record<string, string> = {
  bench: "Bench",
  light: "Light",
  healthy: "Healthy",
  hot: "Hot",
  over: "Over-allocated",
  none: "No capacity",
};
export const STATUS_ICON: Record<string, string> = { bench: "▼▼", light: "▼", healthy: "●", hot: "▲", over: "▲▲", none: "–" };

export function bandClass(status?: string | null) {
  return `band-${status || "none"}`;
}

export function fmtWeek(iso: string) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function fmtDate(iso?: string | null) {
  if (!iso) return "–";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function fmtDateTime(iso?: string | null) {
  if (!iso) return "–";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function pct(v?: number | null) {
  return v === null || v === undefined ? "–" : `${Math.round(v)}%`;
}

export function hrs(v?: number | null) {
  if (v === null || v === undefined) return "–";
  return `${Number.isInteger(v) ? v : v.toFixed(1)} h`;
}

export function money(v?: number | null) {
  return v === null || v === undefined ? "–" : `$${v.toFixed(0)}/h`;
}

export function mondayOf(d: Date) {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = (x.getDay() + 6) % 7;
  x.setDate(x.getDate() - day);
  return x;
}

export function isoDate(d: Date) {
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const dd = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${dd}`;
}

export function addWeeks(iso: string, n: number) {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + 7 * n);
  return isoDate(d);
}

export function thisMonday() {
  return isoDate(mondayOf(new Date()));
}

/** Navigate to a download URL (the API sets Content-Disposition). */
export function download(url: string) {
  window.location.assign(url);
}
