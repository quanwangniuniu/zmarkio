import {
  NOTIFICATIONS_FROM_PARAM,
  isNotificationsAreaPath,
  buildFromReturnValue,
  getSafeInternalReturnPath,
  buildNotificationsListHref,
  buildNotificationsPreferencesHref,
  buildPreferencesHrefFromNotificationsSearch,
  buildNotificationsListHrefPreservingFrom,
} from '@/lib/notificationsNavigation';

describe('notificationsNavigation', () => {
  it('detects notifications area paths', () => {
    expect(isNotificationsAreaPath('/notifications')).toBe(true);
    expect(isNotificationsAreaPath('/notifications/preferences')).toBe(true);
    expect(isNotificationsAreaPath('/tasks')).toBe(false);
  });

  it('builds from return values and hardens open redirects', () => {
    expect(buildFromReturnValue('/tasks', '?q=1')).toBe('/tasks?q=1');
    expect(buildFromReturnValue('/tasks', 'q=1')).toBe('/tasks?q=1');
    expect(buildFromReturnValue('/notifications', '?x=1')).toBe('/');
    expect(buildFromReturnValue('', '')).toBe('/');

    expect(getSafeInternalReturnPath(null)).toBeNull();
    expect(getSafeInternalReturnPath('')).toBeNull();
    expect(getSafeInternalReturnPath('%E0%A4%A')).toBeNull();
    expect(getSafeInternalReturnPath('https://evil.com')).toBeNull();
    expect(getSafeInternalReturnPath('//evil.com')).toBeNull();
    expect(getSafeInternalReturnPath('/tasks?tab=1')).toBe('/tasks?tab=1');
  });

  it('builds list and preferences hrefs', () => {
    expect(buildNotificationsListHref('/dashboard', '?a=1')).toContain(
      `${NOTIFICATIONS_FROM_PARAM}=`
    );
    expect(buildNotificationsPreferencesHref('/dashboard')).toContain(
      '/notifications/preferences?'
    );
    expect(buildPreferencesHrefFromNotificationsSearch(null)).toBe(
      '/notifications/preferences'
    );
    expect(buildPreferencesHrefFromNotificationsSearch('/home')).toContain(
      'from=%2Fhome'
    );
    expect(buildNotificationsListHrefPreservingFrom(null)).toBe('/notifications');
    expect(buildNotificationsListHrefPreservingFrom('/home')).toContain('from=%2Fhome');
  });
});
