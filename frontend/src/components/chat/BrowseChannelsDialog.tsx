'use client';

import { useEffect, useState } from 'react';
import { Hash, Loader2, Search, Users, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { browseChannels, addParticipant } from '@/lib/api/chatApi';
import type { BrowseChannelRow } from '@/lib/api/chatApi';

interface BrowseChannelsDialogProps {
  projectId: number | string;
  currentUserId: number;
  onClose: () => void;
  onJoinedChannel?: (chatSlug: string) => void;
}

export default function BrowseChannelsDialog({
  projectId,
  currentUserId,
  onClose,
  onJoinedChannel,
}: BrowseChannelsDialogProps) {
  const [channels, setChannels] = useState<BrowseChannelRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [joiningId, setJoiningId] = useState<number | null>(null);

  useEffect(() => {
    browseChannels(projectId)
      .then(setChannels)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [projectId]);

  const filtered = channels.filter((c) =>
    c.name.toLowerCase().includes(query.toLowerCase()) ||
    c.topic?.toLowerCase().includes(query.toLowerCase())
  );

  const handleJoin = async (channel: BrowseChannelRow) => {
    setJoiningId(channel.id);
    try {
      await addParticipant(channel.slug, currentUserId);
      setChannels((prev) =>
        prev.map((c) => c.id === channel.id ? { ...c, is_member: true, participant_count: c.participant_count + 1 } : c)
      );
      toast.success(`Joined #${channel.name}`);
      onJoinedChannel?.(channel.slug);
    } catch {
      toast.error('Failed to join channel');
    } finally {
      setJoiningId(null);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="flex h-[520px] w-full max-w-lg flex-col rounded-xl bg-white shadow-2xl">
        {/* Header */}
        <div className="flex shrink-0 items-center gap-3 border-b border-gray-100 px-5 py-4">
          <Hash className="h-5 w-5 text-gray-400" />
          <h2 className="flex-1 text-base font-semibold text-gray-800">Browse channels</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Search */}
        <div className="shrink-0 border-b border-gray-100 px-5 py-3">
          <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
            <Search className="h-4 w-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search channels…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="flex-1 bg-transparent text-sm outline-none"
              autoFocus
            />
          </div>
        </div>

        {/* Channel list */}
        <div className="task-tab-scrollbar flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin text-gray-300" />
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center text-gray-400">
              <Hash className="h-8 w-8 opacity-30" />
              <p className="text-sm">{query ? 'No channels match your search.' : 'No channels in this project yet.'}</p>
            </div>
          ) : (
            <ul className="divide-y divide-gray-50">
              {filtered.map((ch) => (
                <li key={ch.id} className="flex items-center gap-3 px-5 py-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-100">
                    <Hash className="h-4 w-4 text-gray-500" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <p className="truncate text-sm font-semibold text-gray-800">{ch.name}</p>
                      <span className="flex items-center gap-0.5 text-[11px] text-gray-400">
                        <Users className="h-3 w-3" />
                        {ch.participant_count}
                      </span>
                    </div>
                    {ch.topic && (
                      <p className="truncate text-xs text-gray-500">{ch.topic}</p>
                    )}
                  </div>
                  {ch.is_member ? (
                    <span className="shrink-0 rounded-full bg-teal-50 px-2.5 py-0.5 text-xs font-medium text-teal-700">
                      Joined
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void handleJoin(ch)}
                      disabled={joiningId === ch.id}
                      className="shrink-0 rounded-lg border border-gray-200 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                    >
                      {joiningId === ch.id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : 'Join'}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Footer */}
        <div className="shrink-0 border-t border-gray-100 px-5 py-3 text-center">
          <p className="text-xs text-gray-400">
            {filtered.length} channel{filtered.length !== 1 ? 's' : ''} in this project
          </p>
        </div>
      </div>
    </div>
  );
}
