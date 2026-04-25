import axios, { AxiosError } from "axios";
import { useAuthStore } from "@/stores/auth";

const baseURL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

export const api = axios.create({
  baseURL,
  withCredentials: true,
});

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      useAuthStore.getState().logout();
      // Redirect handled by route guard on next render
    }
    return Promise.reject(error);
  }
);

// --- Typed endpoints ---

export interface GitHubRepo {
  github_repo_id: number;
  full_name: string;
  default_branch: string;
  private: boolean;
  html_url: string;
  clone_url: string;
  description: string | null;
  language: string | null;
  pushed_at: string | null;
}

export interface ConnectedRepo {
  id: string;
  github_repo_id: number;
  full_name: string;
  default_branch: string;
  private: boolean;
  html_url: string;
  connected_at: string;
}

export const repos = {
  listGitHub: () => api.get<GitHubRepo[]>("/repos/github").then((r) => r.data),
  listConnected: () => api.get<ConnectedRepo[]>("/repos").then((r) => r.data),
  connect: (github_repo_id: number) =>
    api.post<ConnectedRepo>("/repos", { github_repo_id }).then((r) => r.data),
  disconnect: (id: string) => api.delete(`/repos/${id}`),
};

export interface AuthUser {
  id: string;
  email: string;
  github_login: string | null;
  avatar_url: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: AuthUser;
}

export const auth = {
  // GitHub OAuth — full-page redirect, server hands JWT back via URL fragment.
  githubLoginUrl: () => `${baseURL}/auth/github/login`,
  // Email/password — JSON in, JWT out. Caller stores the token in zustand.
  signup: (email: string, password: string) =>
    api.post<TokenResponse>("/auth/signup", { email, password }).then((r) => r.data),
  login: (email: string, password: string) =>
    api.post<TokenResponse>("/auth/login", { email, password }).then((r) => r.data),
  // Protected — used by RequireAuth on app load to validate the stored token.
  me: () => api.get<AuthUser>("/auth/me").then((r) => r.data),
  logout: () => api.post("/auth/logout"),
};
