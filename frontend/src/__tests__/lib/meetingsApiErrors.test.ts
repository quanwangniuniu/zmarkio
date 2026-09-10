import { formatMeetingsApiError } from '@/lib/meetingsApiErrors';

describe('formatMeetingsApiError', () => {
  it('returns fallback for empty errors', () => {
    expect(formatMeetingsApiError({}, 'fallback')).toBe('fallback');
    expect(formatMeetingsApiError({ message: 'boom' }, 'fallback')).toBe('boom');
  });

  it('formats 404 with and without useful detail', () => {
    expect(
      formatMeetingsApiError(
        { response: { status: 404, data: { detail: 'Not found.' } } },
        'fallback'
      )
    ).toContain('Nothing was found');

    expect(
      formatMeetingsApiError(
        { response: { status: 404, data: { detail: 'Project missing' } } },
        'fallback'
      )
    ).toBe('Project missing (404)');
  });

  it('formats 403/400 and generic detail shapes', () => {
    expect(
      formatMeetingsApiError({ response: { status: 403, data: {} } }, 'fallback')
    ).toContain('do not have permission');

    expect(
      formatMeetingsApiError(
        { response: { status: 403, data: { detail: 'Forbidden for project' } } },
        'fallback'
      )
    ).toBe('Forbidden for project');

    expect(
      formatMeetingsApiError(
        { response: { status: 400, data: { detail: ['Bad', { x: 1 }] } } },
        'fallback'
      )
    ).toContain('Bad');

    expect(
      formatMeetingsApiError(
        { response: { status: 400, data: { error: 'invalid payload' } } },
        'fallback'
      )
    ).toBe('invalid payload');

    expect(
      formatMeetingsApiError(
        { response: { status: 500, data: { detail: { nested: true } } } },
        'fallback'
      )
    ).toContain('nested');

    expect(
      formatMeetingsApiError(
        { response: { status: 500, data: { error: 42 } } },
        'fallback'
      )
    ).toBe('42');
  });
});
