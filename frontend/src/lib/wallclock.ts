/**
 * Contractual datetimes (policy vigencia, endorsement effect, claim occurrence)
 * are Chile wall-clock values, offset-less — product decision 2026-10-17. The
 * server stores and returns `YYYY-MM-DDTHH:mm:ss` with no `Z`, so a
 * `<input type="datetime-local">` value must go out AS TYPED: never through
 * `new Date(v).toISOString()`, which turns 12:00 Chile into 15:00Z.
 *
 * Event timestamps (`created_at`, `reported_at`, …) are a different thing:
 * they stay UTC instants.
 */

/** `2026-03-01T12:00` (datetime-local) -> `2026-03-01T12:00:00`; blank -> null. */
export function toWallClock(value: string): string | null {
  const v = value.trim();
  if (!v) return null;
  // datetime-local yields `YYYY-MM-DDTHH:mm` or `YYYY-MM-DDTHH:mm:ss`.
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(v) ? `${v}:00` : v;
}
