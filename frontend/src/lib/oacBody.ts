/**
 * CloudFront OAC → Lambda Function URL body signing.
 *
 * OAC does NOT sign request bodies: the client must send the SHA-256 of the EXACT bytes in
 * `x-amz-content-sha256`. The literal `UNSIGNED-PAYLOAD` is REJECTED by Lambda Function URLs
 * ("The request signature we calculated does not match…", 403 — found on dev 2026-10-17: every
 * file upload failed). So multipart/binary bodies are serialised HERE, hashed, and sent as those
 * same bytes with the boundary we read back from the serialisation.
 *
 * Shared by `lib/api.ts` (authenticated) and `api/share.ts` (public, no auth headers).
 */

export async function sha256Hex(input: string | ArrayBuffer): Promise<string> {
  const bytes = typeof input === "string" ? new TextEncoder().encode(input) : new Uint8Array(input);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export interface PreparedBody {
  /** What must be sent as the request body (exactly the hashed bytes). */
  data: string | ArrayBuffer | undefined;
  /** Content-Type to force (multipart boundary / blob type); undefined = leave as is. */
  contentType?: string;
  /** Hex SHA-256 of `data` (of "" when there is no body). */
  hash: string;
}

function isFormData(data: unknown): data is FormData {
  return typeof FormData !== "undefined" && data instanceof FormData;
}

function isBlob(data: unknown): data is Blob {
  return typeof Blob !== "undefined" && data instanceof Blob;
}

export async function prepareOacBody(data: unknown): Promise<PreparedBody> {
  if (data === undefined || data === null) {
    return { data: undefined, hash: await sha256Hex("") };
  }
  if (isFormData(data)) {
    // The browser's own multipart serialisation: bytes + the boundary it chose.
    const response = new Response(data);
    const contentType = response.headers.get("content-type") ?? undefined;
    const buffer = await response.arrayBuffer();
    return { data: buffer, contentType, hash: await sha256Hex(buffer) };
  }
  if (isBlob(data)) {
    const buffer = await data.arrayBuffer();
    return { data: buffer, contentType: data.type || undefined, hash: await sha256Hex(buffer) };
  }
  if (data instanceof ArrayBuffer) {
    return { data, hash: await sha256Hex(data) };
  }
  if (ArrayBuffer.isView(data)) {
    const view = data as ArrayBufferView;
    const buffer = view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength) as ArrayBuffer;
    return { data: buffer, hash: await sha256Hex(buffer) };
  }
  const body = typeof data === "string" ? data : JSON.stringify(data);
  return { data: body, hash: await sha256Hex(body) };
}
