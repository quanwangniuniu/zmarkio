/**
 * Hand a Blob to the browser as a file download.
 *
 * Lifted out of the meta-ads export menu so the quality inspection export can
 * reuse it rather than copy it a third time.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** Pull `filename="..."` out of a Content-Disposition header. */
export function filenameFromContentDisposition(
  disposition: string | undefined,
  fallback: string,
): string {
  const match = (disposition ?? '').match(/filename="([^"]+)"/i);
  return match?.[1] ?? fallback;
}

export default downloadBlob;
