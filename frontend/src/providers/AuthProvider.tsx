import * as React from "react";
import api, {
  clearTokens,
  getAccessToken,
  setTokens,
} from "@/lib/api";

export type Rol =
  | "admin_corredora"
  | "ejecutivo_corredora"
  | "inspector"
  | "admin_asegurado"
  | "ejecutivo_asegurado"
  | "admin_aseguradora"
  | "ejecutivo_aseguradora";

export interface Usuario {
  id: number;
  nombre: string;
  email: string;
  cargo: string;
  rol: Rol;
  corredora_id: number;
}

export interface CorredoraSummary {
  id: number;
  nombre: string;
  logo_url?: string | null;
}

interface AuthContextValue {
  user: Usuario | null;
  corredora: CorredoraSummary | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = React.createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<Usuario | null>(null);
  const [corredora, setCorredora] = React.useState<CorredoraSummary | null>(
    null,
  );
  const [isLoading, setIsLoading] = React.useState<boolean>(true);

  const loadMe = React.useCallback(async () => {
    const { data } = await api.get("/auth/me");
    // /auth/me returns the usuario object + a corredora summary.
    const { corredora: corr, ...usuario } = data as Usuario & {
      corredora?: CorredoraSummary;
    };
    setUser(usuario as Usuario);
    setCorredora(corr ?? null);
  }, []);

  React.useEffect(() => {
    let active = true;
    (async () => {
      if (!getAccessToken()) {
        setIsLoading(false);
        return;
      }
      try {
        await loadMe();
      } catch {
        clearTokens();
        if (active) {
          setUser(null);
          setCorredora(null);
        }
      } finally {
        if (active) setIsLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [loadMe]);

  const login = React.useCallback(
    async (email: string, password: string) => {
      const { data } = await api.post("/auth/login", { email, password });
      setTokens(data.access_token, data.refresh_token);
      if (data.usuario) {
        setUser(data.usuario as Usuario);
      }
      // Hydrate corredora summary from /auth/me.
      try {
        await loadMe();
      } catch {
        /* usuario from login is enough to proceed */
      }
    },
    [loadMe],
  );

  const logout = React.useCallback(async () => {
    const refresh = localStorage.getItem("radal.refresh_token");
    try {
      if (refresh) await api.post("/auth/logout", { refresh_token: refresh });
    } catch {
      /* best-effort */
    } finally {
      clearTokens();
      setUser(null);
      setCorredora(null);
    }
  }, []);

  const value = React.useMemo<AuthContextValue>(
    () => ({
      user,
      corredora,
      isAuthenticated: !!user,
      isLoading,
      login,
      logout,
      refreshUser: loadMe,
    }),
    [user, corredora, isLoading, login, logout, loadMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
