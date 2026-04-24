import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/auth";

/**
 * Backend redirected here with #access_token=... in the URL fragment.
 * Read it, store in memory, scrub the URL, then send the user to the dashboard.
 */
export default function AuthCallback() {
  const navigate = useNavigate();
  const setToken = useAuthStore((s) => s.setToken);

  useEffect(() => {
    const hash = window.location.hash.replace(/^#/, "");
    const params = new URLSearchParams(hash);
    const token = params.get("access_token");

    if (token) {
      setToken(token);
      window.history.replaceState({}, "", "/");
      navigate("/", { replace: true });
    } else {
      navigate("/login?error=missing_token", { replace: true });
    }
  }, [navigate, setToken]);

  return (
    <div className="min-h-screen flex items-center justify-center text-slate-400">
      Signing you in…
    </div>
  );
}
