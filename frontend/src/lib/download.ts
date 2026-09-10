/**
 * Authenticated file download.
 *
 * The backend serves stored bytes from `GET /documents/{id}/content`, an
 * AUTHENTICATED route (`content-disposition: attachment`). Opening it as a
 * plain `<a href>`/`window.open` sends no `Authorization` header, so it 401s —
 * which is why "descargar" appeared to do nothing. These helpers fetch the
 * bytes through the shared axios instance (which carries the auth + OAC headers
 * from `lib/api.ts`, untouched) and hand the browser a Blob to save.
 */
import api from "@/lib/api";

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

/**
 * Fetch a document's bytes (authenticated) and trigger a browser download.
 * Falls back to the server's `content-disposition` filename when none is given.
 */
export async function downloadDocument(
  documentId: number,
  filename?: string | null,
): Promise<void> {
  const res = await api.get<Blob>(`/documents/${documentId}/content`, {
    responseType: "blob",
  });
  const disposition = res.headers?.["content-disposition"] as string | undefined;
  const name = filename || filenameFromDisposition(disposition) || `documento-${documentId}`;
  saveBlob(res.data, name);
}

/**
 * Open a document's bytes in a new tab for preview. Same auth path as
 * `downloadDocument`; the object URL is revoked after a grace period so the
 * new tab has time to render.
 */
export async function previewDocument(documentId: number): Promise<void> {
  const res = await api.get<Blob>(`/documents/${documentId}/content`, {
    responseType: "blob",
  });
  const url = URL.createObjectURL(res.data);
  window.open(url, "_blank", "noopener,noreferrer");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
