import { create } from "zustand";

/**
 * Access token lives in memory only — XSS-safe vs localStorage.
 * On full reload, user goes back through GitHub OAuth (cheap: GitHub remembers the grant).
 * If you want true persistence, add a refresh-cookie flow on the backend.
 */
interface AuthState {
  accessToken: string | null;
  setToken: (token: string | null) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: null,
  setToken: (token) => set({ accessToken: token }),
  logout: () => set({ accessToken: null }),
}));
