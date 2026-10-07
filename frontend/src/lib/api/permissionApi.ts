// src/lib/api/permissionApi.ts - Connect to AUTH-06 backend API
import {
  Organization,
  Team,
  Role,
  Permission,
  RolePermission,
  PermissionMatrix,
  ProjectPermissionMatrix,
  ApiResponse,
  PaginatedResponse
} from '@/types/permission';
import { endSession, getSharedRefreshedToken, readPersistedAuthState } from '@/lib/api';

// API settings — base must point at access_control namespace so /roles/, /organizations/, etc. resolve correctly.
// Default to a same-origin relative path so it works through nginx -> local backend in every
// environment (and so auth cookies are sent). Override with NEXT_PUBLIC_API_URL if needed.
const DEFAULT_API_BASE_URL = '/api/access_control';

// When overriding, set NEXT_PUBLIC_API_URL to the access_control base (e.g. https://<host>/api/access_control).
const API_BASE_URL =
  (process.env.NEXT_PUBLIC_API_URL && process.env.NEXT_PUBLIC_API_URL.trim()) ||
  DEFAULT_API_BASE_URL;
const API_TIMEOUT = 10000;

function buildAuthHeaders(baseHeaders?: HeadersInit): Headers {
  const headers = new Headers(baseHeaders);
  const authState = readPersistedAuthState();
  const token = authState?.state?.token;
  const organizationToken = authState?.state?.organizationAccessToken;

  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (organizationToken && !headers.has('X-Organization-Token')) {
    headers.set('X-Organization-Token', organizationToken);
  }

  return headers;
}

// HTTP client
class ApiClient {
  private static async request<T>(
    endpoint: string, 
    options: RequestInit = {}
  ): Promise<T> {
    return this.requestWithAuth<T>(endpoint, options, true);
  }

  private static async requestWithAuth<T>(
    endpoint: string,
    options: RequestInit = {},
    allowRefresh: boolean
  ): Promise<T> {
    const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
    const url = `${API_BASE_URL}${cleanEndpoint}`;
    const headers = buildAuthHeaders({
      'Content-Type': 'application/json',
      ...options.headers,
    });
    
    const authData = readPersistedAuthState();
    const token = authData?.state?.token;

    const defaultOptions: RequestInit = {
      credentials: 'include', 
      ...options,
      headers,
    };

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT);
      
      console.log(`🌐 API Request: ${url}`); // logs
      
      const response = await fetch(url, {
        ...defaultOptions,
        signal: controller.signal,
      });
      
      clearTimeout(timeoutId);
      
      if (!response.ok) {
        if (response.status === 401 && allowRefresh) {
          const refreshToken = readPersistedAuthState()?.state?.refreshToken;
          if (refreshToken) {
            // Shared with the axios clients: one refresh for concurrent 401s, and a
            // rejected refresh ends the session inside getSharedRefreshedToken.
            const accessToken = await getSharedRefreshedToken(refreshToken);
            if (accessToken) {
              return this.requestWithAuth<T>(endpoint, options, false);
            }
          } else if (headers.has('Authorization')) {
            // Sent a token but have nothing to refresh it with: the session is dead.
            endSession();
          }
        }

        let errorMessage = `HTTP error! status: ${response.status} - ${response.statusText}`;
        try {
          const errorData = await response.json();
          errorMessage = errorData.error || errorMessage;
          const error = new Error(errorMessage);
          (error as any).response = { data: errorData, status: response.status };
          throw error;
        } catch (parseError) {
          const error = new Error(errorMessage);
          (error as any).response = { status: response.status };
          throw error;
        }
      }
      
      const data = await response.json();
      console.log(`✅ API Response for ${url}:`, data); // logs
      return data;
    } catch (error) {
      console.error(`❌ API request failed: ${url}`, error);
      throw error;
    }
  }

  static async get<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, { method: 'GET' });
  }

  static async post<T>(endpoint: string, data: any): Promise<T> {
    return this.request<T>(endpoint, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  static async put<T>(endpoint: string, data: any): Promise<T> {
    return this.request<T>(endpoint, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  static async delete<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, { method: 'DELETE' });
  }
}

// permission management API
export class PermissionAPI {
  // fetch organizations list
  static async getOrganizations(): Promise<Organization[]> {
    try {
      console.log('🔄 Fetching organizations from API...');
      const response = await ApiClient.get<any[]>('/organizations/');
      const organizations = response.map(org => ({
        id: org.id.toString(),
        name: org.name
      }));
      console.log('✅ Organizations loaded from API:', organizations);
      return organizations;
    } catch (error) {
      console.warn('⚠️ Failed to fetch organizations from API:', error);
      return [];
    }
  }

  // fetch teams list
  static async getTeams(organizationId?: string): Promise<Team[]> {
    try {
      console.log('🔄 Fetching teams from API...');
      const endpoint = organizationId 
        ? `/teams/?organization_id=${organizationId}`
        : '/teams/';
      
      const response = await ApiClient.get<any[]>(endpoint);
      const teams = response.map(team => ({
        id: team.id.toString(),
        name: team.name,
        organizationId: team.organizationId || team.organization_id?.toString() || organizationId || ''
      }));
      console.log('✅ Teams loaded from API:', teams);
      return teams;
    } catch (error) {
      console.warn('⚠️ Failed to fetch teams from API:', error);
      return [];
    }
  }

  // fetch all roles list
  static async getRoles(): Promise<Role[]> {
    try {
      console.log('🔄 Fetching roles from API...');
      const response = await ApiClient.get<any[]>('/roles/');
      const roles = response.map(role => {
        // normalize organization id from various backend shapes
        const rawOrgId = role.organization_id ?? role.organization?.id ?? null;
        const normalizedOrgId = (rawOrgId == null || rawOrgId === '' || rawOrgId === 'null')
          ? null
          : rawOrgId.toString();
        const mappedRole = {
          id: role.id.toString(),
          name: role.name,
          description: role.description || `Role: ${role.name}`,
          rank: role.rank || role.level || 0,
          organizationId: normalizedOrgId,
          isReadOnly: role.isReadOnly || false
        };
        console.log(`📋 Mapped role "${role.name}":`, {
          original: { rank: role.rank, level: role.level },
          mapped: { rank: mappedRole.rank }
        });
        return mappedRole;
      });
      console.log('✅ Roles loaded from API:', roles);
      return roles;
    } catch (error) {
      console.warn('⚠️ Failed to fetch roles from API:', error);
      return [];
    }
  }

  // fetch roles by organization_id
  static async getRolesByOrganization(organizationId: string): Promise<Role[]> {
    try {
      console.log('🔄 Fetching roles from API for organization:', organizationId);
      const response = await ApiClient.get<any[]>(`/roles/?organization_id=${organizationId}`);
      const roles = response.map(role => ({
        id: role.id.toString(),
        name: role.name,
        description: role.description || `Role: ${role.name}`,
        rank: role.rank || role.level || 0,
        isReadOnly: role.isReadOnly || false
      }));
      console.log('✅ Roles loaded from API:', roles);
      return roles;
      
    } catch (error) {
      console.warn('⚠️ Failed to fetch roles from API, falling back to mock data:', error);
      // Return filtered mock roles on error, not all roles
      return [];
    }
  }

  // create a new role
  static async createRole(data: { name: string; level: number; organization_id?: number }): Promise<Role> {
    try {
      console.log('🔄 Creating role via API...', data);
      const response = await ApiClient.post<any>('/roles/', data);
      const role = {
        id: response.id.toString(),
        name: response.name,
        description: response.description || `Role: ${response.name}`,
        rank: response.rank || response.level || 0,
        organizationId: response.organization_id?.toString() ?? (response.organization_id != null ? String(response.organization_id) : null),
        isReadOnly: response.isReadOnly || false
      };
      console.log('✅ Role created successfully:', role);
      return role;
    } catch (error) {
      console.error('❌ Failed to create role:', error);
      throw error;
    }
  }

  // delete a role
  static async deleteRole(roleId: string): Promise<void> {
    try {
      console.log('🔄 Deleting role via API...', roleId);
      await ApiClient.delete(`/roles/${roleId}/`);
      console.log('✅ Role deleted successfully');
    } catch (error) {
      console.error('❌ Failed to delete role:', error);
      throw error;
    }
  }

  // update a role
  static async updateRole(roleId: string, data: { name?: string; level?: number; organization_id?: number | null }): Promise<Role> {
    try {
      console.log('🔄 Updating role via API...', roleId, data);
      const response = await ApiClient.put<any>(`/roles/${roleId}/`, data);
      const role = {
        id: response.id.toString(),
        name: response.name,
        description: response.description || `Role: ${response.name}`,
        rank: response.rank || response.level || 0,
        organizationId: response.organization_id?.toString() ?? (response.organization_id != null ? String(response.organization_id) : null),
        isReadOnly: response.isReadOnly || false
      };
      console.log('✅ Role updated successfully:', role);
      return role;
    } catch (error) {
      console.error('❌ Failed to update role:', error);
      throw error;
    }
  }




  // Current user's roles with real rank values fetched from the API.
  static async getCurrentUserRoles(): Promise<Role[]> {
    try {
      const { useAuthStore } = await import('../authStore');
      // If Zustand hasn't hydrated yet, fall back to localStorage directly.
      const currentUser = useAuthStore.getState().user ?? readPersistedAuthState()?.state?.user ?? null;

      if (!currentUser) return [];

      // Staff and org admins always get full access.
      if (currentUser.is_staff || currentUser.is_org_admin) {
        return [{
          id: 'admin',
          name: 'Admin',
          description: 'Administrator',
          rank: 1,
          organizationId: undefined,
          isReadOnly: false,
        }];
      }

      if (!currentUser.roles || !Array.isArray(currentUser.roles) || currentUser.roles.length === 0) {
        return [];
      }

      // Fetch real roles from the API to get actual level/rank values.
      const allRoles = await PermissionAPI.getRoles();
      const userRoleNames = new Set(currentUser.roles.map((r: string) => r.toLowerCase()));
      const matched = allRoles.filter(r => userRoleNames.has(r.name.toLowerCase()));

      // If we matched at least one role, use those (with real ranks).
      if (matched.length > 0) return matched;

      // Fallback: role names from auth store but unknown rank — treat as no access.
      return currentUser.roles.map((name: string, index: number) => ({
        id: `auth-${index}-${name}`,
        name,
        description: `Role: ${name}`,
        rank: 10,
        organizationId: undefined,
        isReadOnly: false,
      }));
    } catch {
      return [];
    }
  }

  // fetch permissions
  static async getPermissions(): Promise<Permission[]> {
    try {
      console.log('🔄 Fetching permissions from API...');
      const response = await ApiClient.get<any[]>('/permissions/');
      const permissions = response.map(permission => ({
        id: permission.id, // AUTH-06 return data like "asset_view"
        name: permission.name,
        description: permission.description,
        module: permission.module, 
        action: permission.action   
      }));
      console.log('✅ Permissions loaded from API:', permissions);
      return permissions;
    } catch (error) {
      console.warn('⚠️ Failed to fetch permissions from API:', error);
      return [];
    }
  }

  // fetch role-permissions
  static async getRolePermissions(roleId?: string): Promise<RolePermission[]> {
    try {
      console.log('🔄 Fetching role permissions from API...');
      const endpoint = roleId 
        ? `/role-permissions/?role_id=${roleId}`
        : '/role-permissions/';
      
      const response = await ApiClient.get<any[]>(endpoint);
      const rolePermissions = response.map(rp => ({
        roleId: rp.roleId || rp.role_id?.toString() || '',
        permissionId: rp.permissionId || rp.permission_id?.toString() || '',
        granted: rp.granted !== undefined ? rp.granted : true
      }));
      console.log('✅ Role permissions loaded from API:', rolePermissions);
      return rolePermissions;
    } catch (error) {
      console.warn('⚠️ Failed to fetch role permissions from API:', error);
      return [];
    }
  }

  static async getProjectPermissionMatrix(projectId: string): Promise<ProjectPermissionMatrix> {
    try {
      console.log('🔄 Fetching project permission matrix:', projectId);
      const response = await ApiClient.get<ProjectPermissionMatrix>(`/projects/${projectId}/permission-matrix/`);
      console.log('✅ Project permission matrix loaded:', response);
      return response;
    } catch (error) {
      console.error('❌ Failed to fetch project permission matrix:', error);
      throw error;
    }
  }

  // update role permissions
  static async updateRolePermissions(
    roleId: string, 
    permissions: { permissionId: string; granted: boolean }[]
  ): Promise<any> {
    console.log('🚀 updateRolePermissions START:', {
      roleId,
      permissionsCount: permissions.length,
      API_BASE_URL
    });

    try {
      console.log(`🔄 Sending POST request to /roles/${roleId}/permissions/`);
      const payload = {
        permissions: permissions.map(p => ({
          permissionId: p.permissionId, // AUTH-06 expected data
          granted: p.granted
        }))
      };
      console.log('📤 Request payload:', JSON.stringify(payload, null, 2));
      
      // AUTH-06 api format
      const response = await ApiClient.post(`/roles/${roleId}/permissions/`, payload);
      console.log('✅ Permissions updated successfully:', response);
      return response;
    } catch (error) {
      console.error('❌ Failed to update permissions:', error);
      throw new Error(error instanceof Error ? error.message : 'Failed to update permissions');
    }
  }

  // copy
  static async copyRolePermissions(fromRoleId: string, toRoleId: string): Promise<void> {
    try {
      console.log(`🔄 Copying permissions from role ${fromRoleId} to ${toRoleId}...`);
      // AUTH-06 api format
      const response = await ApiClient.post(`/roles/${toRoleId}/copy-permissions/`, {
        from_role_id: fromRoleId
      });
      console.log('✅ Permissions copied successfully:', response);
    } catch (error) {
      console.error('❌ Failed to copy permissions:', error);
      throw new Error(error instanceof Error ? error.message : 'Failed to copy permissions');
    }
  }

  // RolePermission[] transferred to  PermissionMatrix
  static buildPermissionMatrix(rolePermissions: RolePermission[]): PermissionMatrix {
    const matrix: PermissionMatrix = {};
    
    rolePermissions.forEach(rp => {
      if (!matrix[rp.roleId]) {
        matrix[rp.roleId] = {};
      }
      matrix[rp.roleId][rp.permissionId] = rp.granted;
    });
    
    return matrix;
  }

  // get role permissions from PermissionMatrix 
  static extractRolePermissions(
    matrix: PermissionMatrix, 
    roleId: string
  ): { permissionId: string; granted: boolean }[] {
    const rolePermissions = matrix[roleId] || {};
    return Object.entries(rolePermissions).map(([permissionId, granted]) => ({
      permissionId,
      granted
    }));
  }

  // check the changes of permission
  static hasPermissionChanges(
    original: PermissionMatrix,
    current: PermissionMatrix,
    roleId: string
  ): boolean {
    const originalPermissions = original[roleId] || {};
    const currentPermissions = current[roleId] || {};
    
    const allPermissionIds = new Set([
      ...Object.keys(originalPermissions),
      ...Object.keys(currentPermissions)
    ]);
    
    return Array.from(allPermissionIds).some(permissionId => 
      originalPermissions[permissionId] !== currentPermissions[permissionId]
    );
  }
}

export const apiConfig = {
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT,
};

export const permissionApiMethods = {
  getOrganizations: PermissionAPI.getOrganizations,
  getTeams: PermissionAPI.getTeams,
  getRoles: PermissionAPI.getRoles,
  getCurrentUserRoles: PermissionAPI.getCurrentUserRoles,
  getPermissions: PermissionAPI.getPermissions,
  getRolePermissions: PermissionAPI.getRolePermissions,
  getProjectPermissionMatrix: PermissionAPI.getProjectPermissionMatrix,
  updateRolePermissions: PermissionAPI.updateRolePermissions,
  copyRolePermissions: PermissionAPI.copyRolePermissions,
  buildPermissionMatrix: PermissionAPI.buildPermissionMatrix,
  extractRolePermissions: PermissionAPI.extractRolePermissions,
  hasPermissionChanges: PermissionAPI.hasPermissionChanges
};
