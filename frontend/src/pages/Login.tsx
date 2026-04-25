import { FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { AxiosError } from "axios";
import { auth } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

/**
 * Login page — supports both email/password and GitHub OAuth.
 *
 * GitHub button is a full-page navigation; the API redirects back to
 * /auth/callback with the JWT in the URL fragment.
 *
 * Email/password is a normal POST that returns the JWT in the JSON body.
 * We push it into the in-memory zustand store and route to /.
 */
export default function Login() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const setToken = useAuthStore((s) => s.setToken);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const oauthError = params.get("error");

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      const res = await auth.login(email, password);
      setToken(res.access_token);
      navigate("/", { replace: true });
    } catch (err) {
      const ax = err as AxiosError<{ detail?: string }>;
      setFormError(ax.response?.data?.detail ?? "Sign-in failed. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-xl">
        <h1 className="text-2xl font-semibold mb-2">AI DevOps</h1>
        <p className="text-slate-400 mb-6">
          Sign in to connect a repo, generate a Dockerfile, and deploy.
        </p>

        {oauthError && (
          <div className="mb-4 p-3 rounded-lg bg-red-950 border border-red-900 text-sm">
            GitHub sign-in failed: {oauthError}
          </div>
        )}
        {formError && (
          <div className="mb-4 p-3 rounded-lg bg-red-950 border border-red-900 text-sm">
            {formError}
          </div>
        )}

        <form onSubmit={onSubmit} className="space-y-3">
          <input
            type="email"
            required
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          <input
            type="password"
            required
            minLength={8}
            autoComplete="current-password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 disabled:cursor-not-allowed text-white font-medium rounded-lg py-2.5 transition"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <div className="my-5 flex items-center gap-3 text-xs text-slate-500">
          <div className="h-px flex-1 bg-slate-800" />
          <span>OR</span>
          <div className="h-px flex-1 bg-slate-800" />
        </div>

        <a
          href={auth.githubLoginUrl()}
          className="w-full inline-flex items-center justify-center gap-2 bg-white text-slate-900 font-medium rounded-lg py-2.5 hover:bg-slate-100 transition"
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.55v-1.93c-3.2.69-3.87-1.54-3.87-1.54-.52-1.33-1.27-1.69-1.27-1.69-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.02 1.75 2.69 1.24 3.34.95.1-.74.4-1.24.72-1.53-2.55-.29-5.24-1.28-5.24-5.7 0-1.26.45-2.29 1.18-3.1-.12-.29-.51-1.46.11-3.04 0 0 .96-.31 3.16 1.18a10.93 10.93 0 015.75 0c2.2-1.49 3.16-1.18 3.16-1.18.62 1.58.23 2.75.11 3.04.74.81 1.18 1.84 1.18 3.1 0 4.43-2.69 5.41-5.25 5.69.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.55A11.51 11.51 0 0023.5 12C23.5 5.65 18.35.5 12 .5z" />
          </svg>
          Continue with GitHub
        </a>

        <p className="mt-6 text-sm text-slate-400 text-center">
          New here?{" "}
          <Link to="/signup" className="text-indigo-400 hover:text-indigo-300">
            Create an account
          </Link>
        </p>
      </div>
    </div>
  );
}
