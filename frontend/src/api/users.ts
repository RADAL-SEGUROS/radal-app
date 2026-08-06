/**
 * Users — the people inside the caller's own organization (Settings > Team).
 *
 * `GET /users/roles` returns exactly the roles the current actor may grant, so
 * the invite form offers no option the server would reject.
 * Creating a user without a password returns a one-time `temporary_password`
 * (there is no outbound email in this pass — the admin passes it on).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  AssignableRole,
  OffsetPage,
  User,
  UserCreate,
  UserInviteResponse,
  UserUpdate,
} from "@/api/types";

export interface UserListParams {
  search?: string;
  role?: string;
  user_type?: string;
  is_active?: boolean;
  limit?: number;
  offset?: number;
}

export function useUsers(params: UserListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.users.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<OffsetPage<User>>("/users", { params: clean(params) });
      return data;
    },
  });
}

export function useUser(userId: number | undefined) {
  return useQuery({
    queryKey: qk.users.detail(userId ?? 0),
    enabled: !!userId,
    queryFn: async () => {
      const { data } = await api.get<User>(`/users/${userId}`);
      return data;
    },
  });
}

/** The roles this actor may actually grant — drives the invite form's options. */
export function useAssignableRoles(enabled = true) {
  return useQuery({
    queryKey: qk.users.roles,
    enabled,
    staleTime: 10 * 60 * 1000,
    queryFn: async () => {
      const { data } = await api.get<{ items: AssignableRole[] }>("/users/roles");
      return data.items;
    },
  });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: UserCreate) => {
      const { data } = await api.post<UserInviteResponse>("/users", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.users.all });
    },
  });
}

function useUserWrite<TVars>(userId: number, fn: (vars: TVars) => Promise<User>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => {
      qc.setQueryData(qk.users.detail(userId), data);
      void qc.invalidateQueries({ queryKey: qk.users.lists() });
    },
  });
}

export function useUpdateUser(userId: number) {
  return useUserWrite(userId, async (payload: UserUpdate) => {
    const { data } = await api.patch<User>(`/users/${userId}`, payload);
    return data;
  });
}

export function useUpdateUserRole(userId: number) {
  return useUserWrite(userId, async (payload: { role: string }) => {
    const { data } = await api.patch<User>(`/users/${userId}/role`, payload);
    return data;
  });
}

export function useUpdateUserStatus(userId: number) {
  return useUserWrite(userId, async (payload: { is_active: boolean }) => {
    const { data } = await api.patch<User>(`/users/${userId}/status`, payload);
    return data;
  });
}

/** Self-service password change for the signed-in user. */
export function useChangePassword() {
  return useMutation({
    mutationFn: async (payload: { current_password: string; new_password: string }) => {
      const { data } = await api.post<{ detail: string }>("/auth/change-password", payload);
      return data;
    },
  });
}
