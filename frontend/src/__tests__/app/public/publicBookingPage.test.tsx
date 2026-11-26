/**
 * MED-486: the public booking page must keep working when Calendar surfaces are
 * disabled, so booking links already sent to prospects never break. It must not
 * read the per-project Calendar config or mount a module gate.
 */
import fs from 'fs';
import path from 'path';
import { render, screen, waitFor } from '@testing-library/react';
import BookingPage from '@/app/(public)/book/[orgSlug]/[linkSlug]/page';
import { PublicBookingAPI } from '@/lib/api/calendarApi';
import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import { storeCalendarConfig } from '@/lib/calendar/calendarConfig';
import { useAuthStore } from '@/lib/authStore';
import type { OptionConfig, OptionModuleConfig } from '@/types/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchOptionConfig: jest.fn() }));

const off = (field: string, value: string): OptionConfig => ({
  field, value, default_label: value, label: null, color: null, enabled: false, order: 0, is_default: false, editable: [],
});

/** Every Calendar surface disabled except the default week tab, which cannot be. */
const CALENDAR_DISABLED: OptionModuleConfig = {
  can_edit: true,
  options: [
    ...['day', 'month', 'year', 'agenda'].map((view) => off('tab', view)),
    ...['scope_switch', 'toolbar_today', 'toolbar_navigation', 'toolbar_all_events', 'toolbar_ask_agent'].map((s) => off('section', s)),
    off('panel', 'sidebar'),
    off('nav_item', 'booking_links'),
  ],
};

const SRC = path.resolve(__dirname, '../../..');

/** Local source files reachable from `entry` through `@/` and relative imports. */
const importGraph = (entry: string): string[] => {
  const seen = new Set<string>();
  const visit = (file: string) => {
    if (seen.has(file)) return;
    seen.add(file);
    const source = fs.readFileSync(file, 'utf8');
    for (const [, spec] of source.matchAll(/from\s+['"]([^'"]+)['"]/g)) {
      if (!spec.startsWith('@/') && !spec.startsWith('.')) continue;
      const base = spec.startsWith('@/') ? path.join(SRC, spec.slice(2)) : path.resolve(path.dirname(file), spec);
      const resolved = ['.tsx', '.ts', '/index.tsx', '/index.ts'].map((ext) => base + ext).find((f) => fs.existsSync(f));
      if (resolved) visit(resolved);
    }
  };
  visit(entry);
  return [...seen];
};

describe('public booking page with Calendar disabled', () => {
  const publicDir = path.join(SRC, 'app/(public)/book');
  const publicPages = ['[orgSlug]/[linkSlug]/page.tsx', '[orgSlug]/[linkSlug]/cancel/page.tsx']
    .map((page) => path.join(publicDir, page));

  it.each(publicPages)('does not import Calendar config or a module gate: %s', (page) => {
    const offenders = importGraph(page).filter((file) => {
      const source = fs.readFileSync(file, 'utf8');
      return /calendar\/calendarConfig|ModuleGate|orgCustomization\/configLoader/.test(source);
    });
    expect(offenders.map((file) => path.relative(SRC, file))).toEqual([]);
  });

  it('still loads availability when every Calendar surface is disabled', async () => {
    storeCalendarConfig(1, CALENDAR_DISABLED);
    useAuthStore.setState({ hasHydrated: true, token: null, user: null, isAuthenticated: false } as never);
    const getAvailability = jest.spyOn(PublicBookingAPI, 'getAvailability').mockReturnValue(new Promise(() => {}));

    render(<BookingPage params={{ orgSlug: 'acme', linkSlug: 'intro-call' }} />);

    await waitFor(() => expect(getAvailability).toHaveBeenCalled());
    expect(getAvailability.mock.calls[0].slice(0, 2)).toEqual(['acme', 'intro-call']);
    expect(screen.getByTestId('booking-loading')).toBeInTheDocument();
    expect(fetchOptionConfig).not.toHaveBeenCalled();

    storeCalendarConfig(1, { options: [], can_edit: true });
  });
});
