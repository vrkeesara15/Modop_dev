import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Me, Project } from "@/api/types";

export function useMe() {
  return useQuery<Me>({ queryKey: ["me"], queryFn: () => api("/api/me"), retry: false, staleTime: 60_000 });
}

export function useTechLevels() {
  return useQuery<{ id: string; code: string; rank: number; description: string }[]>({
    queryKey: ["tech-levels"],
    queryFn: () => api("/api/tech-levels"),
    staleTime: 300_000,
  });
}

export interface GroupRow {
  id: string;
  name: string;
  gl_name: string | null;
  gl_user_id: string | null;
  sl_user_id: string | null;
  sl_name: string | null;
  headcount: number;
  parent_group_id: string | null;
}

export function useGroups() {
  return useQuery<GroupRow[]>({ queryKey: ["groups"], queryFn: () => api("/api/groups"), staleTime: 60_000 });
}

export function useSkills() {
  return useQuery<{ id: string; name: string; category: string | null }[]>({ queryKey: ["skills"], queryFn: () => api("/api/skills"), staleTime: 60_000 });
}

export function useSettings() {
  return useQuery<any>({ queryKey: ["settings"], queryFn: () => api("/api/admin/settings"), staleTime: 60_000 });
}

export function useProjects() {
  return useQuery<Project[]>({ queryKey: ["projects"], queryFn: () => api("/api/projects") });
}

export function hasRole(me: Me | undefined, ...roles: string[]) {
  return !!me && me.roles.some((r) => roles.includes(r));
}
