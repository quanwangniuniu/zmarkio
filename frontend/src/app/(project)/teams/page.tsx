'use client';
import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import Layout from "@/components/layout/Layout";
import { RemovablePicker } from "@/components/ui/RemovablePicker";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useState, useEffect, useRef } from "react";
import { approverApi } from "@/lib/api/approverApi";
import { PermissionAPI } from "@/lib/api/permissionApi";
import { TEAM_ROLES } from "@/lib/constants/teamRoles";
import DropdownMenu from "@/components/ui/DropdownMenu";
import { ChevronDown, Users, AlertCircle, Trash2 } from 'lucide-react';
import TeamAPI from "@/lib/api/teamApi";
import { UserRoleAPI } from "@/lib/api/userRoleApi";
import { CreateUserRole } from "@/types/permission";
import { TeamDetailResponse } from "@/types/team";
import { useAuthStore } from "@/lib/authStore";
import Modal from "@/components/ui/Modal";
import { Button } from "@/components/ui/button";
import TeamAdminToolbar from "@/components/teams/TeamAdminToolbar";
import CreateTeamDialog from "@/components/teams/CreateTeamDialog";
import DeleteTeamDialog from "@/components/teams/DeleteTeamDialog";
import { Team } from "@/types/team";



function TeamsPageContent() {
  // Auth store
  const { user, userTeams, selectedTeamId, setSelectedTeamId, getUserTeams } = useAuthStore();

  // State for team management
  const [users, setUsers] = useState<{id: number, username: string, email: string}[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [newMember, setNewMember] = useState<string | null>(null);
  const [selectedRole, setSelectedRole] = useState<number>(TEAM_ROLES.MEMBER);
  const [editingMember, setEditingMember] = useState(false);
  const [selectedMembers, setSelectedMembers] = useState<Set<number>>(new Set());
  const [errorAddMember, setErrorAddMember] = useState(false);
  const [errorRemoveMember, setErrorRemoveMember] = useState(false);
  const [errorUpdateRole, setErrorUpdateRole] = useState(false);
  
  // Edit team & user roles modal state
  const [isEditRoleModalOpen, setIsEditRoleModalOpen] = useState(false);
  const [memberRoleChanges, setMemberRoleChanges] = useState<Record<number, number>>({});
  const [pendingAssignUserRole, setPendingAssignUserRole] = useState<Record<number, {id: number; name: string; level: number} | null>>({});
  const [pendingRemoveUserRole, setPendingRemoveUserRole] = useState<Record<number, Set<number>>>({});
  
  // All system roles (permission roles)
  const [allRoles, setAllRoles] = useState<Array<{ id: number; name: string; level: number }>>([]);
  const [loadingAllRoles, setLoadingAllRoles] = useState(false);
  
  // State for team data
  const [teamList, setTeamList] = useState<any[]>([]);
  const [loadingTeams, setLoadingTeams] = useState(true);
  const [teamsError, setTeamsError] = useState<string | null>(null);
  const [selectedTeamForEdit, setSelectedTeamForEdit] = useState<number | null>(null);
  const [teamDetails, setTeamDetails] = useState<Record<number, any>>({});

  // Organization admins and superusers manage every team of their organization.
  // Uses the backend's strict signal, not the broader is_org_admin flag.
  const canManageAllTeams = !!user?.can_manage_all_teams;
  const [teamSearch, setTeamSearch] = useState('');
  const [isCreateTeamOpen, setIsCreateTeamOpen] = useState(false);
  const [isDeleteTeamOpen, setIsDeleteTeamOpen] = useState(false);
  const [deletingTeam, setDeletingTeam] = useState(false);
  const [teamActionError, setTeamActionError] = useState<string | null>(null);
  const orgTeamsRequest = useRef(0);

  // Handle member selection
  const handleMemberSelect = (memberId: number) => {
    const newSelected = new Set(selectedMembers);
    if (newSelected.has(memberId)) {
      newSelected.delete(memberId);
    } else {
      newSelected.add(memberId);
    }
    setSelectedMembers(newSelected);
    setEditingMember(newSelected.size > 0);
  };

  // Handle team selection for editing
  const handleTeamSelect = (teamId: number) => {
    setSelectedTeamForEdit(teamId);
    setSelectedMembers(new Set());
    setEditingMember(false);

    // The organization-wide list has no member data; load it on selection
    if (canManageAllTeams) {
      TeamAPI.getTeamDetail(teamId)
        .then((detail) => setTeamDetails(prev => ({ ...prev, [teamId]: { id: teamId, ...detail } })))
        .catch((error) => console.error(`Failed to fetch details for team ${teamId}:`, error));
    }
  };

  // Organization admins: load (and search) all teams of the organization
  const loadOrgTeams = async (search: string) => {
    const requestId = ++orgTeamsRequest.current;
    try {
      const teams: Team[] = await TeamAPI.listTeams(search);
      if (requestId !== orgTeamsRequest.current) return;
      setTeamList(teams.map(t => ({ id: t.id, team: t, member_count: t.member_count })));
      setTeamsError(null);
    } catch (error) {
      console.error('Error fetching organization teams:', error);
      if (requestId === orgTeamsRequest.current) setTeamsError('Failed to fetch teams');
    } finally {
      if (requestId === orgTeamsRequest.current) setLoadingTeams(false);
    }
  };

  const handleTeamCreated = async (team: Team) => {
    setIsCreateTeamOpen(false);
    setTeamActionError(null);
    setTeamSearch('');
    await loadOrgTeams('');
    handleTeamSelect(team.id);
  };

  const handleDeleteTeam = async () => {
    if (!selectedTeamForEdit) return;
    const teamId = selectedTeamForEdit;
    try {
      setDeletingTeam(true);
      setTeamActionError(null);
      await TeamAPI.deleteTeam(teamId);
      setSelectedTeamForEdit(null);
      setTeamDetails(prev => {
        const next = { ...prev };
        delete next[teamId];
        return next;
      });
      await loadOrgTeams(teamSearch);
    } catch (error) {
      console.error('Failed to delete team:', error);
      const status = (error as { response?: { status?: number } })?.response?.status;
      setTeamActionError(
        status === 409
          ? 'This team still has members or child teams. Remove them before deleting the team.'
          : 'Failed to delete the team. Please try again.'
      );
    } finally {
      setDeletingTeam(false);
    }
  };

  // Check if current user is leader of the selected team
  const isCurrentUserLeader = (teamId: number): boolean => {
    if (!user?.id || !teamDetails[teamId]) return false;
    
    const currentUserMember = teamDetails[teamId].members?.find(
      (member: any) => member.user_id === user.id
    );
    
    return currentUserMember?.role_id === TEAM_ROLES.LEADER;
  };

  // Team Leaders manage their own team; organization admins manage every team
  const canManageTeam = (teamId: number): boolean =>
    canManageAllTeams || isCurrentUserLeader(teamId);

  // Handle add member to team
  const handleAddMemberToTeam = async (teamId: number, userId: number, roleId: number) => {
    try {
      console.log('Adding member:', {
        teamId: teamId,
        userId: userId,
        role: roleId,
        roleName: roleId === TEAM_ROLES.LEADER ? 'Team Leader' : 'Member'
      });
      const response = await TeamAPI.addMemberToTeam(teamId, userId, roleId);
      console.log(`✅ Member added to team:`, response);
      setErrorAddMember(false);
      
      // Clear the form after successful addition
      setNewMember(null);
      setSelectedRole(TEAM_ROLES.MEMBER);
      
      // Refresh team details to show the new member
      if (selectedTeamForEdit) {
        try {
          const updatedTeamDetail = await TeamAPI.getTeamDetail(selectedTeamForEdit);
          setTeamDetails(prev => ({
            ...prev,
            [selectedTeamForEdit]: { id: selectedTeamForEdit, ...updatedTeamDetail }
          }));
          
          // Also update the team list
          setTeamList(prev => prev.map(team => 
            team.id === selectedTeamForEdit 
              ? { ...team, ...updatedTeamDetail }
              : team
          ));
        } catch (refreshError) {
          console.error('Failed to refresh team data:', refreshError);
        }
      }
      
      
    } catch (error) {
      console.error('Failed to add member:', error);
      setErrorAddMember(true);
    }
  };

  // Handle remove members from team
  const handleRemoveMembersFromTeam = async (teamId: number, userIds: number[]) => {
    try {
      setErrorRemoveMember(false);
      
      console.log('Removing members:', {
        teamId: teamId,
        userIds: userIds,
        count: userIds.length
      });

      // Remove each member
      const removePromises = userIds.map(userId => 
        TeamAPI.removeMemberFromTeam(teamId, userId)
      );
      
      await Promise.all(removePromises);
      console.log(`✅ ${userIds.length} member(s) removed from team`);
      
      // Clear selection
      setSelectedMembers(new Set());
      setEditingMember(false);
      
      // Refresh team details to show updated member list
      if (selectedTeamForEdit) {
        try {
          const updatedTeamDetail = await TeamAPI.getTeamDetail(selectedTeamForEdit);
          setTeamDetails(prev => ({
            ...prev,
            [selectedTeamForEdit]: { id: selectedTeamForEdit, ...updatedTeamDetail }
          }));
          
          // Also update the team list
          setTeamList(prev => prev.map(team => 
            team.id === selectedTeamForEdit 
              ? { ...team, ...updatedTeamDetail }
              : team
          ));
        } catch (refreshError) {
          console.error('Failed to refresh team data after removal:', refreshError);
        }
      }
      
    } catch (error) {
      console.error('Failed to remove members:', error);
      setErrorRemoveMember(true);
    }
  };

  // Handle remove members with confirmation
  const handleRemoveMembersWithConfirmation = (teamId: number, userIds: number[]) => {
    const memberCount = userIds.length;
    const memberText = memberCount === 1 ? 'member' : 'members';
    
    const confirmed = window.confirm(
      `Are you sure you want to remove ${memberCount} ${memberText} from the team?\n\nThis action cannot be undone.`
    );
    
    if (confirmed) {
      handleRemoveMembersFromTeam(teamId, userIds);
    }
  };

  // Handle edit role modal
  const handleEditRoleClick = () => {
    if (selectedTeamForEdit && selectedMembers.size > 0) {
      // Initialize role changes with current roles
      const initialChanges: Record<number, number> = {};
      selectedMembers.forEach(userId => {
        const member = teamDetails[selectedTeamForEdit].members?.find((m: any) => m.user_id === userId);
        if (member) {
          initialChanges[userId] = member.role_id;
        }
      });
      setMemberRoleChanges(initialChanges);
      setIsEditRoleModalOpen(true);
    }
  };

  const handleTeamRoleChange = (userId: number, newRoleId: number) => {
    setMemberRoleChanges(prev => ({
      ...prev,
      [userId]: newRoleId
    }));
  };


  const handleSaveChanges = async () => {
    if (!selectedTeamForEdit) return;

    try {
      setErrorUpdateRole(false);
      const promises: Promise<any>[] = [];
      // 1) Update team roles
      Object.entries(memberRoleChanges).forEach(([userId, roleId]) => {
        promises.push(TeamAPI.updateMemberRole(selectedTeamForEdit, parseInt(userId), roleId));
      });
      // 2) Assign user roles分配用户角色
      Object.entries(pendingAssignUserRole).forEach(([userId, role]) => {
        if (!role) return;
        const payload: CreateUserRole = { role_id: role.id, team_id: selectedTeamForEdit } as CreateUserRole;
        promises.push(UserRoleAPI.assignUserRole(parseInt(userId), payload));
      });
      // 3) Remove user roles
      Object.entries(pendingRemoveUserRole).forEach(([userId, roleSet]) => {
        if (!roleSet) return;
        roleSet.forEach((rid) => {
          promises.push(UserRoleAPI.removeUserRole(parseInt(userId), rid, selectedTeamForEdit));
        });
      });

      await Promise.all(promises);
      console.log('✅ Saved team role and user role changes');
      
      // Close modal and clear selection
      setIsEditRoleModalOpen(false);
      setMemberRoleChanges({});
      setPendingAssignUserRole({});
      setPendingRemoveUserRole({});
      setSelectedMembers(new Set());
      setEditingMember(false);
      
      // Refresh team details
      try {
        const updatedTeamDetail = await TeamAPI.getTeamDetail(selectedTeamForEdit);
        setTeamDetails(prev => ({
          ...prev,
          [selectedTeamForEdit]: { id: selectedTeamForEdit, ...updatedTeamDetail }
        }));
        
        // Also update the team list
        setTeamList(prev => prev.map(team => 
          team.id === selectedTeamForEdit 
            ? { ...team, ...updatedTeamDetail }
            : team
        ));
      } catch (refreshError) {
        console.error('Failed to refresh team data after role update:', refreshError);
      }
      
    } catch (error) {
      console.error('Failed to update member roles:', error);
      setErrorUpdateRole(true);
    }
  };

  // Handle cancel role changes
  const handleCancelRoleChanges = () => {
    setIsEditRoleModalOpen(false);
    setMemberRoleChanges({});
    setPendingAssignUserRole({});
    setPendingRemoveUserRole({});
  };

  // Temporarily store the user role to be assigned
  const stageAssignUserRole = (userId: number, role: { id: number; name: string; level: number }) => {
    setPendingAssignUserRole(prev => ({ ...prev, [userId]: role }));

  };

  // Temporarily store/unstore the user role to be removed
  const stageToggleRemoveUserRole = (userId: number, roleId: number) => {
    setPendingRemoveUserRole(prev => {
      const next = { ...prev } as Record<number, Set<number>>;
      const current = new Set(next[userId] || []);
      if (current.has(roleId)) {
        current.delete(roleId);
      } else {
        current.add(roleId);
      }
      
      // If the Set is empty after toggle, remove the userId entry entirely
      if (current.size === 0) {
        delete next[userId];
      } else {
        next[userId] = current;
      }
      
      return next;
    });
  };

  // get team roles for dropdown
  const teamRoles = [
    {
      type: 'option',
      label: 'Team Leader',
      onClick: () => setSelectedRole(TEAM_ROLES.LEADER),
      className: selectedRole === TEAM_ROLES.LEADER ? 'bg-indigo-50 text-indigo-700' : 'text-gray-700'
    },
    {
      type: 'option', 
      label: 'Member',
      onClick: () => setSelectedRole(TEAM_ROLES.MEMBER),
      className: selectedRole === TEAM_ROLES.MEMBER ? 'bg-indigo-50 text-indigo-700' : 'text-gray-700'
    }
  ];

  // Fetch user teams on component mount
  useEffect(() => {
    // Load all permission roles once
    const loadAllRoles = async () => {
      try {
        setLoadingAllRoles(true);
        const roles = await PermissionAPI.getRoles();
        // roles may contain rank; we rely on name and level
        setAllRoles(
          (roles || []).map((r: any) => ({ id: r.id, name: r.name, level: r.level ?? r.rank }))
        );
      } catch (e) {
        console.error('Failed to load roles:', e);
      } finally {
        setLoadingAllRoles(false);
      }
    };
    loadAllRoles();
  }, []);
  useEffect(() => {
    if (canManageAllTeams) return; // org admins load the organization-wide list below
    const fetchUserTeams = async () => {
      try {
        setLoadingTeams(true);
        setTeamsError(null);
        
        const result = await getUserTeams();
        if (result.success) {
          // Get team details for each team
          const teamDetailsPromises = userTeams.map(async (teamId) => {
            try {
              const teamDetail = await TeamAPI.getTeamDetail(teamId);
              return { id: teamId, ...teamDetail };
            } catch (error) {
              console.error(`Failed to fetch details for team ${teamId}:`, error);
              return { id: teamId, name: `Team ${teamId}`, members: [] };
            }
          });
          
          const teamDetails = await Promise.all(teamDetailsPromises);
          setTeamList(teamDetails);
          
          // Set team details for easy access
          const detailsMap: Record<number, any> = {};
          teamDetails.forEach(team => {
            detailsMap[team.id] = team;
          });
          setTeamDetails(detailsMap);
        } else {
          setTeamsError(result.error || 'Failed to fetch teams');
        }
      } catch (error) {
        console.error('Error fetching user teams:', error);
        setTeamsError('Failed to fetch teams');
      } finally {
        setLoadingTeams(false);
      }
    };

    fetchUserTeams();
  }, [canManageAllTeams]); // Runs on mount (and if the admin status resolves later)

  // Organization admins: debounced search over all teams of the organization
  useEffect(() => {
    if (!canManageAllTeams) return;
    const handle = setTimeout(() => { loadOrgTeams(teamSearch); }, teamSearch ? 300 : 0);
    return () => clearTimeout(handle);
  }, [canManageAllTeams, teamSearch]);

  // Update team list when userTeams changes
  useEffect(() => {
    if (canManageAllTeams) return;
    if (userTeams.length > 0) {
      const fetchTeamDetails = async () => {
        try {
          const teamDetailsPromises = userTeams.map(async (teamId) => {
            try {
              const teamDetail = await TeamAPI.getTeamDetail(teamId);
              return { id: teamId, ...teamDetail };
            } catch (error) {
              console.error(`Failed to fetch details for team ${teamId}:`, error);
              return { id: teamId, name: `Team ${teamId}`, members: [] };
            }
          });
          
          const teamDetails = await Promise.all(teamDetailsPromises);
          setTeamList(teamDetails);
          
          // Set team details for easy access
          const detailsMap: Record<number, any> = {};
          teamDetails.forEach(team => {
            detailsMap[team.id] = team;
          });
          setTeamDetails(detailsMap);
        } catch (error) {
          console.error('Error fetching team details:', error);
        }
      };

      fetchTeamDetails();
    } else {
      setTeamList([]);
      setTeamDetails({});
    }
  }, [userTeams, canManageAllTeams]);

  // Fetch existing users for adding members
  useEffect(() => {
    const fetchUsers = async () => {
      try {
        setLoadingUsers(true);
        const users = await approverApi.getAllUsers();
        setUsers(users);
      } catch (err) {
        console.error('Error fetching users:', err);
        setUsers([]);
      } finally {
        setLoadingUsers(false);
      }
    };
    fetchUsers();
    setErrorAddMember(false);
    setErrorRemoveMember(false);
    setErrorUpdateRole(false);
  }, []);



  return (
    <Layout>
      <div className="min-h-screen bg-gray-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="flex flex-col gap-8">

            {/* Page Header */}
            <div className="flex flex-col gap-4">
              <h1 className="text-3xl font-bold text-gray-900">Team Management</h1>
              <p className="text-gray-600 text-sm">
                {canManageAllTeams
                  ? 'Manage all teams in your organization, including their members and roles.'
                  : 'Manage your team members and their roles.'}
              </p>
              {canManageAllTeams && (
                <TeamAdminToolbar
                  search={teamSearch}
                  onSearchChange={setTeamSearch}
                  onCreateClick={() => setIsCreateTeamOpen(true)}
                />
              )}
              {teamActionError && (
                <p role="alert" className="text-sm text-red-600">{teamActionError}</p>
              )}
            </div>

            {/* Loading State */}
            {loadingTeams && (
              <div className="text-center py-8">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600 mx-auto"></div>
                <p className="mt-2 text-gray-600">Loading teams...</p>
              </div>
            )}

            {/* Error State */}
            {teamsError && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-6">
                <div className="flex items-center gap-3">
                  <AlertCircle className="h-5 w-5 text-red-500" />
                  <div>
                    <h3 className="text-sm font-medium text-red-800">Failed to load teams</h3>
                    <p className="text-sm text-red-600 mt-1">{teamsError}</p>
                  </div>
                </div>
              </div>
            )}

            {/* No Teams State */}
            {!loadingTeams && !teamsError && teamList.length === 0 && (
              <div className="bg-gray-50 border border-gray-200 rounded-lg p-8 text-center">
                <Users className="h-12 w-12 text-gray-400 mx-auto mb-4" />
                <h3 className="text-lg font-medium text-gray-900 mb-2">No Teams Found</h3>
                <p className="text-gray-600">
                  {canManageAllTeams
                    ? (teamSearch.trim()
                        ? `No teams match "${teamSearch.trim()}".`
                        : 'Your organization has no teams yet. Use Create Team to add one.')
                    : 'You are not currently associated with any teams. Please contact your administrator to be added to a team.'}
                </p>
              </div>
            )}

            {/* Teams List */}
            {!loadingTeams && !teamsError && teamList.length > 0 && (
              <div className="space-y-6">

                {/* Team Selection */}
                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                  <h2 className="text-lg font-semibold text-gray-900 mb-4">Select Team to Manage</h2>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {teamList.map((team) => (
                      <button
                        key={team.id}
                        onClick={() => handleTeamSelect(team.id)}
                        className={`p-4 rounded-lg border-2 text-left transition-colors ${
                          selectedTeamForEdit === team.id
                            ? 'border-indigo-500 bg-indigo-50'
                            : 'border-gray-200 hover:border-gray-300'
                        }`}
                      >
                        <h3 className="font-medium text-gray-900">{team.team?.name || `Team ${team.id}`}</h3>
                        <p className="text-sm text-gray-600 mt-1">
                          {team.members?.length ?? team.member_count ?? 0} members
                        </p>
                      </button>
                    ))}
                  </div>
                </div>

                {/* Team Management Section */}
                {selectedTeamForEdit && teamDetails[selectedTeamForEdit] && (
                  <div className="p-6 space-y-1 bg-white rounded-lg shadow-sm border border-gray-200">
                    {/* Team Header */}
                    <div className="py-6 flex items-center justify-between gap-4">
                      <h2 className="text-xl font-semibold text-gray-900">
                        {teamDetails[selectedTeamForEdit].team?.name || `Team ${selectedTeamForEdit}`}
                      </h2>
                      {canManageAllTeams && (
                        <Button variant="destructive" size="sm" onClick={() => setIsDeleteTeamOpen(true)}>
                          <Trash2 aria-hidden="true" />
                          Delete Team
                        </Button>
                      )}
                    </div>

                    {/* Add New Member - Only visible to team leaders */}
                    {selectedTeamForEdit && canManageTeam(selectedTeamForEdit) && (
                      <div className="py-6">
                      <h3 className="text-lg font-medium text-gray-900 mb-4">Add New Member</h3>
                      <div className="flex flex-row gap-4 items-center">
                        <RemovablePicker 
                          options={users.map(user => ({ value: user.id.toString(), label: user.username }))} 
                          placeholder="Select a user" 
                          value={newMember}
                          onChange={(val) => setNewMember(val)}
                          loading={loadingUsers}
                        />

                        <p>as</p>
                        
                        <DropdownMenu
                          trigger={
                            <div className="flex items-center gap-2 px-3 py-1.5 border border-gray-300 rounded-md bg-white hover:bg-gray-50 cursor-pointer">
                              <span className="text-sm text-gray-700">
                                {selectedRole === TEAM_ROLES.LEADER ? 'Team Leader' : 'Member'}
                              </span>
                              <ChevronDown className="h-4 w-4 text-gray-500" />
                            </div>
                          } 
                          items={teamRoles}
                        />

                        <button 
                          className="px-4 py-2 rounded text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50"
                          disabled={!newMember}
                          onClick={() => {
                            if (newMember) {
                              handleAddMemberToTeam(selectedTeamForEdit, parseInt(newMember), selectedRole);
                            }
                          }}
                        >
                          Add Member
                        </button>
                      </div>
                    </div>
                    )}

                     {/* Error State - Add Member */}
                     {errorAddMember && (
                       <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                         <div className="flex items-center gap-3">
                           <AlertCircle className="h-5 w-5 text-red-500" />
                           <div>
                             <h3 className="text-sm font-medium text-red-800">Failed to add member</h3>
                             <p className="text-sm text-red-600 mt-1">
                               There was an error adding the member to the team. Please try again.
                             </p>
                           </div>
                         </div>
                       </div>
                     )}

                     {/* Error State - Remove Member */}
                     {errorRemoveMember && (
                       <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                         <div className="flex items-center gap-3">
                           <AlertCircle className="h-5 w-5 text-red-500" />
                           <div>
                             <h3 className="text-sm font-medium text-red-800">Failed to remove member(s)</h3>
                             <p className="text-sm text-red-600 mt-1">
                               There was an error removing the member(s) from the team. Please try again.
                             </p>
                           </div>
                         </div>
                       </div>
                     )}

                    {/* Team Members Table */}
                    <div className="py-6">
                      <h3 className="text-lg font-medium text-gray-900 mb-4">Manage Team Members</h3>
                      <Table>  
                        <TableHeader>
                          <TableRow>
                            {selectedTeamForEdit && canManageTeam(selectedTeamForEdit) && (
                              <TableHead className="w-12">
                                <input 
                                  type="checkbox" 
                                  className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                                  onChange={(e) => {
                                    const allMemberIds = teamDetails[selectedTeamForEdit].members
                                      ?.map((m: any) => m.user_id)
                                      .filter((userId: number) => userId !== user?.id) || []; // exclude the current user
                                    if (e.target.checked) {
                                      setSelectedMembers(new Set(allMemberIds));
                                      setEditingMember(true);
                                    } else {
                                      setSelectedMembers(new Set());
                                      setEditingMember(false);
                                    }
                                  }}
                                  checked={selectedMembers.size > 0 
                                    && selectedMembers.size === (teamDetails[selectedTeamForEdit].members?.filter((m: any) => m.user_id !== user?.id).length || 0)} // exclude the current user
                                />
                              </TableHead>
                            )}
                            <TableHead>User ID</TableHead>
                            <TableHead>Name</TableHead>
                            <TableHead>Email</TableHead>
                            <TableHead>Team Role</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {teamDetails[selectedTeamForEdit].members?.map((member: any) => {
                            const isCurrentUser = member.user_id === user?.id;
                            return (
                              <TableRow key={member.user_id}>
                                {selectedTeamForEdit && canManageTeam(selectedTeamForEdit) && (
                                  <TableCell>
                                    <input 
                                      type="checkbox" 
                                      className={`rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 ${
                                        isCurrentUser ? 'opacity-50 cursor-not-allowed' : ''
                                      }`}
                                      checked={selectedMembers.has(member.user_id)}
                                      onChange={() => !isCurrentUser && handleMemberSelect(member.user_id)}
                                      disabled={isCurrentUser}
                                      title={isCurrentUser ? 'You cannot remove yourself from the team' : ''}
                                    />
                                  </TableCell>
                                )}
                                <TableCell>{member.user_id}</TableCell>
                                <TableCell className={isCurrentUser ? 'font-medium text-indigo-600' : ''}>
                                  {member.username || 'Unknown'}
                                  {isCurrentUser && <span className="ml-2 text-xs text-indigo-500">(You)</span>}
                                </TableCell>
                                <TableCell>{member.email || 'Unknown'}</TableCell>
                                <TableCell>{member.role_name || 'Unknown'}</TableCell>
                              </TableRow>
                            );
                          }) || (
                            <TableRow>
                              <TableCell colSpan={5} className="text-center text-gray-500 py-8">
                                No members found
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
                      </Table>
                    </div>

                    {/* Operations - Only visible to team leaders */}
                    {editingMember && selectedTeamForEdit && canManageTeam(selectedTeamForEdit) && (
                      <div className="py-6">
                        <h3 className="text-lg font-medium text-gray-900 mb-4">Selected Members ({selectedMembers.size})</h3>
                        <div className="flex flex-row justify-center gap-4">
                          <button 
                            className="px-4 py-2 text-sm font-medium rounded-md transition-colors bg-yellow-600 text-white hover:bg-yellow-700"
                            onClick={handleEditRoleClick}
                          >
                            Edit
                          </button>
                          <button 
                            className="px-4 py-2 text-sm font-medium rounded-md transition-colors bg-red-600 text-white hover:bg-red-700"
                            onClick={() => {
                              if (selectedTeamForEdit && selectedMembers.size > 0) {
                                const userIds = Array.from(selectedMembers);
                                handleRemoveMembersWithConfirmation(selectedTeamForEdit, userIds);
                              }
                            }}
                          >
                            Remove ({selectedMembers.size})
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Organization admin dialogs */}
      <CreateTeamDialog
        open={isCreateTeamOpen}
        onClose={() => setIsCreateTeamOpen(false)}
        onCreated={handleTeamCreated}
      />
      <DeleteTeamDialog
        open={isDeleteTeamOpen}
        teamName={(selectedTeamForEdit && teamDetails[selectedTeamForEdit]?.team?.name) || 'this team'}
        loading={deletingTeam}
        onClose={() => setIsDeleteTeamOpen(false)}
        onConfirm={handleDeleteTeam}
      />

      {/* Edit Role Modal */}
      <Modal isOpen={isEditRoleModalOpen} onClose={handleCancelRoleChanges}>
        <div className="bg-white rounded-lg shadow-xl min-h-[75vh] min-w-[50vw]">
          <div className="px-6 py-4 border-b border-gray-200">
            <h3 className="text-lg font-semibold text-gray-900">Edit Member Roles</h3>
            <p className="text-sm text-gray-600 mt-1">
              Update roles for {selectedMembers.size} selected member(s)
            </p>
          </div>
          
          <div className="px-6 py-4 min-h-[50vh] overflow-y-auto">
            {errorUpdateRole && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                <div className="flex items-center gap-3">
                  <AlertCircle className="h-5 w-5 text-red-500" />
                  <div>
                    <h3 className="text-sm font-medium text-red-800">Failed to update roles</h3>
                    <p className="text-sm text-red-600 mt-1">
                      There was an error updating the member roles. Please try again.
                    </p>
                  </div>
                </div>
              </div>
            )}
            
            <div className="space-y-4">
              {selectedTeamForEdit && Array.from(selectedMembers).map(userId => {
                const member = teamDetails[selectedTeamForEdit]?.members?.find((m: any) => m.user_id === userId);
                if (!member) return null;
                
                const currentRoleId = memberRoleChanges[userId] || member.role_id;
                const isCurrentUser = userId === user?.id;
                const validUserRoles = Array.isArray(member.user_roles)
                  ? member.user_roles.filter((ur: any) => ur && typeof ur.name === 'string' && ur.name.trim().length > 0)
                  : [];

                const assignableRoleItems = (() => {
                  type AssignableRole = { id: number | string; name: string; level: number };
                  const existingRoleNames = new Set(
                    validUserRoles.map((ur: { id: number; name: string; level: number }) => ur.name)
                  );
                  // Filter out roles that are already assigned to the user,and roles that are level 1 or 2
                  const assignableRoles: AssignableRole[] = (allRoles as any[]).filter((r: any) =>
                    r && r.name && r.level !== 1 && r.level !== 2 && !existingRoleNames.has(r.name)
                  );
                  return assignableRoles.length > 0
                    ? assignableRoles.map((r: AssignableRole) => ({
                        type: 'option' as const,
                        label: `${r.name} (Level ${r.level})`,
                      onClick: () => stageAssignUserRole(userId, { id: r.id as number, name: r.name, level: r.level }),
                        className: 'text-gray-700'
                      }))
                    : [{
                        type: 'option' as const,
                        label: 'No available roles',
                        onClick: () => {},
                        className: 'text-gray-400'
                      }];
                })();

                
                return (
                  <div key={userId} className="flex items-center gap-10 p-4 border border-gray-200 rounded-lg w-full">
                    
                    {/* User name and email */}
                    <div className="flex flex-col gap-2">
                      <h4 className="font-medium text-gray-900">
                        {member.username || 'Unknown'}
                        {isCurrentUser && <span className="ml-2 text-xs text-indigo-500">(You)</span>}
                      </h4>
                      <p className="text-sm text-gray-600">{member.email || 'Unknown'}</p>
                    </div>

                    {/* Role Management */}
                    <div className="flex flex-col gap-4 justify-center">

                      {/* Manage Team Role */}
                      <div className="flex items-center gap-3">
                        <span className="text-sm text-gray-500">Update Team Role:</span>
                        <span className="text-sm font-medium text-gray-700">{member.role_name}</span>
                        <span className="text-gray-300">→</span>
                        <DropdownMenu
                          trigger={
                            <div className="flex items-center gap-2 px-3 py-1.5 border border-gray-300 rounded-md bg-white hover:bg-gray-50 cursor-pointer">
                              <span className="text-sm text-gray-700">
                                {currentRoleId === TEAM_ROLES.LEADER ? 'Team Leader' : 'Member'}
                              </span>
                              <ChevronDown className="h-4 w-4 text-gray-500" />
                            </div>
                          }
                          items={[
                            {
                              type: 'option',
                              label: 'Team Leader',
                              onClick: () => handleTeamRoleChange(userId, TEAM_ROLES.LEADER),
                              className: currentRoleId === TEAM_ROLES.LEADER ? 'bg-indigo-50 text-indigo-700' : 'text-gray-700'
                            },
                            {
                              type: 'option',
                              label: 'Member',
                              onClick: () => handleTeamRoleChange(userId, TEAM_ROLES.MEMBER),
                              className: currentRoleId === TEAM_ROLES.MEMBER ? 'bg-indigo-50 text-indigo-700' : 'text-gray-700'
                            }
                          ]}
                        />
                      </div>

                      {/* Manage User Role */}
                      {/* Only display roles related to the current team */}
                      <div className="flex flex-col gap-2 justify-center">
                        <span className="text-sm text-gray-500">Current User Roles:</span>
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Role</TableHead>
                              <TableHead>Level</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {validUserRoles.length > 0 ? (
                              validUserRoles.map((ur: {id: number; name: string; level: number}) => {
                                const stagedRemove = (pendingRemoveUserRole[userId] || new Set()).has(ur.id);
                                return (
                                <TableRow key={`${member.user_id}-userrole-${ur.id}`}>
                                  <TableCell>{ur.name}</TableCell>
                                  <TableCell>{ur.level}</TableCell>
                                  <TableCell>
                                    <button 
                                      onClick={() => stageToggleRemoveUserRole(userId, ur.id)}
                                      className={`px-2 py-1 text-sm font-medium rounded-md ${stagedRemove ? 'bg-red-400 text-white' : 'bg-red-600 text-white hover:bg-red-400'}`}>
                                      {pendingRemoveUserRole[userId]?.has(ur.id) ? 'Undo' : 'Remove'}
                                    </button>
                                  </TableCell>
                                </TableRow>
                              );})
                            ) : (
                              <TableRow>
                                <TableCell colSpan={2} className="text-gray-400 italic">No roles for this team</TableCell>
                              </TableRow>
                            )}
                          </TableBody>
                        </Table>
                      {/* Assign new user roles */}
                      <div className="flex flex-row gap-10 justify-center items-center">
                        <span className="text-sm text-gray-500">Assign New User Roles:</span>
                        <DropdownMenu
                          trigger={
                            <div className="flex items-center gap-2 px-3 py-1.5 border border-gray-300 rounded-md bg-white hover:bg-gray-50 cursor-pointer">
                              <span className="text-sm text-gray-700">
                                {pendingAssignUserRole[userId] 
                                  ? `${pendingAssignUserRole[userId]?.name} (Level ${pendingAssignUserRole[userId]?.level})` 
                                  : (loadingAllRoles ? 'Loading roles...' : 'Select role to assign')}
                              </span>
                              <ChevronDown className="h-4 w-4 text-gray-500" />
                            </div>
                          }
                          items={assignableRoleItems}
                        />
                        <button
                          onClick={() => setPendingAssignUserRole({...pendingAssignUserRole, [userId]: null})}
                          className="px-2 py-1 text-sm font-medium rounded-md bg-gray-600 text-white hover:bg-gray-400">
                          Reset
                        </button>
                      </div>
                      </div>
                    </div>      
                  </div>
                );
              })}
            </div>
          </div>
          
          <div className="px-6 py-4 border-t border-gray-200 flex justify-end gap-3">
            <button
              onClick={handleCancelRoleChanges}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              Cancel
            </button>
            <button
              onClick={handleSaveChanges}
              className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 border border-transparent rounded-md hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              Save Changes
            </button>
          </div>
        </div>
      </Modal>
    </Layout>
  )

}

export default function TeamsPage() {
  return (
    <ProtectedRoute>
      <TeamsPageContent />
    </ProtectedRoute>
  )
}