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

export const auth = {
  loginUrl: () => `${baseURL}/auth/github/login`,
  logout: () => api.post("/auth/logout"),
};
