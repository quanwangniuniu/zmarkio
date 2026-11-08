'use client';

import ConfirmModal from '@/components/ui/ConfirmModal';

interface DeleteTeamDialogProps {
  open: boolean;
  teamName: string;
  loading: boolean;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
}

export default function DeleteTeamDialog({ open, teamName, loading, onClose, onConfirm }: DeleteTeamDialogProps) {
  return (
    <ConfirmModal
      isOpen={open}
      onClose={onClose}
      onConfirm={onConfirm}
      title="Delete team"
      message={`Delete "${teamName}"? The team will no longer appear for its members, and its name cannot be reused. A team that still has members or child teams cannot be deleted.`}
      confirmText="Delete Team"
      type="danger"
      loading={loading}
    />
  );
}
