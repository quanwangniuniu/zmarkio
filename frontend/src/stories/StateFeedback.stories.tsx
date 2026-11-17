import type { Meta, StoryObj } from '@storybook/react';
import LoadingSkeleton from '@/components/state-feedback/LoadingSkeleton';
import ErrorState from '@/components/state-feedback/ErrorState';
import DisabledOverlay from '@/components/state-feedback/DisabledOverlay';
import FieldSkeleton from '@/components/state-feedback/FieldSkeleton';
import EmptyState from '@/components/state-feedback/EmptyState';
import { ListTodo } from 'lucide-react';

const meta: Meta<typeof LoadingSkeleton> = {
  title: 'State/Feedback',
  component: LoadingSkeleton,
  subcomponents: { 
    ErrorState: ErrorState as any, 
    DisabledOverlay: DisabledOverlay as any 
  },
  parameters: {
    layout: 'centered',
  },
  tags: ['autodocs'],
};

export default meta;

type Story = StoryObj<typeof LoadingSkeleton>;

export const LoadingSkeletonExample: Story = {
  name: 'Field Loading Skeleton',
  render: () => (
    <div className="w-[360px]">
      <FieldSkeleton />
    </div>
  ),
};

export const ErrorStateExample: Story = {
  render: () => (
    <div className="w-[360px]">
      <ErrorState
        title="You don't have permission"
        description="Contact an admin to request access."
      />
    </div>
  ),
};

export const ErrorStateWithAction: Story = {
  render: () => (
    <div className="w-[360px]">
      <ErrorState
        title="We couldn't load your workspace"
        description="Check your connection or try again in a few minutes."
        actionLabel="Retry"
        onAction={() => {}}
      />
    </div>
  ),
};

export const DisabledOverlayExample: Story = {
  render: () => (
    <div className="w-[360px]">
      <DisabledOverlay isDisabled message="Upgrade required">
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="text-sm font-semibold text-slate-800">Sharing</div>
          <p className="text-xs text-slate-500">Invite teammates and control access.</p>
        </div>
      </DisabledOverlay>
    </div>
  ),
};

export const SameContentDifferentStates: Story = {
  render: () => (
    <div className="flex w-[760px] flex-col gap-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="text-sm font-semibold text-slate-800">Workspace summary</div>
          <p className="text-xs text-slate-500">Last updated 2 hours ago.</p>
        </div>
        <FieldSkeleton />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <ErrorState
          title="We couldn't load your workspace"
          description="Check your connection or try again in a few minutes."
          actionLabel="Retry"
          onAction={() => {}}
        />
        <DisabledOverlay isDisabled message="Upgrade required">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="text-sm font-semibold text-slate-800">Workspace summary</div>
            <p className="text-xs text-slate-500">Last updated 2 hours ago.</p>
          </div>
        </DisabledOverlay>
      </div>
    </div>
  ),
};

export const EmptyStateExample: Story = {
  name: 'Empty State',
  render: () => (
    <EmptyState
      icon={<ListTodo />}
      title="No tasks yet"
      description="Create your first task to start tracking work."
      actionLabel="Add the first task"
      onAction={() => {}}
    />
  ),
};

export const EmptyStateWithoutAction: Story = {
  name: 'Empty State (no action)',
  render: () => (
    <EmptyState
      icon={<ListTodo />}
      title="No tasks yet"
      description="Ask a project admin to create the first task."
    />
  ),
};