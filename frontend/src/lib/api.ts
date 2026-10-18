import axios, {
  AxiosError,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";
import { prepareOacBody } from "@/lib/oacBody";

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

/**
 * Repeatable query params, the way FastAPI reads them.
 *
 * Axios's default array serialisation is `kind[]=account&kind[]=renewal`.
 * FastAPI declares these as `list[X] = Query(None)` and reads REPEATED bare
 * keys — `kind=account&kind=renewal` — so the bracketed form does not match the
 * parameter at all. It is not an error either: the param simply stays `None`
 * and the endpoint returns everything.
 *
 * That silence is the damage. The Cuentas table asked for
 * `kind=["account","renewal"]` and was served endorsements, collections and
 * claims as though they were accounts — a filter that looks applied, reads as
 * data, and is wrong. Every repeatable filter in the app (`kind`, `stage`,
 * `status`) went through the same hole.
 */
function serializeParams(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      // Repeat the bare key once per member — never `key[]`.
      for (const item of value) {
        if (item === undefined || item === null || item === "") continue;
        search.append(key, String(item));
      }
    } else {
      search.append(key, String(value));
    }
  }
  return search.toString();
}

export const api = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
  paramsSerializer: { serialize: serializeParams },
});

// Attach JWT bearer to every request.
// SHA-256 (hex) of a string via Web Crypto (available on HTTPS + localhost).

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
  if (crypto?.subtle) {
    // OAC rejects UNSIGNED-PAYLOAD: hash the exact bytes, multipart included (lib/oacBody.ts).
    const prepared = await prepareOacBody(config.data);
    config.data = prepared.data;
    if (prepared.contentType) config.headers.set("Content-Type", prepared.contentType);
    config.headers.set("x-amz-content-sha256", prepared.hash);
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
