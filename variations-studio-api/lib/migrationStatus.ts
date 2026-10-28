/** Outcome reported for each tenant in the migration runner's JSON report. */
export enum TenantStatus {
  Pending = 'pending',
  UpToDate = 'up_to_date',
  Applied = 'applied',
  SkippedMissingSchema = 'skipped_missing_schema',
  Failed = 'failed',
  CommitUnknown = 'commit_unknown',
}
