'use client';

import React, { useState, useEffect } from 'react';
import { CampaignData, CampaignObjective, CampaignPlatform } from '@/types/campaign';
import CampaignStatusBadge from './CampaignStatusBadge';
import { Badge } from '@/components/ui/badge';
import InlineEditController from '@/inline-edit/InlineEditController';
import InlineSelectController from '@/inline-edit/InlineSelectController';
import InlineMultiSelectController from '@/inline-edit/InlineMultiSelectController';
import InlineDateController from '@/inline-edit/InlineDateController';
import InlineUserSelector from '@/inline-edit/InlineUserSelector';
import UserAvatar from '@/people/UserAvatar';
import { User } from '@/people/UserPicker';
import Button from '@/components/button/Button';
import { Calendar, User as UserIcon, FolderOpen, Settings, Save, Wallet } from 'lucide-react';
import { ProjectAPI } from '@/lib/api/projectApi';
import { DecorativeGlow } from '@/components/ui/decorative-glow';

interface CampaignHeaderProps {
  campaign: CampaignData;
  onUpdate: (data: {
    name?: string;
    objective?: CampaignObjective;
    platforms?: CampaignPlatform[];
    start_date?: string;
    end_date?: string;
    hypothesis?: string;
    owner_id?: number;
    budget_estimate?: number | null;
  }) => Promise<void>;
  loading?: boolean;
  onChangeStatus?: () => void;
  onSaveAsTemplate?: () => void;
}

const objectiveLabels: Record<string, string> = {
  AWARENESS: 'Awareness',
  CONSIDERATION: 'Consideration',
  CONVERSION: 'Conversion',
  RETENTION: 'Retention',
  ENGAGEMENT: 'Engagement',
  TRAFFIC: 'Traffic',
  LEAD_GENERATION: 'Lead Gen',
  APP_PROMOTION: 'App Promotion',
};

const objectiveOptions: Array<{ value: CampaignObjective; label: string }> = [
  { value: 'AWARENESS', label: 'Awareness' },
  { value: 'CONSIDERATION', label: 'Consideration' },
  { value: 'CONVERSION', label: 'Conversion' },
  { value: 'RETENTION', label: 'Retention' },
  { value: 'ENGAGEMENT', label: 'Engagement' },
  { value: 'TRAFFIC', label: 'Traffic' },
  { value: 'LEAD_GENERATION', label: 'Lead Gen' },
  { value: 'APP_PROMOTION', label: 'App Promotion' },
];

const platformLabels: Record<string, string> = {
  META: 'Meta',
  GOOGLE_ADS: 'Google Ads',
  TIKTOK: 'TikTok',
  LINKEDIN: 'LinkedIn',
  SNAPCHAT: 'Snapchat',
  TWITTER: 'Twitter',
  PINTEREST: 'Pinterest',
  REDDIT: 'Reddit',
  PROGRAMMATIC: 'Programmatic',
  EMAIL: 'Email',
};

const platformOptions: Array<{ value: CampaignPlatform; label: string }> = [
  { value: 'META', label: 'Meta' },
  { value: 'GOOGLE_ADS', label: 'Google Ads' },
  { value: 'TIKTOK', label: 'TikTok' },
  { value: 'LINKEDIN', label: 'LinkedIn' },
  { value: 'SNAPCHAT', label: 'Snapchat' },
  { value: 'TWITTER', label: 'Twitter' },
  { value: 'PINTEREST', label: 'Pinterest' },
  { value: 'REDDIT', label: 'Reddit' },
  { value: 'PROGRAMMATIC', label: 'Programmatic' },
  { value: 'EMAIL', label: 'Email' },
];

const formatDate = (dateString: string) => {
  const date = new Date(dateString);
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric'
  });
};

/**
 * Accepts "30000", "30,000" or "$30,000"; blank means "clear the budget".
 * Rounded to cents because the backend rejects more than 2 decimal places.
 */
const parseBudget = (raw: string): number | null => {
  const cleaned = raw.replace(/[$,\s]/g, '');
  if (cleaned === '') return null;
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? Math.round(parsed * 100) / 100 : NaN;
};

const formatBudget = (value: number) =>
  value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });

export default function CampaignHeader({ campaign, onUpdate, loading, onChangeStatus, onSaveAsTemplate }: CampaignHeaderProps) {
  const [users, setUsers] = useState<User[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);

  // Fetch users when component mounts or project changes
  useEffect(() => {
    const fetchUsers = async () => {
      // Project routes are slug-only; prefer the project slug, fall back to numeric id.
      const projectId = campaign.project?.slug || campaign.project_id || campaign.project?.id;
      
      if (!projectId) {
        console.warn('No project ID available for fetching members');
        setUsers([]);
        return;
      }

      try {
        setLoadingUsers(true);
        console.log('Fetching project members for project:', projectId);
        const members = await ProjectAPI.getProjectMembers(projectId);
        console.log('Received members:', members);
        
        const userList: User[] = members
          .filter((member) => member.is_active) // Only include active members
          .map((member) => ({
            id: member.user.id,
            name: member.user.name || member.user.username || member.user.email || 'Unknown',
            email: member.user.email || '',
          }));
        
        console.log('Mapped user list:', userList);
        setUsers(userList);
      } catch (error) {
        console.error('Error fetching users:', error);
        // Show more detailed error information
        if (error instanceof Error) {
          console.error('Error message:', error.message);
          console.error('Error stack:', error.stack);
        }
        setUsers([]);
      } finally {
        setLoadingUsers(false);
      }
    };

    fetchUsers();
  }, [campaign.project_id, campaign.project?.id]);

  const handleNameSave = async (newName: string) => {
    if (newName.trim() === campaign.name) {
      return; // No change
    }
    await onUpdate({ name: newName.trim() });
  };

  const handleObjectiveSave = async (newObjective: CampaignObjective) => {
    // Handle both undefined and defined cases
    if (newObjective === campaign.objective) {
      return; // No change
    }
    await onUpdate({ objective: newObjective });
  };

  const handlePlatformsSave = async (newPlatforms: CampaignPlatform[]) => {
    if (JSON.stringify(newPlatforms.sort()) === JSON.stringify(campaign.platforms.sort())) {
      return; // No change
    }
    await onUpdate({ platforms: newPlatforms });
  };

  const handleStartDateSave = async (newStartDate: string | null) => {
    // Normalize date format (take only date part, remove time)
    const normalizeDate = (dateStr: string | null | undefined): string | null => {
      if (!dateStr || (typeof dateStr === 'string' && dateStr.trim() === '')) return null;
      // If it's ISO format, take only the date part
      const normalized = typeof dateStr === 'string' ? dateStr.split('T')[0] : null;
      return normalized || null;
    };
    
    const currentStartDate = normalizeDate(campaign.start_date);
    const normalizedNewDate = normalizeDate(newStartDate);
    
    // Compare normalized dates
    if (normalizedNewDate === currentStartDate) {
      return; // No change
    }
    
    // Save the normalized date (or undefined if null)
    await onUpdate({ start_date: normalizedNewDate || undefined });
  };

  const handleEndDateSave = async (newEndDate: string | null) => {
    const currentEndDate = campaign.end_date || null;
    if (newEndDate === currentEndDate) {
      return; // No change
    }
    await onUpdate({ end_date: newEndDate || undefined });
  };

  const handleBudgetSave = async (newBudget: string) => {
    const parsed = parseBudget(newBudget);
    const current = campaign.budget_estimate != null ? Number(campaign.budget_estimate) : null;
    if (parsed === current) {
      return; // No change
    }
    await onUpdate({ budget_estimate: parsed });
  };

  const handleOwnerSave = async (newOwnerId: number | null) => {
    const currentOwnerId = campaign.owner_id;
    if (newOwnerId === currentOwnerId) {
      return; // No change
    }
    await onUpdate({ owner_id: newOwnerId || undefined });
  };

  const handleHypothesisSave = async (newHypothesis: string) => {
    const trimmedHypothesis = newHypothesis.trim() || undefined;
    if (trimmedHypothesis === campaign.hypothesis) {
      return; // No change
    }
    await onUpdate({ hypothesis: trimmedHypothesis });
  };

  const validateName = (value: string): string | null => {
    if (!value.trim()) {
      return 'Campaign name is required';
    }
    if (value.trim().length < 3) {
      return 'Campaign name must be at least 3 characters';
    }
    return null;
  };

  const validatePlatforms = (value: CampaignPlatform[]): string | null => {
    if (value.length === 0) {
      return 'At least one platform must be selected';
    }
    return null;
  };

  const validateStartDate = (value: string | null): string | null => {
    if (value && campaign.end_date && value > campaign.end_date) {
      return 'Start date must be before end date';
    }
    return null;
  };

  const validateEndDate = (value: string | null): string | null => {
    if (value && campaign.start_date && value < campaign.start_date) {
      return 'End date must be after start date';
    }
    return null;
  };

  const validateBudget = (value: string): string | null => {
    const parsed = parseBudget(value);
    if (parsed === null) return null; // blank clears the budget
    if (Number.isNaN(parsed) || parsed <= 0) {
      return 'Budget must be a positive number';
    }
    return null;
  };

  const ownerName = campaign.owner?.username || campaign.owner?.email || 'Unknown';
  const ownerDisplay = {
    name: ownerName,
    email: campaign.owner?.email,
  };

  const isArchived = campaign.status === 'ARCHIVED';

  return (
    <div className="relative overflow-hidden bg-white rounded-lg border border-gray-200 p-6 mb-6">
      <DecorativeGlow variant="subtle" />
      {/* Campaign Name with Inline Editing */}
      <div className="mb-4">
        {isArchived ? (
          <h1 className="text-2xl font-bold text-gray-900">{campaign.name}</h1>
        ) : (
          <InlineEditController
            value={campaign.name}
            onSave={handleNameSave}
            validate={validateName}
            inputType="input"
            className="text-2xl font-bold text-gray-900"
            renderTrigger={(value) => (
              <h1 className="text-2xl font-bold text-gray-900 hover:text-[#0E8A96] transition-colors cursor-pointer">
                {value}
              </h1>
            )}
          />
        )}
      </div>

      {/* Metadata Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* Status */}
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-500">Status:</span>
          <CampaignStatusBadge status={campaign.status} />
          {campaign.status !== 'ARCHIVED' && onChangeStatus && (
            <Button
              variant="secondary"
              size="sm"
              onClick={onChangeStatus}
              leftIcon={<Settings className="h-3 w-3" />}
              className="ml-2"
            >
              Change
            </Button>
          )}
          {campaign.status !== 'ARCHIVED' && onSaveAsTemplate && (
            <Button
              variant="secondary"
              size="sm"
              onClick={onSaveAsTemplate}
              leftIcon={<Save className="h-3 w-3" />}
              className="ml-2"
            >
              Save as Template
            </Button>
          )}
        </div>

        {/* Objective */}
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-500">Objective:</span>
          {isArchived ? (
            <span className="text-sm text-gray-900">
              {campaign.objective ? (objectiveLabels[campaign.objective] || campaign.objective) : 'Not set'}
            </span>
          ) : (
            <InlineSelectController
              value={campaign.objective}
              options={objectiveOptions}
              onSave={handleObjectiveSave}
              className="text-sm text-gray-900"
              placeholder="Select objective"
            />
          )}
        </div>

        {/* Platforms */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium text-gray-500">Platforms:</span>
          {isArchived ? (
            <div className="flex flex-wrap gap-1">
              {campaign.platforms.map((platform) => (
                <Badge key={platform} variant="outline" className="text-xs">
                  {platformLabels[platform] || platform}
                </Badge>
              ))}
            </div>
          ) : (
            <InlineMultiSelectController
              value={campaign.platforms}
              options={platformOptions}
              onSave={handlePlatformsSave}
              validate={validatePlatforms}
              className="text-sm"
            />
          )}
        </div>

        {/* Start Date */}
        <div className="flex items-center gap-2">
          <Calendar className="h-4 w-4 text-gray-400" />
          <span className="text-sm font-medium text-gray-500">Start:</span>
          {isArchived ? (
            <span className="text-sm text-gray-900">{formatDate(campaign.start_date)}</span>
          ) : (
            <InlineDateController
              value={campaign.start_date}
              onSave={handleStartDateSave}
              validate={validateStartDate}
              maxDate={campaign.end_date}
              placeholder="Not set"
              className="text-sm text-gray-900"
            />
          )}
        </div>

        {/* End Date */}
        <div data-testid="campaign-end-date" className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-500">End:</span>
          {isArchived ? (
            <span className="text-sm text-gray-900">{campaign.end_date ? formatDate(campaign.end_date) : 'Not set'}</span>
          ) : (
            <InlineDateController
              value={campaign.end_date}
              onSave={handleEndDateSave}
              validate={validateEndDate}
              minDate={campaign.start_date}
              placeholder="Not set"
              className="text-sm text-gray-900"
            />
          )}
        </div>

        {/* Owner */}
        <div className="flex items-center gap-2">
          <UserIcon className="h-4 w-4 text-gray-400" />
          <span className="text-sm font-medium text-gray-500">Owner:</span>
          {isArchived ? (
            <div className="flex items-center gap-2">
              <UserAvatar user={ownerDisplay} size="sm" />
              <span className="text-sm text-gray-900">{ownerName}</span>
            </div>
          ) : (
            <InlineUserSelector
              value={campaign.owner_id}
              users={users}
              onSave={handleOwnerSave}
              loading={loadingUsers}
              currentUser={campaign.owner}
              placeholder="Select owner"
              className="text-sm text-gray-900"
            />
          )}
        </div>

        {/* Project */}
        <div className="flex items-center gap-2">
          <FolderOpen className="h-4 w-4 text-gray-400" />
          <span className="text-sm font-medium text-gray-500">Project:</span>
          <span className="text-sm text-gray-900">{campaign.project?.name || 'N/A'}</span>
        </div>

        {/* Budget — editable here so the pacing panel's "add a budget" prompt is actionable */}
        <div className="flex items-center gap-2">
          <Wallet className="h-4 w-4 text-gray-400" />
          <span className="text-sm font-medium text-gray-500">Budget:</span>
          {isArchived ? (
            <span className="text-sm text-gray-900">
              {campaign.budget_estimate != null ? formatBudget(Number(campaign.budget_estimate)) : 'Not set'}
            </span>
          ) : (
            <InlineEditController
              value={campaign.budget_estimate != null ? String(Number(campaign.budget_estimate)) : ''}
              onSave={handleBudgetSave}
              validate={validateBudget}
              inputType="input"
              placeholder="e.g. 30000"
              className="text-sm text-gray-900"
              renderTrigger={(value) => {
                const parsed = parseBudget(value);
                return (
                  <span
                    data-testid="campaign-budget"
                    className="text-sm text-gray-900 hover:text-[#0E8A96] transition-colors cursor-pointer"
                  >
                    {parsed !== null && !Number.isNaN(parsed) ? (
                      formatBudget(parsed)
                    ) : (
                      <span className="text-gray-400 italic">Not set</span>
                    )}
                  </span>
                );
              }}
            />
          )}
        </div>
      </div>

      {/* Hypothesis */}
      <div className="mt-4 pt-4 border-t border-gray-200">
        <p className="text-sm font-medium text-gray-500 mb-1">Hypothesis:</p>
        {isArchived ? (
          <p className="text-sm text-gray-700 min-h-[1.5rem]">
            {campaign.hypothesis || <span className="text-gray-400 italic">No hypothesis set</span>}
          </p>
        ) : (
          <InlineEditController
            value={campaign.hypothesis || ''}
            onSave={handleHypothesisSave}
            inputType="textarea"
            placeholder="Add a hypothesis for this campaign..."
            className="text-sm text-gray-700"
            renderTrigger={(value) => (
              <p className="text-sm text-gray-700 hover:text-[#0E8A96] transition-colors cursor-pointer min-h-[1.5rem]">
                {value || <span className="text-gray-400 italic">Add a hypothesis for this campaign...</span>}
              </p>
            )}
          />
        )}
      </div>
    </div>
  );
}

