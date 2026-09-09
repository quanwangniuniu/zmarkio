'use client';

import { useState } from 'react';
import { Loader2, UserPlus, X } from 'lucide-react';
import {
  BookingLinkAPI,
  type BookingLinkDTO,
} from '@/lib/api/calendarApi';
import { namedInvitees } from '@/lib/bookingLinkList';

export type InviteCandidate = {
  key: string;
  label: string;
  email: string;
  userId: number | null;
  source: 'member' | 'customer';
};

interface BookingLinkQuickInviteProps {
  link: BookingLinkDTO;
  shareUrl: string;
  candidates: InviteCandidate[];
  canSearchPeople: boolean;
  onInvited: (link: BookingLinkDTO) => void;
  onError: (message: string) => void;
}

const looksLikeEmail = (value: string) =>
  /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());

export default function BookingLinkQuickInvite({
  link,
  shareUrl,
  candidates,
  canSearchPeople,
  onInvited,
  onError,
}: BookingLinkQuickInviteProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [saving, setSaving] = useState(false);
  const invitees = namedInvitees(link);
  const guestEmails = (link.invitee_emails ?? []).map((address) =>
    address.trim().toLowerCase(),
  );
  const namedEmails = new Set([
    ...invitees.map((person) => person.email.trim().toLowerCase()),
    ...guestEmails,
  ]);
  const namedIds = new Set(
    invitees.map((person) => person.id).filter((id): id is number => id != null),
  );
  const matches =
    canSearchPeople && query.trim()
      ? candidates
          .filter((candidate) => {
            if (candidate.userId != null && namedIds.has(candidate.userId)) {
              return false;
            }
            if (namedEmails.has(candidate.email.toLowerCase())) return false;
            return `${candidate.label} ${candidate.email}`
              .toLowerCase()
              .includes(query.trim().toLowerCase());
          })
          .slice(0, 6)
      : [];
  const email = query.trim().toLowerCase();
  const canAddEmail =
    looksLikeEmail(email) && !namedEmails.has(email) && matches.length === 0;

  const invite = async (next: {
    invitee_ids?: number[];
    invitee_emails?: string[];
    copyLink?: boolean;
  }) => {
    if (saving) return;
    setSaving(true);
    try {
      const updated = await BookingLinkAPI.update(link.id, next);
      onInvited(updated.data);
      setQuery('');
      setOpen(false);
      if (next.copyLink) {
        try {
          await navigator.clipboard.writeText(shareUrl);
        } catch {
          // The person has no account; the chip is enough if copy is blocked.
        }
      }
    } catch {
      onError('Could not invite that person.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-3" data-testid="booking-link-invitees">
      <div className="flex flex-wrap items-center gap-1.5">
        {link.invitees_only ? (
          <span
            className="rounded-full bg-[#3CCED7]/10 px-2 py-0.5 text-[11px] font-medium text-[#0E8A96]"
            data-testid="booking-link-invitees-only"
          >
            Invitees only
          </span>
        ) : null}
        {invitees.length === 0 && !link.invitees_only ? (
          <span
            className="text-[11px] text-gray-400"
            data-testid="booking-link-open-link"
          >
            Anyone with the link
          </span>
        ) : (
          invitees.map((person) => (
            <span
              key={`${person.id ?? 'e'}:${person.email}`}
              data-testid="booking-link-invitee-chip"
              className="max-w-[12rem] truncate rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600"
            >
              {person.name || person.email}
            </span>
          ))
        )}
        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          data-testid="booking-link-quick-invite"
          className="inline-flex items-center gap-1 rounded-lg px-1.5 py-0.5 text-[11px] font-medium text-[#0E8A96] hover:bg-[#3CCED7]/10"
        >
          <UserPlus className="h-3 w-3" />
          Invite
        </button>
      </div>

      {open && (
        <div className="mt-2">
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              autoComplete="off"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={
                canSearchPeople
                  ? 'Search a colleague or type an email'
                  : 'Type an email address'
              }
              data-testid="booking-link-quick-invite-input"
              className="w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs focus:border-gray-400 focus:outline-none"
            />
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-gray-300" />
            ) : (
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close invite"
                className="rounded-lg p-1 text-gray-400 hover:bg-gray-50 hover:text-gray-700"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          {matches.length > 0 && (
            <ul className="mt-1 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
              {matches.map((candidate) => (
                <li key={candidate.key}>
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() =>
                      invite({
                        invitee_ids:
                          candidate.userId != null
                            ? [...namedIds, candidate.userId]
                            : undefined,
                        invitee_emails:
                          candidate.userId == null
                            ? [...guestEmails, candidate.email.toLowerCase()]
                            : undefined,
                        copyLink: candidate.userId == null,
                      })
                    }
                    className="flex w-full px-2.5 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                  >
                    {candidate.label}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {canAddEmail && (
            <button
              type="button"
              disabled={saving}
              onClick={() =>
                invite({
                  invitee_emails: [...guestEmails, email],
                  copyLink: true,
                })
              }
              data-testid="booking-link-quick-invite-email"
              className="mt-1 w-full rounded-lg border border-dashed border-gray-300 px-2.5 py-1.5 text-left text-xs text-gray-600 hover:bg-gray-50"
            >
              Invite {email} — copies the link
            </button>
          )}
        </div>
      )}
    </div>
  );
}
