'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  ArrowUpRight,
  Bell,
  Calendar as CalendarIcon,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  Download,
  FileText,
  Globe2,
  Image,
  Loader2,
  LogOut,
  Music,
  Pin,
  Plus,
  Search,
  ShieldCheck,
  Trash2,
  Users,
  Video,
  X,
} from 'lucide-react';
import { avatarColor } from './avatarColor';
import {
  addMonths,
  eachDayOfInterval,
  endOfMonth,
  endOfWeek,
  format,
  isSameDay,
  isSameMonth,
  parseISO,
  startOfDay,
  startOfMonth,
  startOfWeek,
  subMonths,
} from 'date-fns';
import toast from 'react-hot-toast';
import type { Chat, ChatParticipant, ChannelVisibility } from '@/types/chat';
import { useChatStore } from '@/lib/chatStore';
import { isChannelManager } from '@/lib/chatPermissions';
import { addParticipant, cancelScheduledMessage, createScheduledMessage, getChat, leaveChat, listPins, listChatFiles, listScheduledMessages, removeParticipant, unpinMessage, updateChatDetails, updateNotificationSettings, updateParticipantManager } from '@/lib/api/chatApi';
import { TEMP_MUTE_OPTIONS, formatMutedUntil, getTemporaryMuteUntil, isParticipantCurrentlyMuted } from '@/lib/chatMute';
import { MAX_CHANNEL_NAME_LENGTH, limitName, normalizeLimitedName } from '@/lib/messages/nameLimits';
import type { PinnedMessageRow, ChatFileRow, ScheduledMessageRow } from '@/lib/api/chatApi';
import { ProjectAPI } from '@/lib/api/projectApi';
import type { ProjectMemberData } from '@/lib/api/projectApi';

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Returns today's date as 'YYYY-MM-DD' in local time (not UTC). */
function localDateString(d = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// ── Avatar helper ─────────────────────────────────────────────────────────────

const AVATAR_COLORS = [
  'bg-blue-500', 'bg-emerald-500', 'bg-violet-500', 'bg-orange-500',
  'bg-pink-500', 'bg-teal-500', 'bg-red-500', 'bg-indigo-500',
];

const CHANNEL_VISIBILITY_OPTIONS: Array<{
  value: ChannelVisibility;
  label: string;
  description: string;
}> = [
  {
    value: 'public',
    label: 'Public browse and join',
    description: 'Project members can find this channel in Browse channels and join themselves.',
  },
  {
    value: 'member_invite',
    label: 'Hidden, members can add',
    description: 'Hidden from Browse channels. Any channel member can add project members.',
  },
  {
    value: 'manager_invite',
    label: 'Hidden, managers add only',
    description: 'Hidden from Browse channels. Only channel managers can add project members.',
  },
];

function getAvatarColor(userId: number) {
  return AVATAR_COLORS[userId % AVATAR_COLORS.length];
}

function Avatar({ user, size = 'md' }: {
  user: { id: number; username?: string; email?: string; avatar?: string | null };
  size?: 'sm' | 'md' | 'lg';
}) {
  const initials = (user.username || user.email || '?')[0].toUpperCase();
  const cls = size === 'sm' ? 'h-6 w-6 text-[10px]' : size === 'lg' ? 'h-10 w-10 text-base' : 'h-8 w-8 text-xs';
  if (user.avatar) {
    return <img src={user.avatar} alt={initials} className={`${cls} rounded-full object-cover shrink-0`} />;
  }
  return (
    <div className={`${cls} flex shrink-0 items-center justify-center rounded-full font-semibold text-white ${getAvatarColor(user.id)}`}>
      {initials}
    </div>
  );
}

// ── Inline-editable field ─────────────────────────────────────────────────────

interface EditableFieldProps {
  label: string;
  hint?: string;
  value: string;
  placeholder: string;
  multiline?: boolean;
  disabled?: boolean;
  maxLength?: number;
  onSave: (value: string) => Promise<void>;
}

function EditableField({ label, hint, value, placeholder, multiline = false, disabled = false, maxLength, onSave }: EditableFieldProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null);

  // Sync when parent updates
  useEffect(() => { setDraft(value); }, [value]);

  const startEdit = () => {
    setDraft(value);
    setEditing(true);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const handleSave = async () => {
    if (draft === value) { setEditing(false); return; }
    setSaving(true);
    try { await onSave(draft); } finally { setSaving(false); setEditing(false); }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { setEditing(false); setDraft(value); }
    if (e.key === 'Enter' && !multiline) { void handleSave(); }
  };

  return (
    <div className="group">
      <div className="mb-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">{label}</span>
      </div>
      {editing ? (
        <div className="space-y-1.5">
          {multiline ? (
            <textarea
              ref={inputRef as React.RefObject<HTMLTextAreaElement>}
              value={draft}
              onChange={(e) => setDraft(maxLength ? limitName(e.target.value, maxLength) : e.target.value)}
              onKeyDown={handleKeyDown}
              maxLength={maxLength}
              rows={3}
              className="w-full resize-none rounded border border-gray-300 px-2 py-1.5 text-sm text-gray-800 focus:border-teal-400 focus:outline-none focus:ring-1 focus:ring-teal-400"
            />
          ) : (
            <input
              ref={inputRef as React.RefObject<HTMLInputElement>}
              type="text"
              value={draft}
              onChange={(e) => setDraft(maxLength ? limitName(e.target.value, maxLength) : e.target.value)}
              onKeyDown={handleKeyDown}
              maxLength={maxLength}
              className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm text-gray-800 focus:border-teal-400 focus:outline-none focus:ring-1 focus:ring-teal-400"
            />
          )}
          {maxLength && (
            <p className="text-[10px] text-gray-400">{draft.length}/{maxLength}</p>
          )}
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving}
              className="rounded bg-[#3CCED7] px-2.5 py-1 text-xs font-medium text-white hover:bg-[#33b8c0] disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              onClick={() => { setEditing(false); setDraft(value); }}
              className="rounded px-2.5 py-1 text-xs font-medium text-gray-500 hover:bg-gray-100"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button type="button" onClick={startEdit} disabled={disabled} className="w-full text-left disabled:cursor-default">
          {value ? (
            <p className="text-sm text-gray-700 [overflow-wrap:anywhere]">{value}</p>
          ) : (
            <>
              {hint
                ? <p className="text-[11px] text-gray-400 leading-snug">{hint}</p>
                : <p className="text-xs text-gray-400 italic">{placeholder}</p>}
            </>
          )}
        </button>
      )}
    </div>
  );
}

// ── Add member popover ────────────────────────────────────────────────────────

interface AddMemberPickerProps {
  chatId: number;
  chatSlug: string;
  projectId: number;
  existingUserIds: Set<number>;
  currentUserId: number;
  onAdded: (participant: ChatParticipant) => void;
  onClose: () => void;
}

function AddMemberPicker({ chatId, chatSlug, projectId, existingUserIds, onAdded, onClose }: AddMemberPickerProps) {
  const [query, setQuery] = useState('');
  const [members, setMembers] = useState<ProjectMemberData[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    ProjectAPI.getProjectMembers(projectId)
      .then((all) => setMembers(all.filter((m) => m.is_active && !existingUserIds.has(m.user.id))))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [projectId, existingUserIds]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  const filtered = members.filter((m) => {
    const q = query.toLowerCase();
    return (m.user.username || '').toLowerCase().includes(q) || (m.user.email || '').toLowerCase().includes(q);
  });

  const handleAdd = async (member: ProjectMemberData) => {
    setAdding(member.user.id);
    try {
      const participant = await addParticipant(chatSlug, member.user.id);
      // Backend returns a ChatParticipant; normalise chat_id if needed
      onAdded({ ...participant, chat_id: chatId });
      onClose();
    } catch {
      /* silently ignore */
    } finally {
      setAdding(null);
    }
  };

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full z-50 mt-1 w-64 rounded-lg border border-gray-200 bg-white shadow-lg"
    >
      <div className="p-2">
        <div className="flex items-center gap-1.5 rounded border border-gray-300 px-2 py-1.5">
          <Search className="h-3.5 w-3.5 text-gray-400" />
          <input
            type="text"
            placeholder="Search members…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="flex-1 text-sm outline-none"
            autoFocus
          />
        </div>
      </div>
      <div className="task-tab-scrollbar max-h-48 overflow-y-auto">
        {loading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="h-4 w-4 animate-spin text-gray-400" />
          </div>
        ) : filtered.length === 0 ? (
          <p className="px-3 py-3 text-center text-xs text-gray-400">No members to add</p>
        ) : (
          filtered.map((m) => (
            <button
              key={m.user.id}
              type="button"
              onClick={() => void handleAdd(m)}
              disabled={adding === m.user.id}
              className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-gray-50 disabled:opacity-50"
            >
              <Avatar user={m.user} size="sm" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-gray-800">
                  {m.user.username || m.user.email}
                </p>
              </div>
              {adding === m.user.id && <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-400" />}
            </button>
          ))
        )}
      </div>
    </div>
  );
}

// ── Upward-opening date picker ────────────────────────────────────────────────
// Native <input type="date"> always opens its calendar downward, which gets
// clipped inside the drawer. This custom popover opens upward instead.

interface DatePickerUpProps {
  value: string;            // 'YYYY-MM-DD'
  min?: string;             // 'YYYY-MM-DD' — days before this are disabled
  onChange: (value: string) => void;
  className?: string;
}

function DatePickerUp({ value, min, onChange, className }: DatePickerUpProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const selected = value ? parseISO(value) : null;
  const minDate = min ? startOfDay(parseISO(min)) : null;
  const [viewMonth, setViewMonth] = useState<Date>(selected ?? new Date());
  // Fixed-position coords for the portal popover (anchored above the trigger)
  const [coords, setCoords] = useState<{ left: number; bottom: number; width: number } | null>(null);

  const updateCoords = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setCoords({
      left: rect.left,
      bottom: window.innerHeight - rect.top + 4, // sit just above the trigger
      width: rect.width,
    });
  }, []);

  // Re-centre the calendar on the selected month + measure position when opening
  useLayoutEffect(() => {
    if (!open) return;
    setViewMonth(selected ?? new Date());
    updateCoords();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Reposition while open (scroll/resize), and close on outside click / Escape
  useEffect(() => {
    if (!open) return;
    const onScrollOrResize = () => updateCoords();
    const onMouseDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (popoverRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('scroll', onScrollOrResize, true);
    window.addEventListener('resize', onScrollOrResize);
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true);
      window.removeEventListener('resize', onScrollOrResize);
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, updateCoords]);

  const days = eachDayOfInterval({
    start: startOfWeek(startOfMonth(viewMonth), { weekStartsOn: 1 }),
    end: endOfWeek(endOfMonth(viewMonth), { weekStartsOn: 1 }),
  });

  const pick = (day: Date) => {
    onChange(format(day, 'yyyy-MM-dd'));
    setOpen(false);
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`flex flex-1 items-center justify-between gap-1.5 ${className ?? ''}`}
      >
        <span className={selected ? 'text-gray-700' : 'text-gray-400'}>
          {selected ? format(selected, 'MM/dd/yyyy') : 'Select date'}
        </span>
        <CalendarIcon className="h-3.5 w-3.5 shrink-0 text-gray-400" />
      </button>

      {open && coords && typeof document !== 'undefined' && createPortal(
        <div
          ref={popoverRef}
          className="fixed z-[60] w-60 rounded-lg border border-gray-200 bg-white p-2 shadow-lg"
          style={{ left: coords.left, bottom: coords.bottom }}
        >
          {/* Month header */}
          <div className="mb-1.5 flex items-center justify-between px-1">
            <button
              type="button"
              onClick={() => setViewMonth((m) => subMonths(m, 1))}
              className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
              aria-label="Previous month"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            <span className="text-xs font-semibold text-gray-700">
              {format(viewMonth, 'MMMM yyyy')}
            </span>
            <button
              type="button"
              onClick={() => setViewMonth((m) => addMonths(m, 1))}
              className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
              aria-label="Next month"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>

          {/* Weekday labels */}
          <div className="mb-1 grid grid-cols-7 gap-0.5 text-center text-[10px] font-medium text-gray-400">
            {['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map((d) => (
              <span key={d}>{d}</span>
            ))}
          </div>

          {/* Day grid */}
          <div className="grid grid-cols-7 gap-0.5">
            {days.map((day) => {
              const disabled = minDate ? startOfDay(day) < minDate : false;
              const isSelected = selected ? isSameDay(day, selected) : false;
              const inMonth = isSameMonth(day, viewMonth);
              return (
                <button
                  key={day.toISOString()}
                  type="button"
                  disabled={disabled}
                  onClick={() => pick(day)}
                  className={[
                    'flex h-7 w-7 items-center justify-center rounded text-xs',
                    isSelected
                      ? 'bg-teal-500 font-semibold text-white'
                      : inMonth
                        ? 'text-gray-700 hover:bg-teal-50'
                        : 'text-gray-300 hover:bg-gray-50',
                    disabled ? 'cursor-not-allowed text-gray-200 hover:bg-transparent' : '',
                  ].join(' ')}
                >
                  {format(day, 'd')}
                </button>
              );
            })}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}

// ── Collapsible section ───────────────────────────────────────────────────────

function Section({ title, icon, defaultOpen = true, onOpen, contentClassName, testId, children }: {
  title: string;
  icon: React.ReactNode;
  defaultOpen?: boolean;
  onOpen?: () => void;
  contentClassName?: string;
  testId?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const headerRef = useRef<HTMLButtonElement>(null);
  return (
    <div className="border-b border-gray-100">
      <button
        ref={headerRef}
        data-testid={testId}
        type="button"
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next) {
            onOpen?.();
            // After the content renders, scroll the header to the top of the panel
            setTimeout(() => {
              headerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 50);
          }
        }}
        className="flex w-full items-center gap-2 px-4 py-3 text-left hover:bg-gray-50"
      >
        <span className="text-gray-500">{icon}</span>
        <span className="flex-1 text-sm font-semibold text-gray-700">{title}</span>
        {open
          ? <ChevronDown className="h-3.5 w-3.5 text-gray-400" />
          : <ChevronRight className="h-3.5 w-3.5 text-gray-400" />}
      </button>
      {open && (
        <div className={contentClassName ?? 'task-tab-scrollbar max-h-64 overflow-y-auto px-4 pb-4'}>
          {children}
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export interface ChannelDetailsDrawerProps {
  chat: Chat;
  currentUserId: number;
  onClose: () => void;
  onChatUpdated: (updated: Chat) => void;
  /**
   * Called when user clicks a pinned message so the timeline can jump to it.
   * `parentMessageId` is set when the pinned message is a thread reply — the
   * caller should open the thread for the parent and highlight the reply.
   */
  onJumpToMessage?: (messageId: number, parentMessageId?: number | null) => void;
  /** A newly-created scheduled message to push into the drawer list immediately. */
  lastScheduledMsg?: ScheduledMessageRow | null;
  /** Incremented when timeline pin state changes so the drawer can refresh immediately. */
  pinRefreshKey?: number;
  /** Called when a pin is removed inside the drawer so the timeline can update too. */
  onPinRemoved?: (messageId: number) => void;
  /** Called when a new participant is successfully added, so callers can update their mention list. */
  onParticipantAdded?: (p: ChatParticipant) => void;
  /** Called after the current user leaves the channel, so the caller can drop it from the list and navigate away. */
  onLeft?: (chatId: number) => void;
}

export default function ChannelDetailsDrawer({
  chat,
  currentUserId,
  onClose,
  onChatUpdated,
  onJumpToMessage,
  lastScheduledMsg,
  pinRefreshKey = 0,
  onPinRemoved,
  onParticipantAdded,
  onLeft,
}: ChannelDetailsDrawerProps) {
  const isGroup = chat.type === 'group';
  const [participants, setParticipants] = useState<ChatParticipant[]>(
    (chat.participants ?? []).filter((p: ChatParticipant) => p.user && p.is_active !== false)
  );
  const [showAddPicker, setShowAddPicker] = useState(false);
  const [removingId, setRemovingId] = useState<number | null>(null);
  const [pins, setPins] = useState<PinnedMessageRow[]>([]);
  const [pinsLoaded, setPinsLoaded] = useState(false);
  const [pinsLoading, setPinsLoading] = useState(false);
  const [pinsError, setPinsError] = useState<string | null>(null);
  const [unpinningId, setUnpinningId] = useState<number | null>(null);
  const [files, setFiles] = useState<ChatFileRow[]>([]);
  const [filesTotal, setFilesTotal] = useState(0);
  const [filesLoaded, setFilesLoaded] = useState(false);
  const [filesLoading, setFilesLoading] = useState(false);
  const [scheduled, setScheduled] = useState<ScheduledMessageRow[]>([]);
  const [scheduledLoaded, setScheduledLoaded] = useState(false);
  const [cancellingScheduledId, setCancellingScheduledId] = useState<number | null>(null);
  const [reschedulingId, setReschedulingId] = useState<number | null>(null);
  const [managerSavingId, setManagerSavingId] = useState<number | null>(null);
  const [metadataChat, setMetadataChat] = useState<Chat | null>(null);
  const [savingVisibility, setSavingVisibility] = useState(false);
  const rescheduleFormRef = useRef<HTMLDivElement>(null);
  const drawerBodyRef = useRef<HTMLDivElement>(null);
  const notificationsSectionRef = useRef<HTMLDivElement>(null);
  const leaveSectionRef = useRef<HTMLDivElement>(null);
  const scrollNotificationsAfterUnmuteRef = useRef(false);
  const [rescheduleDate, setRescheduleDate] = useState('');
  const [rescheduleTime, setRescheduleTime] = useState('');
  const [rescheduleSaving, setRescheduleSaving] = useState(false);

  // Notification / mute state — derived live from participants so changes are reflected immediately
  const myParticipant = participants.find((p) => p.user.id === currentUserId);
  const isMuted = isParticipantCurrentlyMuted(myParticipant);
  const mutedUntilLabel = isMuted && myParticipant?.muted_until
    ? formatMutedUntil(myParticipant.muted_until)
    : null;
  const notifLevel: 'all' | 'mentions' = myParticipant?.notification_level === 'mentions' ? 'mentions' : 'all';
  const [savingNotif, setSavingNotif] = useState(false);

  // Leave-channel confirmation + in-flight state
  const [confirmingLeave, setConfirmingLeave] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const leaveConfirmRef = useRef<HTMLDivElement>(null);

  const activeParticipants = useCallback((items?: ChatParticipant[]) =>
    (items ?? []).filter((p: ChatParticipant) => p.user && p.is_active !== false),
  []);

  const syncChatDetails = useCallback((details: Chat) => {
    const syncedParticipants = activeParticipants(details.participants);
    const syncedChat = { ...details, participants: syncedParticipants };
    setMetadataChat(syncedChat);
    setParticipants(syncedParticipants);
    useChatStore.getState().updateChat(chat.id, syncedChat);
    onChatUpdated(syncedChat);
    return syncedChat;
  }, [activeParticipants, chat.id, onChatUpdated]);

  // Keep participants in sync when chat prop changes, unless fresh drawer
  // metadata has already replaced the prop-level snapshot.
  useEffect(() => {
    if (metadataChat?.id === chat.id) return;
    setParticipants(activeParticipants(chat.participants));
  }, [activeParticipants, chat.id, chat.participants, metadataChat?.id]);

  // When a new scheduled message is created via the composer, append it to the
  // local list immediately — no need to refetch.
  useEffect(() => {
    if (!lastScheduledMsg) return;
    setScheduled((prev) => {
      if (prev.some((m) => m.id === lastScheduledMsg.id)) return prev;
      return [...prev, lastScheduledMsg].sort(
        (a, b) => new Date(a.scheduled_at).getTime() - new Date(b.scheduled_at).getTime()
      );
    });
    // Mark as loaded so the section won't overwrite the list on first open
    setScheduledLoaded(true);
  }, [lastScheduledMsg]);

  const existingUserIds = new Set(participants.map((p) => p.user.id));

  const projectId: number = (() => {
    const raw = (chat as any).project_id ?? (chat as any).project;
    return Number(raw) || 0;
  })();
  const metadataSource = metadataChat ?? chat;
  const createdDateLabel = metadataSource.created_at ? format(parseISO(metadataSource.created_at), 'MMM d, yyyy') : null;
  const creatorParticipants = (metadataSource.participants ?? participants).filter((p) => p.user);
  const inferredCreator = [...creatorParticipants].sort((a, b) => {
    const aTime = a.joined_at ? new Date(a.joined_at).getTime() : Number.MAX_SAFE_INTEGER;
    const bTime = b.joined_at ? new Date(b.joined_at).getTime() : Number.MAX_SAFE_INTEGER;
    return aTime - bTime;
  })[0]?.user ?? null;
  const creator = metadataSource.created_by
    ?? (metadataSource.created_by_id
      ? participants.find((p) => p.user.id === Number(metadataSource.created_by_id))?.user
      : null)
    ?? inferredCreator;
  const creatorLabel = creator?.username || creator?.email || null;
  const createdMetaLabel = creatorLabel && createdDateLabel
    ? `Created by ${creatorLabel} on ${createdDateLabel}`
    : createdDateLabel
      ? `Created on ${createdDateLabel}`
      : null;
  const managerCount = participants.filter((p) => p.is_manager).length;
  const assignedManagerCount = participants.filter((p) => p.is_manager && p.user.id !== metadataSource.created_by_id).length;
  const permissionChat = { ...metadataSource, participants };
  const isEffectiveManager = (participant: ChatParticipant) => isChannelManager(
    permissionChat,
    participant.user.id,
  );
  const managerParticipants = participants.filter(isEffectiveManager);
  const nonManagerParticipants = participants.filter((p) => !isEffectiveManager(p));
  const channelVisibility: ChannelVisibility = metadataSource.visibility ?? 'public';
  const canManageChannel = isChannelManager(permissionChat, currentUserId);
  const canAddMembers = isGroup && (channelVisibility !== 'manager_invite' || canManageChannel);

  useEffect(() => {
    if (!canAddMembers) setShowAddPicker(false);
  }, [canAddMembers]);

  useEffect(() => {
    let cancelled = false;
    setMetadataChat(null);
    getChat(chat.slug)
      .then((details) => {
        if (cancelled) return;
        syncChatDetails(details);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [chat.id, chat.slug, syncChatDetails]);

  const handleSaveName = useCallback(async (value: string) => {
    const updated = await updateChatDetails(chat.id, { name: normalizeLimitedName(value, MAX_CHANNEL_NAME_LENGTH) });
    onChatUpdated(updated);
  }, [chat.id, onChatUpdated]);

  const handleSaveTopic = useCallback(async (value: string) => {
    const updated = await updateChatDetails(chat.id, { topic: value });
    onChatUpdated(updated);
  }, [chat.id, onChatUpdated]);

  const handleSaveDescription = useCallback(async (value: string) => {
    const updated = await updateChatDetails(chat.id, { description: value });
    onChatUpdated(updated);
  }, [chat.id, onChatUpdated]);

  const handleVisibilityChange = async (visibility: ChannelVisibility) => {
    if (visibility === channelVisibility || savingVisibility) return;

    const previousMetadata = metadataChat;
    const previousVisibility = channelVisibility;
    setSavingVisibility(true);
    setMetadataChat((prev) => prev ? { ...prev, visibility } : { ...chat, visibility });
    useChatStore.getState().updateChat(chat.id, { visibility });

    try {
      const updated = await updateChatDetails(chat.id, { visibility });
      setMetadataChat(updated);
      onChatUpdated(updated);
      toast.success('Channel access updated');
    } catch (error: any) {
      setMetadataChat(previousMetadata);
      useChatStore.getState().updateChat(chat.id, { visibility: previousVisibility });
      toast.error(error?.response?.data?.error ?? 'Could not update channel access');
    } finally {
      setSavingVisibility(false);
    }
  };

  const handleParticipantAdded = useCallback((p: ChatParticipant) => {
    const nextParticipants = participants.some((participant) => participant.user.id === p.user.id)
      ? participants
      : [...participants, p];
    setParticipants(nextParticipants);
    onChatUpdated({ ...chat, participants: nextParticipants });
    onParticipantAdded?.(p);
  }, [chat, onChatUpdated, onParticipantAdded, participants]);

  const handleRemove = async (participant: ChatParticipant) => {
    setRemovingId(participant.user.id);
    try {
      await removeParticipant(chat.slug, participant.user.id);
      const nextParticipants = participants.filter((p) => p.user.id !== participant.user.id);
      setParticipants(nextParticipants);
      onChatUpdated({ ...chat, participants: nextParticipants });
    } catch (error: any) {
      try {
        syncChatDetails(await getChat(chat.id));
      } catch {
        // Keep the visible state unchanged if the refresh also fails.
      }
      toast.error(error?.response?.data?.error ?? 'Could not remove member');
    } finally {
      setRemovingId(null);
    }
  };

  const handleManagerToggle = async (participant: ChatParticipant) => {
    const nextIsManager = !participant.is_manager;
    const previousParticipants = participants;
    setManagerSavingId(participant.user.id);
    const nextParticipants = participants.map((p) =>
      p.user.id === participant.user.id ? { ...p, is_manager: nextIsManager } : p
    );
    setParticipants(nextParticipants);
    useChatStore.getState().updateChat(chat.id, { participants: nextParticipants });

    try {
      const updated = await updateParticipantManager(chat.id, participant.user.id, nextIsManager);
      const fallbackParticipants = nextParticipants.map((p) =>
        p.user.id === participant.user.id ? { ...p, ...updated } : p
      );
      let syncedChat: Chat | null = null;
      try {
        syncedChat = await getChat(chat.id);
      } catch {
        // Keep the optimistic single-participant update if the follow-up refresh fails.
      }
      const syncedParticipants = activeParticipants(syncedChat?.participants ?? fallbackParticipants);
      setParticipants(syncedParticipants);
      setMetadataChat((prev) =>
        syncedChat
          ? { ...syncedChat, participants: syncedParticipants }
          : prev ? { ...prev, participants: syncedParticipants } : prev
      );
      useChatStore.getState().updateChat(
        chat.id,
        syncedChat ? { ...syncedChat, participants: syncedParticipants } : { participants: syncedParticipants }
      );
      onChatUpdated(syncedChat ? { ...syncedChat, participants: syncedParticipants } : { ...chat, participants: syncedParticipants });
      toast.success(nextIsManager ? 'Manager added' : 'Manager removed');
    } catch (error: any) {
      try {
        syncChatDetails(await getChat(chat.id));
      } catch {
        setParticipants(previousParticipants);
        useChatStore.getState().updateChat(chat.id, { participants: previousParticipants });
      }
      const status = error?.response?.status;
      toast.error(
        error?.response?.data?.error
        ?? (status === 404 ? 'Manager controls are not available yet. Please refresh after the backend restarts.' : 'Could not update manager')
      );
    } finally {
      setManagerSavingId(null);
    }
  };

  const updateMyParticipant = (patch: Partial<ChatParticipant>) => {
    const next = participants.map((p) => p.user.id === currentUserId ? { ...p, ...patch } : p);
    setParticipants(next);
    useChatStore.getState().updateChat(chat.id, { participants: next });
  };

  const scrollDrawerTargetIntoView = useCallback((target: HTMLElement | null, padding = 16) => {
    const body = drawerBodyRef.current;
    if (!target || !body) return;

    const bodyRect = body.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const bottomOverflow = targetRect.bottom + padding - bodyRect.bottom;
    const topOverflow = targetRect.top - padding - bodyRect.top;

    if (bottomOverflow > 0) {
      body.scrollBy({ top: bottomOverflow, behavior: 'smooth' });
    } else if (topOverflow < 0) {
      body.scrollBy({ top: topOverflow, behavior: 'smooth' });
    }
  }, []);

  const handleMuteToggle = async () => {
    const next = !isMuted;
    const prevMuted = myParticipant?.is_muted ?? false;
    const prevMutedUntil = myParticipant?.muted_until ?? null;
    if (!next) {
      scrollNotificationsAfterUnmuteRef.current = true;
    }
    updateMyParticipant({ is_muted: next, muted_until: null });
    setSavingNotif(true);
    try {
      await updateNotificationSettings(chat.id, { is_muted: next, muted_until: null });
    } catch {
      updateMyParticipant({ is_muted: prevMuted, muted_until: prevMutedUntil });
    } finally {
      setSavingNotif(false);
    }
  };

  useEffect(() => {
    if (isMuted || !scrollNotificationsAfterUnmuteRef.current) return;
    scrollNotificationsAfterUnmuteRef.current = false;
    window.setTimeout(() => {
      scrollDrawerTargetIntoView(leaveSectionRef.current ?? notificationsSectionRef.current, 18);
    }, 80);
  }, [isMuted, scrollDrawerTargetIntoView]);

  const handleTemporaryMute = async (preset: '1h' | 'tomorrow' | '1w') => {
    const prevMuted = myParticipant?.is_muted ?? false;
    const prevMutedUntil = myParticipant?.muted_until ?? null;
    const mutedUntil = getTemporaryMuteUntil(preset).toISOString();

    updateMyParticipant({ is_muted: true, muted_until: mutedUntil });
    setSavingNotif(true);
    try {
      await updateNotificationSettings(chat.id, { is_muted: true, muted_until: mutedUntil });
      toast.success(`Muted until ${formatMutedUntil(mutedUntil)}`);
    } catch {
      updateMyParticipant({ is_muted: prevMuted, muted_until: prevMutedUntil });
      toast.error('Could not update notification settings');
    } finally {
      setSavingNotif(false);
    }
  };

  const handleNotifLevel = async (level: 'all' | 'mentions') => {
    const prev = notifLevel;
    updateMyParticipant({ notification_level: level });
    setSavingNotif(true);
    try {
      await updateNotificationSettings(chat.id, { notification_level: level });
    } catch {
      updateMyParticipant({ notification_level: prev });
    } finally {
      setSavingNotif(false);
    }
  };

  const handleLeave = async () => {
    setLeaving(true);
    try {
      await leaveChat(chat.id);
      toast.success(`You left ${chat.name ? `#${chat.name}` : 'the channel'}`);
      onLeft?.(chat.id);
      onClose();
    } catch {
      toast.error('Could not leave the channel');
      setLeaving(false);
      setConfirmingLeave(false);
    }
  };

  // pinRefreshKey bumps on every pin_update the channel receives, so several of
  // these can be in flight at once and they need not resolve in order. Only the
  // newest may write, or a late reply reinstates a list the server has already
  // moved past. Switching channels re-enters this too, which is what stops one
  // channel's pins rendering inside another.
  const pinRequestGenerationRef = useRef(0);
  const chatScopedRequestGenerationRef = useRef(0);

  const fetchPins = useCallback(async () => {
    const generation = ++pinRequestGenerationRef.current;
    setPinsLoaded(true);
    setPinsLoading(true);
    setPinsError(null);
    try {
      const rows = await listPins(chat.slug);
      if (generation !== pinRequestGenerationRef.current) return;
      setPins(
        [...rows].sort((a, b) => {
          const timeDifference = new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
          return timeDifference || b.id - a.id;
        }),
      );
    } catch {
      if (generation !== pinRequestGenerationRef.current) return;
      setPinsError('Could not load pinned messages.');
    } finally {
      if (generation === pinRequestGenerationRef.current) setPinsLoading(false);
    }
  }, [chat.slug]);

  // Keep pins fresh while the drawer is open; the timeline bumps pinRefreshKey
  // after pin/unpin actions so this section updates immediately.
  useEffect(() => {
    setPins([]);
    void fetchPins();
  }, [chat.id, fetchPins, pinRefreshKey]);

  // Fetch counts on mount so each section header shows its count even before
  // it's expanded. The onOpen loaders below become no-ops once *Loaded is true.
  // Both responses belong to one channel, and switching channels does not
  // cancel the request the previous one started. Without the check, its reply
  // lands afterwards and fills this drawer with the other channel's contents.
  useEffect(() => {
    // Comparing against a captured chat.id would not work: the closure holds
    // the value from its own render, so it always matches itself. The counter
    // lives outside the render and is what actually advances on a switch.
    const generation = ++chatScopedRequestGenerationRef.current;
    const isStale = () => generation !== chatScopedRequestGenerationRef.current;

    setFilesLoaded(true);
    setFilesLoading(true);
    listChatFiles(chat.id)
      .then(({ results, total }) => {
        if (isStale()) return;
        setFiles(results);
        setFilesTotal(total);
      })
      .catch(() => {})
      .finally(() => { if (!isStale()) setFilesLoading(false); });

    setScheduledLoaded(true);
    listScheduledMessages(chat.id)
      .then((rows) => { if (!isStale()) setScheduled(rows); })
      .catch(() => {});
  }, [chat.id]);

  const loadPins = useCallback(() => {
    if (pinsLoaded) return;
    void fetchPins();
  }, [fetchPins, pinsLoaded]);

  const loadFiles = useCallback(() => {
    if (filesLoaded) return;
    setFilesLoaded(true);
    setFilesLoading(true);
    listChatFiles(chat.id)
      .then(({ results, total }) => { setFiles(results); setFilesTotal(total); })
      .catch(() => {})
      .finally(() => setFilesLoading(false));
  }, [chat.id, filesLoaded]);

  const loadScheduled = useCallback(() => {
    if (scheduledLoaded) return;
    setScheduledLoaded(true);
    listScheduledMessages(chat.id).then(setScheduled).catch(() => {});
  }, [chat.id, scheduledLoaded]);

  const handleUnpin = async (pin: PinnedMessageRow) => {
    setUnpinningId(pin.id);
    try {
      await unpinMessage(chat.slug, pin.message.id);
      setPins((prev) => prev.filter((p) => p.id !== pin.id));
      onPinRemoved?.(pin.message.id);
      toast.success('Message unpinned');
    } catch {
      toast.error('Failed to unpin message');
    } finally {
      setUnpinningId(null);
    }
  };

  const handleCancelScheduled = async (msg: ScheduledMessageRow) => {
    setCancellingScheduledId(msg.id);
    try {
      await cancelScheduledMessage(msg.id);
      setScheduled((prev) => prev.filter((m) => m.id !== msg.id));
    } catch {
      /* silently ignore */
    } finally {
      setCancellingScheduledId(null);
    }
  };

  const openReschedule = (msg: ScheduledMessageRow) => {
    // Pre-fill with the existing scheduled time, but clamp to at least now+15min
    // so the form never opens with a time already in the past.
    const existing = new Date(msg.scheduled_at);
    const floor = new Date(Date.now() + 15 * 60 * 1000);
    const target = existing > floor ? existing : floor;
    const date = localDateString(target);
    const time = `${String(target.getHours()).padStart(2, '0')}:${String(target.getMinutes()).padStart(2, '0')}`;
    setRescheduleDate(date);
    setRescheduleTime(time);
    setReschedulingId(msg.id);
  };

  const confirmReschedule = async (msg: ScheduledMessageRow) => {
    if (!rescheduleDate || !rescheduleTime) return;
    const scheduledAt = new Date(`${rescheduleDate}T${rescheduleTime}:00`);
    if (scheduledAt <= new Date()) {
      toast.error('Scheduled time must be in the future');
      return;
    }
    setRescheduleSaving(true);
    try {
      // Cancel old, create new with same content + new time
      await cancelScheduledMessage(msg.id);
      const newMsg = await createScheduledMessage({
        chat_id: msg.chat,
        content: msg.content,
        rich_body: msg.rich_body ?? undefined,
        attachment_ids: msg.attachment_ids,
        mention_ids: msg.mention_ids,
        reply_to_id: msg.reply_to ?? null,
        scheduled_at: scheduledAt.toISOString(),
      });
      setScheduled((prev) => prev.filter((m) => m.id !== msg.id).concat(newMsg));
      setReschedulingId(null);
    } catch {
      /* silently ignore */
    } finally {
      setRescheduleSaving(false);
    }
  };

  // Scroll the outer drawer body so the reschedule form is visible
  useEffect(() => {
    if (!reschedulingId) return;
    setTimeout(() => {
      const form = rescheduleFormRef.current;
      const body = drawerBodyRef.current;
      if (!form || !body) return;
      const formBottom = form.offsetTop + form.offsetHeight;
      const bodyVisible = body.scrollTop + body.clientHeight;
      if (formBottom > bodyVisible) {
        body.scrollTo({ top: formBottom - body.clientHeight + 16, behavior: 'smooth' });
      }
    }, 50);
  }, [reschedulingId]);

  // Scroll the outer drawer body so the leave-confirmation box is visible
  useEffect(() => {
    if (!confirmingLeave) return;
    setTimeout(() => {
      const box = leaveConfirmRef.current;
      const body = drawerBodyRef.current;
      if (!box || !body) return;
      const boxBottom = box.offsetTop + box.offsetHeight;
      const bodyVisible = body.scrollTop + body.clientHeight;
      if (boxBottom > bodyVisible) {
        body.scrollTo({ top: boxBottom - body.clientHeight + 16, behavior: 'smooth' });
      }
    }, 50);
  }, [confirmingLeave]);

  const pendingScheduled = scheduled.filter((m) => m.status === 'pending' || m.status === 'sending');

  return (
    <div className="flex h-full w-full flex-col border-l border-gray-200 bg-white" data-testid="channel-details-drawer">
      {/* Header */}
      <div className="relative z-10 flex shrink-0 items-center justify-between border-b border-gray-100 px-4 py-3 shadow-[0_1px_3px_rgba(16,24,40,0.05)]">
        <span className="text-sm font-semibold text-gray-800">{isGroup ? 'Channel details' : 'Direct message'}</span>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1.5 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
          aria-label="Close channel details"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Scrollable body */}
      <div ref={drawerBodyRef} className="task-tab-scrollbar flex-1 overflow-y-auto">

        {/* About — group channels only */}
        {isGroup && (
          <div className="border-b border-gray-100 px-4 py-4 space-y-4">
            <EditableField
              label="Channel name"
              value={limitName(metadataSource.name ?? '', MAX_CHANNEL_NAME_LENGTH)}
              placeholder="Add a name…"
              disabled={!canManageChannel}
              maxLength={MAX_CHANNEL_NAME_LENGTH}
              onSave={handleSaveName}
            />
            <EditableField
              label="Topic"
              hint={'What is this channel focused on right now? Update it as things change — e.g. "Q2 launch · deadline Jun 15".'}
              value={chat.topic ?? ''}
              placeholder="e.g. Q2 campaign launch · deadline Jun 15"
              disabled={!canManageChannel}
              onSave={handleSaveTopic}
            />
            <EditableField
              label="Description"
              hint="A permanent note on what this channel is for and who should join. New members read this once."
              value={chat.description ?? ''}
              placeholder="e.g. All paid social work for APAC. Tag @media-buyer for urgent requests."
              multiline
              disabled={!canManageChannel}
              onSave={handleSaveDescription}
            />
          </div>
        )}

        {/* Members (group) / Profile card (DM) */}
        {isGroup ? (
          <Section
            title={`Members · ${participants.length}`}
            icon={<Users className="h-4 w-4" />}
            defaultOpen={!canManageChannel}
          >
            <div className="relative mb-3">
              {canAddMembers ? (
                <>
                  <button
                    type="button"
                    onClick={() => setShowAddPicker((v) => !v)}
                    className="flex items-center gap-1.5 rounded-md border border-dashed border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-500 hover:border-teal-400 hover:text-teal-600"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    Add member
                  </button>
                  {showAddPicker && (
                    <AddMemberPicker
                      chatId={chat.id}
                      chatSlug={chat.slug}
                      projectId={projectId}
                      existingUserIds={existingUserIds}
                      currentUserId={currentUserId}
                      onAdded={handleParticipantAdded}
                      onClose={() => setShowAddPicker(false)}
                    />
                  )}
                </>
              ) : (
                <p className="rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-500">
                  Only managers can add members to this channel.
                </p>
              )}
            </div>

            <ul className="space-y-1">
              {participants.map((p: ChatParticipant) => {
                const isCreator = Boolean(metadataSource.created_by_id && p.user.id === metadataSource.created_by_id);
                const isManager = isEffectiveManager(p);
                const canRemoveMember = canManageChannel && p.user.id !== currentUserId && !isCreator;

                return (
                  <li key={p.id} className="group flex items-center gap-2.5 rounded-md px-1 py-1.5 hover:bg-gray-50">
                    <Avatar user={p.user} />
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <p className="truncate text-sm font-medium text-gray-800">
                          {p.user.username || p.user.email}
                        </p>
                        {isManager && (
                          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-teal-50 px-1.5 py-0.5 text-[10px] font-medium text-teal-700">
                            <ShieldCheck className="h-3 w-3" />
                            Manager
                          </span>
                        )}
                      </div>
                      {p.user.username && p.user.email && (
                        <p className="truncate text-xs text-gray-400">{p.user.email}</p>
                      )}
                    </div>
                    {canRemoveMember && (
                      <button
                        type="button"
                        onClick={() => void handleRemove(p)}
                        disabled={removingId === p.user.id}
                        className="hidden shrink-0 rounded p-0.5 text-gray-400 hover:bg-red-50 hover:text-red-500 group-hover:block disabled:opacity-50"
                        aria-label={`Remove ${p.user.username || p.user.email}`}
                      >
                        {removingId === p.user.id
                          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          : <Trash2 className="h-3.5 w-3.5" />}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          </Section>
        ) : (
          /* DM: profile card(s) for the other person(s) */
          <div className="border-b border-gray-100 px-4 py-5">
            {participants
              .filter((p) => p.user.id !== currentUserId)
              .map((p) => (
                <div key={p.id} className="flex flex-col items-center gap-3 text-center">
                  <Avatar user={p.user} size="lg" />
                  <div>
                    <p className="text-base font-semibold text-gray-800">
                      {p.user.username || p.user.email}
                    </p>
                    {p.user.username && p.user.email && (
                      <p className="mt-0.5 text-sm text-gray-400">{p.user.email}</p>
                    )}
                  </div>
                </div>
              ))}
          </div>
        )}

        {isGroup && canManageChannel && (
          <Section
            title={`Managers · ${managerParticipants.length}`}
            icon={<ShieldCheck className="h-4 w-4" />}
            defaultOpen
            contentClassName="px-4 pb-4"
          >
            <div className="space-y-3">
              <p className="text-xs leading-snug text-gray-500">
                Managers can edit channel details, manage members, pin messages, and change access settings.
              </p>

              <div>
                <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400">Current managers</p>
                <ul className="space-y-1">
                  {managerParticipants.map((p) => {
                    const isCreator = Boolean(metadataSource.created_by_id && p.user.id === metadataSource.created_by_id);
                    const canRemoveManager = p.is_manager && !isCreator && managerCount > 1;
                    return (
                      <li key={`manager-${p.id}`} className="group flex items-center gap-2.5 rounded-md px-1 py-1.5 hover:bg-gray-50">
                        <Avatar user={p.user} />
                        <div className="min-w-0 flex-1">
                          <div className="flex min-w-0 items-center gap-1.5">
                            <p className="truncate text-sm font-medium text-gray-800">
                              {p.user.username || p.user.email}
                            </p>
                            {isCreator && (
                              <span className="shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
                                Creator
                              </span>
                            )}
                          </div>
                          {p.user.username && p.user.email && (
                            <p className="truncate text-xs text-gray-400">{p.user.email}</p>
                          )}
                        </div>
                        {canRemoveManager && (
                          <button
                            type="button"
                            onClick={() => void handleManagerToggle(p)}
                            disabled={managerSavingId === p.user.id}
                            className="hidden shrink-0 rounded px-2 py-1 text-[11px] font-medium text-gray-400 hover:bg-red-50 hover:text-red-500 group-hover:block disabled:opacity-50"
                          >
                            {managerSavingId === p.user.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Remove'}
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>

              <div>
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">Add manager</p>
                  <span className="text-[10px] text-gray-400">{assignedManagerCount}/5 assigned</span>
                </div>
                {nonManagerParticipants.length === 0 ? (
                  <p className="rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-500">
                    Everyone in this channel is already a manager.
                  </p>
                ) : (
                  <ul className="space-y-1">
                    {nonManagerParticipants.map((p) => {
                      const managerLimitReached = assignedManagerCount >= 5;
                      return (
                        <li key={`manager-add-${p.id}`} className="group flex items-center gap-2.5 rounded-md px-1 py-1.5 hover:bg-gray-50">
                          <Avatar user={p.user} />
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium text-gray-800">
                              {p.user.username || p.user.email}
                            </p>
                            {p.user.username && p.user.email && (
                              <p className="truncate text-xs text-gray-400">{p.user.email}</p>
                            )}
                          </div>
                          <button
                            type="button"
                            onClick={() => void handleManagerToggle(p)}
                            disabled={managerSavingId === p.user.id || managerLimitReached}
                            title={managerLimitReached ? 'Manager limit reached' : undefined}
                            className="shrink-0 rounded px-2 py-1 text-[11px] font-medium text-teal-700 hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            {managerSavingId === p.user.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Make manager'}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </div>
          </Section>
        )}

        {isGroup && canManageChannel && (
          <Section
            title="Channel access"
            icon={<Globe2 className="h-4 w-4" />}
            defaultOpen
            contentClassName="px-4 pb-4"
          >
            <div className="space-y-2">
              {CHANNEL_VISIBILITY_OPTIONS.map((option) => {
                const selected = channelVisibility === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => void handleVisibilityChange(option.value)}
                    disabled={savingVisibility || selected}
                    className={[
                      'flex w-full items-start gap-2 rounded-md border px-3 py-2 text-left transition disabled:cursor-default',
                      selected
                        ? 'border-teal-100 bg-teal-50 text-teal-800'
                        : 'border-gray-200 text-gray-700 hover:border-teal-200 hover:bg-teal-50/60',
                    ].join(' ')}
                  >
                    <span className={[
                      'mt-0.5 h-3.5 w-3.5 shrink-0 rounded-full border-2',
                      selected ? 'border-teal-500 bg-teal-500' : 'border-gray-300',
                    ].join(' ')} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-xs font-semibold">{option.label}</span>
                      <span className="mt-0.5 block text-[11px] leading-snug text-gray-500">{option.description}</span>
                    </span>
                    {savingVisibility && selected && <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-teal-500" />}
                  </button>
                );
              })}
            </div>
          </Section>
        )}

        {/* Files */}
        <Section
          title={`Files${filesTotal > 0 ? ` · ${filesTotal}` : ''}`}
          icon={<FileText className="h-4 w-4" />}
          defaultOpen={false}
          onOpen={loadFiles}
        >
          {filesLoading ? (
            <div className="flex justify-center py-4">
              <Loader2 className="h-4 w-4 animate-spin text-gray-300" />
            </div>
          ) : files.length === 0 ? (
            <p className="text-sm text-gray-400 italic">No files shared in this channel yet.</p>
          ) : (
            <ul className="space-y-1">
              {files.map((file) => {
                const icon = file.file_type === 'image' ? <Image className="h-4 w-4 shrink-0 text-blue-400" />
                  : file.file_type === 'video' ? <Video className="h-4 w-4 shrink-0 text-purple-400" />
                  : file.file_type === 'audio' ? <Music className="h-4 w-4 shrink-0 text-green-400" />
                  : <FileText className="h-4 w-4 shrink-0 text-gray-400" />;
                const sizeKb = file.file_size ? Math.round(file.file_size / 1024) : null;
                return (
                  <li key={file.id} className="group flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-gray-50">
                    {icon}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-gray-700">{file.original_filename}</p>
                      <p className="text-[11px] text-gray-400">
                        {file.uploader?.username || file.uploader?.email}
                        {sizeKb !== null && <> · {sizeKb} KB</>}
                      </p>
                    </div>
                    <a
                      href={file.file_url}
                      download={file.original_filename}
                      target="_blank"
                      rel="noreferrer"
                      className="hidden shrink-0 rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 group-hover:block"
                      aria-label="Download"
                    >
                      <Download className="h-3.5 w-3.5" />
                    </a>
                  </li>
                );
              })}
            </ul>
          )}
        </Section>

        {/* Pinned messages */}
        <Section
          title={`Pinned messages${pins.length > 0 ? ` · ${pins.length}` : ''}`}
          icon={<Pin className="h-4 w-4" />}
          defaultOpen={false}
          onOpen={loadPins}
          testId="pinned-messages-section-toggle"
        >
          {pinsLoading ? (
            <div className="flex items-center gap-2 py-1 text-sm text-gray-400" role="status">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading pinned messages…
            </div>
          ) : pinsError ? (
            <div className="space-y-2" role="alert">
              <p className="text-sm text-red-600">{pinsError}</p>
              <button
                type="button"
                onClick={() => void fetchPins()}
                className="text-xs font-medium text-teal-700 hover:text-teal-800"
              >
                Try again
              </button>
            </div>
          ) : pins.length === 0 ? (
            <p className="text-sm text-gray-400 italic">No pinned messages yet.</p>
          ) : (
            <ul className="space-y-2" data-testid="pinned-messages-list">
              {pins.map((pin) => {
                const sender = pin.message.sender?.username || pin.message.sender?.email || 'Unknown';
                return (
                  <li
                    key={pin.id}
                    className="group rounded-lg border border-gray-200/80 bg-white p-2.5 shadow-[0_1px_3px_rgba(16,24,40,0.06)] transition-all duration-200 hover:-translate-y-0.5 hover:border-teal-200 hover:shadow-[0_6px_16px_rgba(16,24,40,0.10)]"
                    data-testid="pinned-message-item"
                  >
                    <div className="flex items-center gap-2">
                      <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[9px] font-bold text-white ${avatarColor(pin.message.sender?.id)}`}>
                        {sender.charAt(0).toUpperCase()}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[11px] font-semibold text-gray-700">
                        {sender}
                      </span>
                    </div>
                    {onJumpToMessage ? (
                      <button
                        type="button"
                        onClick={() => onJumpToMessage(pin.message.id, pin.message.parent_message_id ?? null)}
                        className="mt-1 block w-full rounded text-left"
                        title="Jump to message"
                        aria-label={`Jump to pinned message: ${pin.message.content || 'attachment'}`}
                      >
                        <span className="block text-[11px] text-gray-500 line-clamp-2 [overflow-wrap:anywhere]">
                          {pin.message.content || '(attachment)'}
                        </span>
                      </button>
                    ) : (
                      <p className="mt-1 text-[11px] text-gray-500 line-clamp-2">{pin.message.content || '(attachment)'}</p>
                    )}
                    {/* Pin audit context + aligned actions */}
                    <div className="mt-1 flex items-center gap-1.5">
                      <p className="min-w-0 flex-1 truncate text-[10px] text-gray-400" data-testid="pinned-message-meta">
                        {pin.pinned_by
                          ? `by ${pin.pinned_by.username || pin.pinned_by.email} · `
                          : ''}
                        {format(parseISO(pin.created_at), 'MMM d, yyyy · h:mm a')}
                      </p>
                      {onJumpToMessage && (
                        <button
                          type="button"
                          onClick={() => onJumpToMessage(pin.message.id, pin.message.parent_message_id ?? null)}
                          className="shrink-0 rounded p-1 text-gray-300 transition hover:bg-teal-50 hover:text-teal-600"
                          aria-label={`Jump to pinned message: ${pin.message.content || 'attachment'}`}
                          title="Jump to message"
                        >
                          <ArrowUpRight className="h-3.5 w-3.5" />
                        </button>
                      )}
                      {canManageChannel && (
                        <button
                          type="button"
                          onClick={() => void handleUnpin(pin)}
                          disabled={unpinningId === pin.id}
                          className="shrink-0 rounded p-1 text-gray-400 transition hover:bg-red-50 hover:text-red-500 disabled:opacity-50"
                          aria-label="Unpin"
                          title="Unpin"
                        >
                          {unpinningId === pin.id
                            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            : <X className="h-3.5 w-3.5" />}
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </Section>

        {/* Scheduled messages */}
        <Section
          title={`Scheduled${pendingScheduled.length > 0 ? ` · ${pendingScheduled.length}` : ''}`}
          icon={<Clock className="h-4 w-4" />}
          defaultOpen={false}
          onOpen={loadScheduled}
        >
          {pendingScheduled.length === 0 ? (
            <p className="text-sm text-gray-400 italic">No scheduled messages pending.</p>
          ) : (
            <ul className="divide-y divide-gray-100">
              {pendingScheduled.map((msg) => {
                const scheduledDate = new Date(msg.scheduled_at);
                const preview = msg.content?.trim() || '(no text)';
                const isRescheduling = reschedulingId === msg.id;
                const todayStr = localDateString();
                const minTime = rescheduleDate === todayStr
                  ? (() => { const n = new Date(Date.now() + 2 * 60 * 1000); return `${String(n.getHours()).padStart(2, '0')}:${String(n.getMinutes()).padStart(2, '0')}`; })()
                  : undefined;
                return (
                  <li key={msg.id} className="py-2.5 first:pt-0 last:pb-0">
                    <div className="flex items-start gap-2">
                      <div className="w-0.5 self-stretch rounded-full bg-amber-400 shrink-0" />
                      <div className="min-w-0 flex-1">
                        <p className="flex items-center gap-1 text-[11px] font-semibold text-amber-600">
                          <Clock className="h-2.5 w-2.5 shrink-0" />
                          {format(scheduledDate, 'MMM d, yyyy · h:mm a')}
                        </p>
                        <p className="mt-0.5 text-[12px] text-gray-600 line-clamp-2 [overflow-wrap:anywhere]">
                          {preview}
                        </p>
                      </div>
                      {!isRescheduling && (
                        <div className="flex shrink-0 flex-col gap-1 mt-0.5">
                          <button
                            type="button"
                            onClick={() => openReschedule(msg)}
                            disabled={cancellingScheduledId === msg.id}
                            className="rounded px-2 py-0.5 text-[11px] font-medium text-teal-600 hover:bg-teal-50 disabled:opacity-50"
                          >
                            Reschedule
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleCancelScheduled(msg)}
                            disabled={cancellingScheduledId === msg.id}
                            className="rounded px-2 py-0.5 text-[11px] font-medium text-gray-400 hover:bg-red-50 hover:text-red-500 disabled:opacity-50"
                            aria-label="Cancel scheduled message"
                          >
                            {cancellingScheduledId === msg.id
                              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              : 'Cancel'}
                          </button>
                        </div>
                      )}
                    </div>

                    {/* Inline reschedule form */}
                    {isRescheduling && (
                      <div ref={rescheduleFormRef} className="mt-2 ml-2.5 space-y-1.5">
                        <div className="flex gap-1.5">
                          <DatePickerUp
                            value={rescheduleDate}
                            min={todayStr}
                            onChange={setRescheduleDate}
                            className="w-full rounded border border-gray-300 px-2 py-1 text-xs focus:border-teal-400 focus:outline-none focus:ring-1 focus:ring-teal-400"
                          />
                          <input
                            type="time"
                            value={rescheduleTime}
                            min={minTime}
                            onChange={(e) => setRescheduleTime(e.target.value)}
                            className="w-24 rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 focus:border-teal-400 focus:outline-none focus:ring-1 focus:ring-teal-400"
                          />
                        </div>
                        <div className="flex gap-1.5">
                          <button
                            type="button"
                            onClick={() => void confirmReschedule(msg)}
                            disabled={rescheduleSaving || !rescheduleDate || !rescheduleTime}
                            className="flex items-center gap-1 rounded bg-teal-500 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-teal-600 disabled:opacity-50"
                          >
                            {rescheduleSaving
                              ? <><Loader2 className="h-3 w-3 animate-spin" /> Saving…</>
                              : 'Confirm'}
                          </button>
                          <button
                            type="button"
                            onClick={() => setReschedulingId(null)}
                            disabled={rescheduleSaving}
                            className="rounded px-2.5 py-1 text-[11px] font-medium text-gray-500 hover:bg-gray-100 disabled:opacity-50"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </Section>

        {/* Notification settings */}
        <div ref={notificationsSectionRef}>
          <Section title="Notifications" icon={<Bell className="h-4 w-4" />} defaultOpen={false} contentClassName="px-4 pb-2">
            <div className="space-y-3">
              {/* Mute toggle */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-700">Mute channel</p>
                  <p className="text-xs text-gray-400">
                    {mutedUntilLabel ? `Muted until ${mutedUntilLabel}` : isMuted ? 'Muted until you turn it back on' : 'Silence all notifications'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void handleMuteToggle()}
                  disabled={savingNotif}
                  className={[
                    'relative h-5 w-9 rounded-full transition-colors disabled:opacity-50',
                    isMuted ? 'bg-[#3CCED7]' : 'bg-gray-300',
                  ].join(' ')}
                  role="switch"
                  aria-checked={isMuted}
                >
                  <span className={[
                    'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform',
                    isMuted ? 'translate-x-4' : 'translate-x-0.5',
                  ].join(' ')} />
                </button>
              </div>

              <div>
                <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400">Temporarily mute</p>
                <div className="flex flex-wrap gap-1.5">
                  {TEMP_MUTE_OPTIONS.map((option) => {
                    const label = option.id === '1h' ? '1 hour' : option.id === 'tomorrow' ? 'Tomorrow' : '1 week';
                    return (
                      <button
                        key={option.id}
                        type="button"
                        onClick={() => void handleTemporaryMute(option.id)}
                        disabled={savingNotif}
                        className="h-7 rounded-full border border-gray-200 px-3 text-[11px] font-medium text-gray-600 hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 disabled:opacity-50"
                      >
                        {label}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Notification level — only shown when not muted */}
              {!isMuted && (
                <div>
                  <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400">Notify me about</p>
                  <div className="space-y-1">
                    {(['all', 'mentions'] as const).map((level) => (
                      <button
                        key={level}
                        type="button"
                        onClick={() => void handleNotifLevel(level)}
                        disabled={savingNotif}
                        className={[
                          'flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs disabled:opacity-50',
                          notifLevel === level ? 'bg-teal-50 font-medium text-teal-700' : 'text-gray-600 hover:bg-gray-50',
                        ].join(' ')}
                      >
                        <span className={`h-3 w-3 rounded-full border-2 ${notifLevel === level ? 'border-teal-500 bg-teal-500' : 'border-gray-300'}`} />
                        {level === 'all' ? 'All messages' : 'Mentions only'}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </Section>
        </div>

        {/* Leave channel — group chats only */}
        {isGroup && (
          <div ref={leaveSectionRef} className="border-b border-gray-100">
            {confirmingLeave ? (
              <>
                <div className="flex w-full items-center gap-2 px-4 py-3 text-left text-red-600">
                  <LogOut className="h-4 w-4 shrink-0" />
                  <span className="flex-1 text-sm font-semibold">Leave channel</span>
                </div>
                <div className="px-4 pb-4">
                  <div ref={leaveConfirmRef} className="rounded-lg border border-red-200 bg-red-50 p-3">
                    <p className="text-sm font-medium text-gray-800">
                      Leave {chat.name ? `#${chat.name}` : 'this channel'}?
                    </p>
                    <p className="mt-0.5 text-xs text-gray-500">
                      You&apos;ll stop receiving messages and need to be re-added to rejoin.
                    </p>
                    <div className="mt-2.5 flex gap-1.5">
                      <button
                        type="button"
                        onClick={() => void handleLeave()}
                        disabled={leaving}
                        className="flex items-center gap-1 rounded bg-red-500 px-2.5 py-1 text-xs font-medium text-white hover:bg-red-600 disabled:opacity-50"
                      >
                        {leaving
                          ? <><Loader2 className="h-3 w-3 animate-spin" /> Leaving…</>
                          : 'Leave channel'}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmingLeave(false)}
                        disabled={leaving}
                        className="rounded px-2.5 py-1 text-xs font-medium text-gray-500 hover:bg-gray-100 disabled:opacity-50"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmingLeave(true)}
                className="flex w-full items-center gap-2 px-4 py-3 text-left text-red-600 hover:bg-red-50"
              >
                <LogOut className="h-4 w-4 shrink-0" />
                <span className="flex-1 text-sm font-semibold">Leave channel</span>
              </button>
            )}
            {createdMetaLabel && (
              <p className="px-4 pb-3 text-[11px] text-gray-400">
                {createdMetaLabel}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
