import api from "../api";
import { CampaignData, CreateCampaignData, UpdateCampaignData, CampaignTaskLink, CampaignActivityTimelineItem, CampaignStatusHistoryItem, CampaignCheckIn, CreateCheckInData, UpdateCheckInData, PerformanceSnapshot, CreateSnapshotData, UpdateSnapshotData, CampaignTemplate, CreateTemplateData, UpdateTemplateData, CreateCampaignFromTemplateData, SaveCampaignAsTemplateData } from "@/types/campaign";

export const CampaignAPI = {
  // List campaigns with optional filters
  getCampaigns: (params?: {
    project?: string;
    status?: string;
    owner?: string;
    assignee?: string;
    search?: string;
  }) => {
    return api.get("/api/campaigns/", { params });
  },

  // Get single campaign
  getCampaign: (id: string) => api.get(`/api/campaigns/${id}/`),

  // Create campaign
  createCampaign: (data: CreateCampaignData) => api.post("/api/campaigns/", data),

  // Update campaign
  updateCampaign: (id: string, data: UpdateCampaignData) => 
    api.patch(`/api/campaigns/${id}/`, data),

  // Delete campaign
  deleteCampaign: (id: string) => api.delete(`/api/campaigns/${id}/`),

  // Get task links for a campaign
  getTaskLinks: (campaignId: string) => {
    return api.get("/api/campaign-task-links/", { params: { campaign: campaignId } });
  },

  // Get activity timeline for a campaign
  getActivityTimeline: (campaignId: string, params?: { page?: number; page_size?: number }) => {
    return api.get<CampaignActivityTimelineItem[]>(`/api/campaigns/${campaignId}/activity-timeline/`, { params });
  },

  // Get status history for a campaign
  getStatusHistory: (campaignId: string) => {
    return api.get<CampaignStatusHistoryItem[]>(`/api/campaigns/${campaignId}/status-history/`);
  },

  // Transition campaign status
  transitionStatus: (campaignId: string, transition: string, statusNote?: string) => {
    const urlMap: Record<string, string> = {
      'start-testing': `/api/campaigns/${campaignId}/start-testing/`,
      'start-scaling': `/api/campaigns/${campaignId}/start-scaling/`,
      'start-optimizing': `/api/campaigns/${campaignId}/start-optimizing/`,
      'pause': `/api/campaigns/${campaignId}/pause/`,
      'resume': `/api/campaigns/${campaignId}/resume/`,
      'complete': `/api/campaigns/${campaignId}/complete/`,
      'archive': `/api/campaigns/${campaignId}/archive/`,
      'restore': `/api/campaigns/${campaignId}/restore/`,
    };

    const url = urlMap[transition];
    if (!url) {
      throw new Error(`Unknown transition: ${transition}`);
    }

    return api.post<CampaignData>(url, { status_note: statusNote || '' });
  },

  // Get check-ins for a campaign
  getCheckIns: (campaignId: string, params?: { sentiment?: string }) => {
    return api.get<CampaignCheckIn[]>(`/api/campaigns/${campaignId}/check-ins/`, { params });
  },

  // Create a new check-in
  createCheckIn: (campaignId: string, data: CreateCheckInData) => {
    return api.post<CampaignCheckIn>(`/api/campaigns/${campaignId}/check-ins/`, data);
  },

  // Update a check-in
  updateCheckIn: (campaignId: string, checkInId: string, data: UpdateCheckInData) => {
    return api.patch<CampaignCheckIn>(`/api/campaigns/${campaignId}/check-ins/${checkInId}/`, data);
  },

  // Delete a check-in
  deleteCheckIn: (campaignId: string, checkInId: string) => {
    return api.delete(`/api/campaigns/${campaignId}/check-ins/${checkInId}/`);
  },

  // Get performance snapshots for a campaign
  getSnapshots: (campaignId: string, params?: { milestone_type?: string; metric_type?: string }) => {
    return api.get<PerformanceSnapshot[]>(`/api/campaigns/${campaignId}/performance-snapshots/`, { params });
  },

  // Get a single performance snapshot
  getSnapshot: (campaignId: string, snapshotId: string) => {
    return api.get<PerformanceSnapshot>(`/api/campaigns/${campaignId}/performance-snapshots/${snapshotId}/`);
  },

  // Create a new performance snapshot
  createSnapshot: (campaignId: string, data: CreateSnapshotData) => {
    // If screenshot is provided, use FormData; otherwise use JSON
    if (data.screenshot) {
      const formData = new FormData();
      formData.append('milestone_type', data.milestone_type);
      formData.append('spend', data.spend.toString());
      formData.append('metric_type', data.metric_type);
      formData.append('metric_value', data.metric_value.toString());
      if (data.percentage_change !== undefined) {
        formData.append('percentage_change', data.percentage_change.toString());
      }
      if (data.notes) {
        formData.append('notes', data.notes);
      }
      formData.append('screenshot', data.screenshot);
      if (data.additional_metrics) {
        formData.append('additional_metrics', JSON.stringify(data.additional_metrics));
      }
      return api.post<PerformanceSnapshot>(`/api/campaigns/${campaignId}/performance-snapshots/`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
    } else {
      // No file, use JSON
      const jsonData: any = {
        milestone_type: data.milestone_type,
        spend: data.spend,
        metric_type: data.metric_type,
        metric_value: data.metric_value,
      };
      if (data.percentage_change !== undefined) {
        jsonData.percentage_change = data.percentage_change;
      }
      if (data.notes) {
        jsonData.notes = data.notes;
      }
      if (data.additional_metrics) {
        jsonData.additional_metrics = data.additional_metrics;
      }
      return api.post<PerformanceSnapshot>(`/api/campaigns/${campaignId}/performance-snapshots/`, jsonData);
    }
  },

  // Update a performance snapshot
  updateSnapshot: (campaignId: string, snapshotId: string, data: UpdateSnapshotData) => {
    // If screenshot is provided, use FormData; otherwise use JSON
    if (data.screenshot) {
      const formData = new FormData();
      if (data.milestone_type) {
        formData.append('milestone_type', data.milestone_type);
      }
      if (data.spend !== undefined) {
        formData.append('spend', data.spend.toString());
      }
      if (data.metric_type) {
        formData.append('metric_type', data.metric_type);
      }
      if (data.metric_value !== undefined) {
        formData.append('metric_value', data.metric_value.toString());
      }
      if (data.percentage_change !== undefined) {
        formData.append('percentage_change', data.percentage_change.toString());
      }
      if (data.notes !== undefined) {
        formData.append('notes', data.notes || '');
      }
      formData.append('screenshot', data.screenshot);
      if (data.additional_metrics !== undefined) {
        formData.append('additional_metrics', JSON.stringify(data.additional_metrics));
      }
      return api.patch<PerformanceSnapshot>(`/api/campaigns/${campaignId}/performance-snapshots/${snapshotId}/`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
    } else {
      // No file, use JSON
      const jsonData: any = {};
      if (data.milestone_type) {
        jsonData.milestone_type = data.milestone_type;
      }
      if (data.spend !== undefined) {
        jsonData.spend = data.spend;
      }
      if (data.metric_type) {
        jsonData.metric_type = data.metric_type;
      }
      if (data.metric_value !== undefined) {
        jsonData.metric_value = data.metric_value;
      }
      if (data.percentage_change !== undefined) {
        jsonData.percentage_change = data.percentage_change;
      }
      if (data.notes !== undefined) {
        jsonData.notes = data.notes;
      }
      if (data.additional_metrics !== undefined) {
        jsonData.additional_metrics = data.additional_metrics;
      }
      return api.patch<PerformanceSnapshot>(`/api/campaigns/${campaignId}/performance-snapshots/${snapshotId}/`, jsonData);
    }
  },

  // Delete a performance snapshot
  deleteSnapshot: (campaignId: string, snapshotId: string) => {
    return api.delete(`/api/campaigns/${campaignId}/performance-snapshots/${snapshotId}/`);
  },

  // Upload screenshot for a performance snapshot
  uploadScreenshot: (campaignId: string, snapshotId: string, file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post<PerformanceSnapshot>(`/api/campaigns/${campaignId}/performance-snapshots/${snapshotId}/screenshot/`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
  },

  // Template CRUD
  getTemplates: (params?: {
    sharing_scope?: string;
    project?: string;
    creator?: string;
    search?: string;
  }) => {
    return api.get<CampaignTemplate[]>('/api/campaign-templates/', { params });
  },

  getTemplate: (id: string) => {
    return api.get<CampaignTemplate>(`/api/campaign-templates/${id}/`);
  },

  createTemplate: (data: CreateTemplateData) => {
    return api.post<CampaignTemplate>('/api/campaign-templates/', data);
  },

  updateTemplate: (id: string, data: UpdateTemplateData) => {
    return api.patch<CampaignTemplate>(`/api/campaign-templates/${id}/`, data);
  },

  deleteTemplate: (id: string) => {
    return api.delete(`/api/campaign-templates/${id}/`);
  },

  // Create campaign from template
  createCampaignFromTemplate: (templateId: string, data: CreateCampaignFromTemplateData) => {
    return api.post<CampaignData>(`/api/campaign-templates/${templateId}/create-campaign/`, data);
  },

  // Save campaign as template
  saveCampaignAsTemplate: (campaignId: string, data: SaveCampaignAsTemplateData) => {
    return api.post<CampaignTemplate>(`/api/campaigns/${campaignId}/save-as-template/`, data);
  },
};
