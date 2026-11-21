'use client';

import { GitBranch ,Plus} from 'lucide-react';
import EmptyState from '@/components/state-feedback/EmptyState';


interface Props {
  onCreate: () => void;
  canCreate: boolean;
}

export default function DecisionsEmptyState({ onCreate, canCreate }: Props) {
  return (
    <EmptyState
      icon={<GitBranch className="h-6 w-6" />}
      title='No decisions yet'
      description="Create your first decision to map context, options, signals, and outcomes into a traceable tree."
      actionLabel={canCreate ? 'Create your first decision' : undefined}
      onAction={canCreate ? onCreate : undefined}
      actionIcon={canCreate ? <Plus className="h-3.5 w-3.5" /> : undefined}
    />

  );
}
