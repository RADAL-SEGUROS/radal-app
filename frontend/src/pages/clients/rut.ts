/**
 * Chilean RUT — client-side mirror of `backend/app/services/identifiers.py`.
 *
 * The server is still the authority (it re-validates and normalizes on every
 * write); this exists so the create form can reject a typo before the round
 * trip and show the normalized value back to the user.
 *
 * Normalized form: `BODY-DV`, no dots, uppercase K.
 */

/** Strip dots/spaces/hyphens and uppercase. */
function strip(raw: string): string {
  return raw.replace(/[.\s-]/g, "").toUpperCase();
}

/** The mod-11 check digit for a numeric body. */
export function checkDigit(body: string): string {
  let sum = 0;
  let factor = 2;
  for (let i = body.length - 1; i >= 0; i -= 1) {
    sum += Number(body[i]) * factor;
    factor = factor === 7 ? 2 : factor + 1;
  }
  const remainder = 11 - (sum % 11);
  if (remainder === 11) return "0";
  if (remainder === 10) return "K";
  return String(remainder);
}

/** `"76.086.428-5"` -> `"76086428-5"`. Returns `null` when invalid. */
export function normalizeRut(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const clean = strip(raw);
  if (clean.length < 2) return null;
  const body = clean.slice(0, -1);
  const dv = clean.slice(-1);
  if (!/^\d+$/.test(body)) return null;
  if (!/^[\dK]$/.test(dv)) return null;
  if (checkDigit(body) !== dv) return null;
  return `${body}-${dv}`;
}

export function isValidRut(raw: string | null | undefined): boolean {
  return normalizeRut(raw) !== null;
}

/** Display form with thousand dots: `"76086428-5"` -> `"76.086.428-5"`. */
export function formatRut(rut: string | null | undefined): string {
  if (!rut) return "—";
  const clean = strip(rut);
  if (clean.length < 2) return rut;
  const body = clean.slice(0, -1);
  const dv = clean.slice(-1);
  return `${body.replace(/\B(?=(\d{3})+(?!\d))/g, ".")}-${dv}`;
}
