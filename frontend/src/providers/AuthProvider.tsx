import * as React from "react";
import api, { clearTokens, getAccessToken, getRefreshToken, setTokens } from "@/lib/api";
import { queryClient } from "@/providers/QueryProvider";
import type { MeResponse, Organization, User } from "@/api/types";

/**
 * Session state for the whole SPA.
 *
 * Wiring (v2):
 *   POST /auth/login   -> access + refresh tokens, the user AND its organization
 *                         in ONE call, so the app boots without a second round trip.
 *   GET  /auth/me      -> re-hydrates the profile on reload / after a refresh.
 *   GET  /auth/permissions -> NOT here. It is a react-query resource in
 *                         `@/lib/permissions` (`usePermissions`), because it is
 *                         cached, invalidatable data rather than session state.
 *
 * The tenant is NEVER read from here for scoping: `user.broker_id` is display
 * data only. Every query is scoped server-side from the authenticated user.
 */

export type { Organization, User } from "@/api/types";

interface AuthContextValue {
  user: User | null;
  /** The user's home organization — for a broker user, their brokerage. */
  organization: Organization | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = React.createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<User | null>(null);
  const [organization, setOrganization] = React.useState<Organization | null>(null);
  const [isLoading, setIsLoading] = React.useState<boolean>(true);

  const loadMe = React.useCallback(async () => {
    const { data } = await api.get<MeResponse>("/auth/me");
    setUser(data.user);
    setOrganization(data.organization ?? null);
  }, []);

  React.useEffect(() => {
    let active = true;
    (async () => {
      if (!getAccessToken() && !getRefreshToken()) {
        setIsLoading(false);
        return;
      }
      try {
        await loadMe();
      } catch {
        // The refresh interceptor already had its chance; the session is dead.
        clearTokens();
        if (active) {
          setUser(null);
          setOrganization(null);
        }
      } finally {
        if (active) setIsLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [loadMe]);

  const login = React.useCallback(async (email: string, password: string) => {
    const { data } = await api.post("/auth/login", { email, password });
    setTokens(data.access_token, data.refresh_token);
    setUser(data.user as User);
    setOrganization((data.organization as Organization | null) ?? null);
  }, []);

  // There is no /auth/logout endpoint: tokens are stateless, so signing out is
  // dropping them. The query cache is cleared too, so the next user in this tab
  // can never be served the previous tenant's rows from memory.
  const logout = React.useCallback(async () => {
    clearTokens();
    setUser(null);
    setOrganization(null);
    queryClient.clear();
  }, []);

  const value = React.useMemo<AuthContextValue>(
    () => ({
      user,
      organization,
      isAuthenticated: !!user,
      isLoading,
      login,
      logout,
      refreshUser: loadMe,
    }),
    [user, organization, isLoading, login, logout, loadMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
