import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import CsmSettingsSidebar from '@/components/csm-settings/CsmSettingsSidebar';

const mockPathname = jest.fn();

jest.mock('next/navigation', () => ({
  usePathname: () => mockPathname(),
}));

// Real URLs carry an /{orgSlug}/{projectSlug} prefix.
jest.mock('@/lib/buildUrl', () => ({
  useBuildUrl: () => (path: string) => `/acme/web${path}`,
}));

const PAGES = [
  'Settings Hub',
  'Support Projects',
  'Channels',
  'Work Types',
  'Assignments',
  'SLA Policy',
  'Business Hours',
  'Ticket Statuses',
  'Customer Status Labels',
  'Agent Guidance',
];

function activeLabels() {
  // The active link is the only one carrying the accent bar.
  return screen.getAllByRole('link')
    .filter((link) => link.querySelector('span[aria-hidden]'))
    .map((link) => link.textContent?.trim());
}

describe('CsmSettingsSidebar', () => {
  beforeEach(() => jest.clearAllMocks());

  it('links to every settings page, org/project prefixed', () => {
    mockPathname.mockReturnValue('/acme/web/admin/csm/settings');
    render(<CsmSettingsSidebar />);

    PAGES.forEach((label) => {
      expect(screen.getByRole('link', { name: label })).toHaveAttribute(
        'href',
        expect.stringContaining('/acme/web/admin/csm/settings'),
      );
    });
    expect(screen.getAllByRole('link')).toHaveLength(PAGES.length);
  });

  it.each([
    ['/acme/web/admin/csm/settings/work-types', 'Work Types'],
    ['/acme/web/admin/csm/settings/guidance', 'Agent Guidance'],
    ['/acme/web/admin/csm/settings', 'Settings Hub'],
    // The unprefixed legacy path must keep working too.
    ['/admin/csm/settings/channels', 'Channels'],
  ])('highlights the current page for %s', (pathname, expected) => {
    mockPathname.mockReturnValue(pathname);
    render(<CsmSettingsSidebar />);

    expect(activeLabels()).toEqual([expected]);
  });
});
