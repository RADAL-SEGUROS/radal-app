import axios, {
  AxiosError,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

export const API_URL =
  import.meta.env.VITE_API_URL || "http://localhost:8000/api/v1";

export const TOKEN_KEY = "radal.access_token";
export const REFRESH_KEY = "radal.refresh_token";

export function getAccessToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export function setTokens(access: string, refresh?: string) {
  localStorage.setItem(TOKEN_KEY, access);
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export const api = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
});

// Attach JWT bearer to every request.
// SHA-256 (hex) of a string via Web Crypto (available on HTTPS + localhost).
async function sha256Hex(input: string): Promise<string> {
  const bytes = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// Attach JWT bearer + the x-amz-content-sha256 body hash to every request.
// CloudFront OAC does NOT sign POST/PUT bodies to a Lambda function URL, so the
// client must send the body's SHA-256 in x-amz-content-sha256 for the OAC SigV4
// signature to validate at the origin. Harmless for local (direct-to-backend) dev.
api.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  const token = getAccessToken();
  if (token) {
    // Authorization for local/direct dev; X-Radal-Token for the CloudFront path
    // (OAC overwrites Authorization with its SigV4 signature).
    config.headers.set("Authorization", `Bearer ${token}`);
    config.headers.set("X-Radal-Token", token);
  }
  // Binary/multipart bodies (file uploads) are serialised by the browser with a
  // boundary we do not control, so their exact bytes cannot be hashed here.
  // SigV4's documented escape hatch is the literal "UNSIGNED-PAYLOAD".
  const data = config.data;
  const isOpaqueBody =
    typeof FormData !== "undefined" && data instanceof FormData
      ? true
      : (typeof Blob !== "undefined" && data instanceof Blob) ||
        data instanceof ArrayBuffer ||
        ArrayBuffer.isView(data as ArrayBufferView);

  if (isOpaqueBody) {
    config.headers.set("x-amz-content-sha256", "UNSIGNED-PAYLOAD");
  } else if (crypto?.subtle) {
    let body = "";
    if (data !== undefined && data !== null) {
      body = typeof data === "string" ? data : JSON.stringify(data);
      // ensure the bytes we hash are exactly what gets sent
      config.data = body;
    }
    config.headers.set("x-amz-content-sha256", await sha256Hex(body));
  }
  return config;
});

// 401 -> attempt refresh once, then replay the original request.
let isRefreshing = false;
let pendingQueue: Array<(token: string | null) => void> = [];

function flushQueue(token: string | null) {
  pendingQueue.forEach((cb) => cb(token));
  pendingQueue = [];
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as
      | (AxiosRequestConfig & { _retry?: boolean })
      | undefined;

    if (
      error.response?.status !== 401 ||
      !original ||
      original._retry ||
      original.url?.includes("/auth/refresh") ||
      original.url?.includes("/auth/login")
    ) {
      return Promise.reject(error);
    }

    original._retry = true;

    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        pendingQueue.push((token) => {
          if (!token) return reject(error);
          original.headers = original.headers ?? {};
          (original.headers as Record<string, string>)["Authorization"] =
            `Bearer ${token}`;
          resolve(api(original));
        });
      });
    }

    isRefreshing = true;
    const refreshToken = getRefreshToken();

    if (!refreshToken) {
      isRefreshing = false;
      clearTokens();
      flushQueue(null);
      redirectToLogin();
      return Promise.reject(error);
    }

    try {
      const { data } = await axios.post(`${API_URL}/auth/refresh`, {
        refresh_token: refreshToken,
      });
      setTokens(data.access_token, data.refresh_token);
      isRefreshing = false;
      flushQueue(data.access_token);

      original.headers = original.headers ?? {};
      (original.headers as Record<string, string>)["Authorization"] =
        `Bearer ${data.access_token}`;
      return api(original);
    } catch (refreshError) {
      isRefreshing = false;
      clearTokens();
      flushQueue(null);
      redirectToLogin();
      return Promise.reject(refreshError);
    }
  },
);

function redirectToLogin() {
  if (window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
}

export default api;
