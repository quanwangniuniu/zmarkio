'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import toast from 'react-hot-toast';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import ChatFAB from '@/components/global-chat/ChatFAB';
import { Button } from '@/components/ui/button';
import { useActiveProjectForFlatRoute } from '@/lib/useActiveProjectForFlatRoute';
import { useBuildUrl } from '@/lib/buildUrl';
import { useCampaignData } from '@/hooks/useCampaignData';
import type { CampaignCheckIn, CampaignData, PerformanceSnapshot } from '@/types/campaign';
import CampaignHeader from '@/components/campaigns/CampaignHeader';
import CampaignSyncBanner from '@/components/campaigns/CampaignSyncBanner';
import TimelineSection, { type TimelineSectionHandle } from '@/components/campaigns/sections/TimelineSection';
import CheckInsSection, { type CheckInsSectionHandle } from '@/components/campaigns/sections/CheckInsSection';
import SnapshotsSection, { type SnapshotsSectionHandle } from '@/components/campaigns/sections/SnapshotsSection';
import StatusHistorySection, { type StatusHistorySectionHandle } from '@/components/campaigns/sections/StatusHistorySection';
import TasksSection, { type TasksSectionHandle } from '@/components/campaigns/sections/TasksSection';
import CampaignFSMActionBar from '@/components/campaigns/detail/CampaignFSMActionBar';
import CreateCheckInDialog from '@/components/campaigns/modals/CreateCheckInDialog';
import EditCheckInDialog from '@/components/campaigns/modals/EditCheckInDialog';
import CreateSnapshotDialog from '@/components/campaigns/modals/CreateSnapshotDialog';
import EditSnapshotDialog from '@/components/campaigns/modals/EditSnapshotDialog';
import SaveAsTemplateDialog from '@/components/campaigns/modals/SaveAsTemplateDialog';

export default function CampaignV2DetailPage() {
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const params = useParams();
  const campaignId = params?.slug as string;
  const { projectId } = useActiveProjectForFlatRoute();

  const { currentCampaign, loading, error, fetchCampaign, updateCampaign } = useCampaignData();

  const [createCheckInOpen, setCreateCheckInOpen] = useState(false);
  const [editCheckInOpen, setEditCheckInOpen] = useState(false);
  const [selectedCheckIn, setSelectedCheckIn] = useState<CampaignCheckIn | null>(null);
  const checkInsRef = useRef<CheckInsSectionHandle>(null);
  const [createSnapshotOpen, setCreateSnapshotOpen] = useState(false);
  const [editSnapshotOpen, setEditSnapshotOpen] = useState(false);
  const [selectedSnapshot, setSelectedSnapshot] = useState<PerformanceSnapshot | null>(null);
  const snapshotsRef = useRef<SnapshotsSectionHandle>(null);
  const timelineRef = useRef<TimelineSectionHandle>(null);
  const tasksRef = useRef<TasksSectionHandle>(null);
  const [saveAsTemplateOpen, setSaveAsTemplateOpen] = useState(false);
  const statusHistoryRef = useRef<StatusHistorySectionHandle>(null);

  useEffect(() => {
    if (!campaignId) return;
    fetchCampaign(campaignId).catch((err) => {
      console.error('Failed to fetch campaign:', err);
      toast.error('Failed to load campaign');
    });
  }, [campaignId, fetchCampaign]);

  const handleUpdate = async (data: Parameters<typeof updateCampaign>[1]) => {
    if (!campaignId) return;
    try {
      await updateCampaign(campaignId, data);
      toast.success('Campaign updated');
      await fetchCampaign(campaignId);
    } catch (err: any) {
      const msg = err?.response?.data?.error || err?.message || 'Failed to update campaign';
      toast.error(msg);
      throw err;
    }
  };

  const handleBack = () => router.push(buildUrl('/campaigns'));

  const isArchived = currentCampaign?.status === 'ARCHIVED';

  const guardEditable = useCallback((): boolean => {
    if (isArchived) {
      toast.error('Archived campaigns cannot be edited. Restore to Completed first.');
      return false;
    }
    return true;
  }, [isArchived]);

  const handleCheckInCreate = () => {
    if (!guardEditable()) return;
    setCreateCheckInOpen(true);
  };

  const handleCheckInEdit = (checkIn: CampaignCheckIn) => {
    if (!guardEditable()) return;
    if (!checkIn?.id) return;
    setSelectedCheckIn(checkIn);
    setEditCheckInOpen(true);
  };

  const handleSnapshotCreate = () => {
    if (!guardEditable()) return;
    setCreateSnapshotOpen(true);
  };

  const handleSnapshotEdit = (snapshot: PerformanceSnapshot) => {
    if (!guardEditable()) return;
    if (!snapshot?.id) return;
    setSelectedSnapshot(snapshot);
    setEditSnapshotOpen(true);
  };

  const handleFSMTransitioned = (next: CampaignData) => {
    // optimistic update via direct fetch for related sections
    void fetchCampaign(campaignId);
    statusHistoryRef.current?.refresh();
    timelineRef.current?.refresh();
  };

  if (loading) {
    return (
      <DashboardLayout>
        <div className="flex items-center justify-center py-24">
          <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-[#3CCED7]" />
          <span className="ml-3 text-sm text-gray-600">Loading campaign...</span>
        </div>
      </DashboardLayout>
    );
  }

  if (error || !currentCampaign) {
    return (
      <DashboardLayout>
        <div className="px-6 py-5">
          <Button variant="ghost" size="sm" onClick={handleBack} className="mb-4">
            <ArrowLeft className="h-4 w-4" />
            Back to Campaigns
          </Button>
          <div className="rounded-md border border-rose-200 bg-rose-50 p-4">
            <p className="text-sm text-rose-800">
              {error?.response?.data?.error || error?.message || 'Failed to load campaign'}
            </p>
          </div>
        </div>
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout>
      <div className="px-6 py-5">
        <Button variant="ghost" size="sm" onClick={handleBack} className="mb-4">
          <ArrowLeft className="h-4 w-4" />
          Back to Campaigns
        </Button>

        <CampaignSyncBanner
          campaignSlug={campaignId}
          integrations={currentCampaign.platform_integrations}
        />

        <CampaignHeader
          campaign={currentCampaign}
          onUpdate={handleUpdate}
          loading={loading}
          onSaveAsTemplate={() => setSaveAsTemplateOpen(true)}
        />

        <div className="mt-5">
          <CampaignFSMActionBar
            campaign={currentCampaign}
            onTransitioned={handleFSMTransitioned}
          />
        </div>

        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <SnapshotsSection
              ref={snapshotsRef}
              campaignId={campaignId}
              onEdit={handleSnapshotEdit}
              onCreate={handleSnapshotCreate}
              isArchived={isArchived}
            />
            <TimelineSection ref={timelineRef} campaignId={campaignId} />
            <TasksSection ref={tasksRef} campaignId={campaignId} />
          </div>
          <div className="space-y-6 lg:col-span-1">
            <CheckInsSection
              ref={checkInsRef}
              campaignId={campaignId}
              onEdit={handleCheckInEdit}
              onCreate={handleCheckInCreate}
              isArchived={isArchived}
            />
            <StatusHistorySection ref={statusHistoryRef} campaignId={campaignId} />
          </div>
        </div>

        <CreateCheckInDialog
          open={createCheckInOpen}
          onOpenChange={setCreateCheckInOpen}
          campaignId={campaignId}
          onSuccess={() => {
            checkInsRef.current?.refresh();
            timelineRef.current?.refresh();
          }}
        />
        <EditCheckInDialog
          open={editCheckInOpen}
          onOpenChange={(next) => {
            setEditCheckInOpen(next);
            if (!next) setSelectedCheckIn(null);
          }}
          campaignId={campaignId}
          checkIn={selectedCheckIn}
          onSuccess={() => {
            checkInsRef.current?.refresh();
            timelineRef.current?.refresh();
          }}
        />
        <CreateSnapshotDialog
          open={createSnapshotOpen}
          onOpenChange={setCreateSnapshotOpen}
          campaignId={campaignId}
          onSuccess={() => {
            snapshotsRef.current?.refresh();
            timelineRef.current?.refresh();
          }}
        />
        <EditSnapshotDialog
          open={editSnapshotOpen}
          onOpenChange={(next) => {
            setEditSnapshotOpen(next);
            if (!next) setSelectedSnapshot(null);
          }}
          campaignId={campaignId}
          snapshot={selectedSnapshot}
          onSuccess={() => {
            snapshotsRef.current?.refresh();
            timelineRef.current?.refresh();
          }}
          onDelete={() => {
            snapshotsRef.current?.refresh();
            timelineRef.current?.refresh();
          }}
        />
        <SaveAsTemplateDialog
          open={saveAsTemplateOpen}
          onOpenChange={setSaveAsTemplateOpen}
          campaignId={campaignId}
          campaignName={currentCampaign.name}
        />
      </div>
      <ChatFAB />
    </DashboardLayout>
  );
}
