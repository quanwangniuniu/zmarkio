/**
 * Re-export barrel — preserves backward compatibility.
 * Actual implementations live in src/platform/http/.
 */
export {
  ApiError,
  jsonError,
  responseFromUnknown,
  readJsonBody,
  projectIdParam,
  parseSelectedIds,
} from '@/src/platform/http';
export { startBulkPost } from '@/src/platform/http/bulkRequest';
