// Next.js does not inherit layouts across route groups, so the org/project tree
// needs its own re-export — without it these pages render with no navigation,
// no dashboard chrome and no auth wrapper.
export { default } from '@/app/(project)/admin/csm/settings/layout';
