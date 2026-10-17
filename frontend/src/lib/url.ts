/**
 * URL guards for values that come from people, not from code.
 *
 * An insurer's `payment_url`, an instruction's `portal_url` or a provider's
 * checkout URL is typed by a broker or a maintainer. Rendering one straight
 * into `href` would let `javascript:` or `data:` URLs through, so a server
 * string becomes a link only when it parses as absolute http(s). Anything else
 * is shown as plain text.
 */

/** The trimmed URL when it is an absolute http(s) URL, otherwise `null`. */
export function safeHttpUrl(value: string | null | undefined): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  try {
    const parsed = new URL(raw);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? raw : null;
  } catch {
    return null;
  }
}
