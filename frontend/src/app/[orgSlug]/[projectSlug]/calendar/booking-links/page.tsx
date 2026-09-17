// Nested org/project alias for the booking link manager.
//
// The sidebar routes through useBuildUrl, which prefixes every path with
// /[orgSlug]/[projectSlug], so the flat (project) route alone is not reachable
// from navigation. Mirrors ../page.tsx for /calendar.
export { default } from '@/app/(project)/calendar/booking-links/page';
