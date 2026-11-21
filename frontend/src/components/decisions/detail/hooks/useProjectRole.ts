'use client';

import { useEffect, useState } from 'react';
import { useAuthStore } from '@/lib/authStore';
import api from '@/lib/api';

const ROLE_LEVELS: Record<string, number> = {
  owner: 1,
  'Super Administrator': 1,
  'Organization Admin': 2,
  'Team Leader': 3,
  'Campaign Manager': 4,
  'Budget Controller': 5,
  Approver: 6,
  Reviewer: 7,
  'Data Analyst': 8,
  member: 8,
  'Senior Media Buyer': 9,
  'Specialist Media Buyer': 10,
  'Junior Media Buyer': 11,
  Designer: 12,
  Copywriter: 13,
  viewer: 999,
};

export const EDIT_MAX_LEVEL = 13;
export const APPROVAL_REVIEW_MAX_LEVEL = 8;
export const VIEW_MAX_LEVEL = 999;

export interface ProjectMember {
  id?: number;
  user?: { id: number; username?: string; email?: string; name?: string } | null;
  user_id?: number;
  email?: string;
  role?: string;
  is_active?: boolean;
}

export interface ProjectRoleInfo {
  role: string | null;
  roleLevel: number;
  canEdit: boolean;
  canApproveOrReview: boolean;
  canView: boolean;
  loading: boolean;
  members: ProjectMember[];
}

function resolveLevel(role?: string | null): number {
  if (!role) return EDIT_MAX_LEVEL;
  return ROLE_LEVELS[role.trim()] ?? EDIT_MAX_LEVEL;
}

export function resolveUserName(members: ProjectMember[] | undefined, userId: number | null | undefined): string {
  if (userId == null) return '—';
  const member = members?.find((m) => (m.user?.id ?? m.user_id) === userId);
  const user = member?.user;
  return (
    user?.name?.trim() ||
    user?.username?.trim() ||
    user?.email?.trim() ||
    `User #${userId}`
  );
}

export function useProjectRole(projectId: number | string | null): ProjectRoleInfo {
  const user = useAuthStore((s) => s.user);
  const [role, setRole] = useState<string | null>(null);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!projectId || !user?.id) {
      setRole(null);
      setMembers([]);
      return;
    }
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const res = await api.get<
          ProjectMember[] | { items?: ProjectMember[]; results?: ProjectMember[] }
        >(`/api/core/projects/${projectId}/members/`);
        const data = res.data as any;
        const raw: ProjectMember[] = Array.isArray(data)
          ? data
          : data?.results ?? data?.items ?? [];
        const myMember = raw.find((m: ProjectMember) => {
          const mid = m.user?.id ?? m.user_id ?? null;
          return mid === user.id;
        });
        if (!cancelled) {
          setRole(myMember?.role ?? null);
          setMembers(raw);
        }
      } catch {
        if (!cancelled) {
          setRole(null);
          setMembers([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [projectId, user?.id]);

  const roleLevel = resolveLevel(role);
  return {
    role,
    roleLevel,
    canEdit: roleLevel <= EDIT_MAX_LEVEL,
    canApproveOrReview: roleLevel <= APPROVAL_REVIEW_MAX_LEVEL,
    canView: roleLevel <= VIEW_MAX_LEVEL,
    loading,
    members,
  };
}
