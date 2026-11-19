'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import TeamAPI from '@/lib/api/teamApi';
import { Team } from '@/types/team';

interface CreateTeamDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (team: Team) => void | Promise<void>;
}

function errorMessage(error: any): string {
  const data = error?.response?.data;
  if (data?.code === 'TEAM_NAME_TAKEN') {
    return 'A team with this name already exists. Names of deleted teams cannot be reused.';
  }
  return data?.error || 'Failed to create the team. Please try again.';
}

export default function CreateTeamDialog({ open, onClose, onCreated }: CreateTeamDialogProps) {
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setName('');
      setDesc('');
      setError(null);
    }
  }, [open]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      setSubmitting(true);
      setError(null);
      const team = await TeamAPI.createTeam(name.trim(), desc.trim());
      await onCreated(team);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !submitting && onClose()}>
      <DialogContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Create Team</DialogTitle>
            <DialogDescription>Add a new team to your organization.</DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <label htmlFor="create-team-name" className="text-sm font-medium text-gray-700">
              Team name
            </label>
            <Input
              id="create-team-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              autoFocus
              required
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="create-team-desc" className="text-sm font-medium text-gray-700">
              Description (optional)
            </label>
            <textarea
              id="create-team-desc"
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              rows={3}
              className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>

          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button
              type="submit"
              className="bg-indigo-600 text-white hover:bg-indigo-700"
              disabled={submitting || !name.trim()}
            >
              {submitting ? 'Creating...' : 'Create Team'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
