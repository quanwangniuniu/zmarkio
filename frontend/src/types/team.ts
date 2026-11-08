/**
 * Team-related type definitions
 */

import { TeamRoleValue } from '@/lib/constants/teamRoles';

/**
 * Team member information
 */
export interface TeamMember {
  user_id: number;
  team_id: number;
  role_id: TeamRoleValue;
  role_name: string;
  user_roles?: { id: number; name: string; level: number }[];
  created_at: string;
  updated_at: string;
}

/**
 * Team information
 */
export interface Team {
  id: number;
  name: string;
  organization_id: number;
  organization_name: string;
  desc?: string;
  parent_team_id?: number;
  is_parent: boolean;
  created_at: string;
  updated_at: string;
  member_count?: number;
}

/**
 * Team members response from API
 */
export interface TeamMembersResponse {
  team_id: number;
  team_name: string;
  members: TeamMember[];
  member_count: number;
}

/**
 * Team detail response from API
 */
export interface TeamDetailResponse {
  team: Team;
  members: TeamMember[];
  member_count: number;
  child_teams: Team[];
  child_team_count: number;
}

/**
 * Current user's team membership info
 */
export interface UserTeamMembership {
  team_id: number;
  team_name: string;
  role_id: TeamRoleValue;
  role_name: string;
  is_leader: boolean;
}
