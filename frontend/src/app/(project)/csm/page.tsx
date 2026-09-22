'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useSearchParams } from 'next/navigation';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import { useProjectStore } from '@/lib/projectStore';
import { useAuthStore } from '@/lib/authStore';
import { useBuildUrl } from '@/lib/buildUrl';
import { Headset, Building2 } from 'lucide-react';
import { OrganisationAPI } from '@/lib/api/organisationAPI';
import QueuesTab from '@/components/csm/QueuesTab';
import UsersTab from '@/components/csm/UsersTab';
import RegionsTab from '@/components/csm/RegionsTab';
import OrganisationsTab from '@/components/csm/OrganisationsTab';
import CsmSectionTabs, { CSM_SECTION_TABS } from '@/components/csm/CsmSectionTabs';

const TABS = CSM_SECTION_TABS;

type TabId = (typeof TABS)[number]['id'];

const CSMPageContent: React.FC = () => {
  const searchParams = useSearchParams();
  const buildUrl = useBuildUrl();
  const activeProject = useProjectStore((s) => s.activeProject);
  const user = useAuthStore((s) => s.user);
  const isCsmAdmin = user?.is_csm_admin || (Array.isArray(user?.roles) && user.roles.some((r: string) => r.toLowerCase().includes('admin') || r.toLowerCase().includes('owner')));

  const projectId = activeProject?.id ?? '';
  const projectValid = !!projectId;

  const initialTab = (searchParams.get('tab') as TabId) || 'organisations';
  const [activeTab, setActiveTab] = useState<TabId>(
    TABS.some((t) => t.id === initialTab) ? initialTab : 'organisations'
  );

  // Organisation selector state
  const [adminOrgs, setAdminOrgs] = useState<{ id: number; name: string }[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<number | null>(null);
  const [orgsLoading, setOrgsLoading] = useState(true);

  const fetchAdminOrgs = useCallback(async () => {
    setOrgsLoading(true);
    try {
      const res = await OrganisationAPI.myAdminOrgs();
      const data = Array.isArray(res.data) ? res.data : [];
      setAdminOrgs(data);
      if (data.length > 0) {
        const ids = data.map((o: { id: number }) => o.id);
        setSelectedOrgId((prev) => (prev !== null && ids.includes(prev)) ? prev : ids[0]);
      }
    } catch {
      setAdminOrgs([]);
    } finally {
      setOrgsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAdminOrgs();
  }, [fetchAdminOrgs]);

  // Re-fetch admin orgs when switching to a tab that needs the org selector
  const needsOrgFilter = activeTab !== 'organisations';

  useEffect(() => {
    if (needsOrgFilter) {
      fetchAdminOrgs();
    }
  }, [needsOrgFilter, fetchAdminOrgs]);

  const tabContent = useMemo(() => {
    if (!projectValid) return null;
    switch (activeTab) {
      case 'organisations':
        return <OrganisationsTab projectId={projectId} />;
      case 'queues':
        return selectedOrgId ? <QueuesTab projectId={projectId} organisationId={selectedOrgId} /> : null;
      case 'regions':
        return selectedOrgId ? <RegionsTab projectId={projectId} organisationId={selectedOrgId} /> : null;
      case 'users':
        return selectedOrgId ? <UsersTab projectId={projectId} organisationId={selectedOrgId} /> : null;
    }
  }, [activeTab, projectId, projectValid, selectedOrgId]);

  return (
    <DashboardLayout hideRightPanel>
      <div className="flex flex-col gap-6">
        {/* Page Header */}
        <div>
          <div className="flex items-center gap-3 mb-1">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-gradient-to-br from-[#3CCED7] to-[#A6E661]">
              <Headset className="h-5 w-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Customer Service</h1>
              <p className="text-sm text-gray-500">
                Manage queues, organisations, regions, and users.
              </p>
            </div>
          </div>
        </div>

        {!projectValid ? (
          <div className="flex flex-col items-center justify-center min-h-[300px] gap-3 text-center">
            <p className="text-sm text-gray-600">
              No active project selected. Please select a project first.
            </p>
            <a
              href="/select-project"
              className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
            >
              Go to projects
            </a>
          </div>
        ) : (
          <>
            {/* Tabs Navigation — shared with the settings pages */}
            <CsmSectionTabs
              activeTab={activeTab}
              onSelectTab={setActiveTab}
              showSettings={!!isCsmAdmin && projectValid}
            />

            {/* Organisation Selector — shown for queues/regions/users tabs */}
            {needsOrgFilter && (
              <div className="flex items-center gap-3">
                <Building2 className="h-4 w-4 text-gray-400" />
                <label className="text-sm font-medium text-gray-700">Organisation:</label>
                {orgsLoading ? (
                  <span className="text-sm text-gray-400">Loading...</span>
                ) : adminOrgs.length === 0 ? (
                  <span className="text-sm text-gray-400">No organisations available</span>
                ) : (
                  <select
                    value={selectedOrgId ?? ''}
                    onChange={(e) => setSelectedOrgId(Number(e.target.value))}
                    className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
                  >
                    {adminOrgs.map((org) => (
                      <option key={org.id} value={org.id}>{org.name}</option>
                    ))}
                  </select>
                )}
              </div>
            )}

            {/* Tab Content */}
            <div>{tabContent}</div>
          </>
        )}
      </div>
    </DashboardLayout>
  );
};

export default function CSMPage() {
  return (
    <ProtectedRoute requiredAuth={true}>
      <CSMPageContent />
    </ProtectedRoute>
  );
}
