'use client';

import React, { useEffect } from 'react';
import { useAuthStore } from '../../lib/authStore';
import { useRouter } from 'next/navigation';

// Props for ProtectedRoute component
interface ProtectedRouteProps {
  children: React.ReactNode;
  requiredAuth?: boolean; // Whether authentication is required
  requiredRoles?: string[]; // Required roles for access
  requireAdmin?: boolean; // Require org admin or CSM admin
  requireSupervisor?: boolean; // Require CSM supervisor (admins and staff qualify)
  fallback?: string; // Redirect path if access is denied
  loadingComponent?: React.ReactNode; // Custom loading component
  renderChildrenWhileLoading?: boolean;
}

// ProtectedRoute component that handles authentication and role-based access control
export const ProtectedRoute: React.FC<ProtectedRouteProps> = ({
  children,
  requiredAuth = true,
  requiredRoles = [],
  requireAdmin = false,
  requireSupervisor = false,
  fallback = '/login',
  loadingComponent,
  renderChildrenWhileLoading = false,
}) => {
  const { isAuthenticated, user, loading, initialized } = useAuthStore();
  const router = useRouter();
  const authIsBooting = !initialized || loading;

  const hasRequiredRoles =
    requiredRoles.length === 0 ||
    Boolean(user?.roles && requiredRoles.some(role => user.roles.includes(role)));

  const isAdmin = Boolean(user?.is_org_admin || user?.is_csm_admin);
  const passesAdminCheck = !requireAdmin || isAdmin;

  // is_csm_supervisor already covers CSM admins and Django staff server-side.
  const isSupervisor = Boolean(user?.is_csm_supervisor);
  const passesSupervisorCheck = !requireSupervisor || isSupervisor;

  // Handle authentication and role checks
  useEffect(() => {
    // Wait for authentication to be initialized
    if (authIsBooting) return;

    // If authentication is required but user is not authenticated
    if (requiredAuth && !isAuthenticated) {
      router.push(fallback);
      return;
    }

    // If roles are required but user doesn't have them
    if (requiredRoles.length > 0 && !hasRequiredRoles) {
      router.push('/unauthorized');
      return;
    }

    // If admin is required but user is not admin
    if (requireAdmin && !passesAdminCheck) {
      router.push('/unauthorized');
      return;
    }

    // If supervisor is required but user does not supervise anything
    if (requireSupervisor && !passesSupervisorCheck) {
      router.push('/unauthorized');
      return;
    }
  }, [isAuthenticated, authIsBooting, requiredAuth, requiredRoles, requireAdmin, requireSupervisor, router, fallback, hasRequiredRoles, passesAdminCheck, passesSupervisorCheck]);

  // Show loading while authentication is being initialized
  if (authIsBooting) {
    if (renderChildrenWhileLoading) {
      return <>{children}</>;
    }
    if (loadingComponent) {
      return <>{loadingComponent}</>;
    }
    return null;
  }

  // If authentication is required but user is not authenticated, don't render children
  if (requiredAuth && !isAuthenticated) {
    return null;
  }

  // If roles are required but user doesn't have them, don't render children
  if (requiredRoles.length > 0 && !hasRequiredRoles) {
    return null;
  }

  // If admin required but not admin
  if (!passesAdminCheck) {
    return null;
  }

  // If supervisor required but not a supervisor
  if (!passesSupervisorCheck) {
    return null;
  }

  // Render children if all checks pass
  return <>{children}</>;
}; 
