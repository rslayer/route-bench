/**
 * Upload limits and formatting.
 *
 * MAX_UPLOAD_BYTES mirrors MAX_UPLOAD_BYTES in src/routebench/core/config.py.
 * The server enforces it with a 413; this only exists so a user finds out
 * before spending a minute uploading a file that was always going to bounce.
 */

/** 50 MB — must match core/config.py. */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  const mb = kb / 1024;
  return mb >= 10 ? `${Math.round(mb)} MB` : `${mb.toFixed(1)} MB`;
}
