/**
 * File download / preview — one path for every stored document.
 *
 * `GET /documents/{id}/download` mints the link (`DocumentDownload.url`):
 *
 *  - on the S3 backend (dev, prod) an ABSOLUTE presigned URL. It is opened
 *    DIRECTLY — a plain anchor / `window.open`, never axios: the signature is
 *    in the query string, and our `Authorization` / `X-Radal-Token` /
 *    `x-amz-content-sha256` headers would only break it. `GET …/content` on
 *    that backend answers 409 ("Use the presigned URL from /download"), which
 *    is what made every "Descargar" fail on dev;
 *  - on the local backend a RELATIVE path to the broker-scoped, AUTHENTICATED
 *    `…/content` route. Opening it as a link sends no auth header and 401s, so
 *    the bytes are fetched through the shared axios instance (auth + OAC
 *    headers from `lib/api.ts`, untouched) and handed to the browser as a Blob.
 *
 * `openFileUrl` applies the same rule to any `DocumentDownload`-shaped URL
 * another router returns (packs, archives, offering PDFs). Links are fetched
 * fresh on click: a presigned URL expires (15 min), so never render a cached
 * one into an `href`.
 */
import api, { API_URL } from "@/lib/api";
import type { DocumentDownload } from "@/api/types";

/** Save an in-memory Blob to disk under `filename` via a throwaway anchor. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "download";
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Give the browser a beat to start the download before revoking.
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

function filenameFromDisposition(disposition: string | undefined): string | null {
  if (!disposition) return null;
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1]);
    } catch {
      /* fall through */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain?.[1] ?? null;
}

export function isAbsoluteUrl(url: string | null | undefined): url is string {
  return !!url && /^https?:\/\//i.test(url);
}

/**
 * A relative server URL (`/api/v1/documents/7/content`) as a path for the
 * axios instance, whose `baseURL` already carries the API prefix.
 */
function toApiPath(url: string): string {
  let prefix = "";
  try {
    prefix = new URL(API_URL, window.location.origin).pathname.replace(/\/+$/, "");
  } catch {
    /* keep "" */
  }
  if (prefix && (url === prefix || url.startsWith(`${prefix}/`))) {
    return url.slice(prefix.length) || "/";
  }
  return url;
}

/**
 * Open a tab SYNCHRONOUSLY (inside the click's user activation) so the popup
 * blocker lets it through, then point it at the file once the link is known.
 * `null` when the browser refused — the caller then clicks a fresh anchor.
 */
function reserveTab(): Window | null {
  try {
    const tab = window.open("", "_blank");
    if (tab) tab.opener = null;
    return tab;
  } catch {
    return null;
  }
}

function clickAnchor(href: string, opts: { download?: string; newTab?: boolean }) {
  const a = document.createElement("a");
  a.href = href;
  if (opts.download) a.download = opts.download;
  if (opts.newTab) a.target = "_blank";
  a.rel = "noopener noreferrer";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function fetchBlob(path: string): Promise<{ blob: Blob; name: string | null }> {
  const res = await api.get<Blob>(toApiPath(path), { responseType: "blob" });
  const disposition = res.headers?.["content-disposition"] as string | undefined;
  return { blob: res.data, name: filenameFromDisposition(disposition) };
}

export type OpenMode = "download" | "preview";

/**
 * `GET /documents/{id}/download` — a fresh, short-lived link. Review r2 B7:
 * `mode=preview` mints an `inline` link (the PDF opens in the tab instead of
 * downloading and leaving an `about:blank` behind), `mode=download` an
 * `attachment` one carrying the ORIGINAL file name.
 */
export async function fetchDocumentLink(
  documentId: number,
  mode: OpenMode = "download",
): Promise<DocumentDownload> {
  const { data } = await api.get<DocumentDownload>(`/documents/${documentId}/download`, {
    params: { mode },
  });
  return data;
}

/**
 * Open a server-minted file URL: absolute → straight to the browser (no
 * axios, no headers); relative → authenticated blob. Rejects on failure so
 * the caller can toast; a reserved tab is closed again.
 *
 * `reserved`: a tab opened by the caller before its own await (see
 * `previewDocument`); `undefined` lets a preview reserve one itself, `null`
 * means "no tab".
 */
export async function openFileUrl(
  url: string,
  mode: OpenMode,
  filename?: string | null,
  reserved?: Window | null,
): Promise<void> {
  const tab = reserved === undefined ? (mode === "preview" ? reserveTab() : null) : reserved;
  try {
    if (isAbsoluteUrl(url)) {
      if (tab) {
        tab.location.href = url;
      } else {
        // The presigned S3 object is cross-origin: `download` is a hint the
        // browser may ignore, so a new tab keeps the SPA where it is.
        clickAnchor(url, { download: filename ?? undefined, newTab: true });
      }
      return;
    }
    const { blob, name } = await fetchBlob(url);
    if (mode === "download") {
      tab?.close();
      saveBlob(blob, filename || name || "documento");
      return;
    }
    const objectUrl = URL.createObjectURL(blob);
    if (tab) tab.location.href = objectUrl;
    else clickAnchor(objectUrl, { newTab: true });
    setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
  } catch (error) {
    tab?.close();
    throw error;
  }
}

/** Download a stored document (fresh link each time — S3 links expire). */
export async function downloadDocument(
  documentId: number,
  filename?: string | null,
): Promise<void> {
  const link = await fetchDocumentLink(documentId, "download");
  await openFileUrl(
    link.url,
    "download",
    filename || link.original_name || `documento-${documentId}`,
    null,
  );
}

/**
 * Preview a stored document in a new tab. The tab is reserved BEFORE the link
 * request so the popup blocker never eats it.
 */
export async function previewDocument(documentId: number): Promise<void> {
  const tab = reserveTab();
  try {
    const link = await fetchDocumentLink(documentId, "preview");
    await openFileUrl(link.url, "preview", link.original_name, tab);
  } catch (error) {
    tab?.close();
    throw error;
  }
}

/**
 * Fetch any authenticated endpoint that STREAMS bytes and save the result.
 *
 * `downloadDocument` above is for the stored-`document` route; this is for the
 * endpoints that render on demand and return the file itself — the expediente
 * completo PDF, the comparison and propuesta packs. Same reason it exists:
 * those routes are auth-gated, so a plain link 401s.
 */
export async function downloadFrom(
  path: string,
  filename: string,
): Promise<void> {
  const res = await api.get<Blob>(path, { responseType: "blob" });
  const disposition = res.headers?.["content-disposition"] as string | undefined;
  saveBlob(res.data, filenameFromDisposition(disposition) || filename);
}
