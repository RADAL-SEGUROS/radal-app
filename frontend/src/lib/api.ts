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
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
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
