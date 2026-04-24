import { useSearchParams } from "react-router-dom";
import { auth } from "@/api/client";

export default function Login() {
  const [params] = useSearchParams();
  const error = params.get("error");

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-xl">
        <h1 className="text-2xl font-semibold mb-2">AI DevOps</h1>
        <p className="text-slate-400 mb-6">
          Connect a repo. We'll analyze it, generate a Dockerfile, and deploy it for you.
        </p>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-950 border border-red-900 text-sm">
            Sign-in failed: {error}
          </div>
        )}

        <a
          href={auth.loginUrl()}
          className="w-full inline-flex items-center justify-center gap-2 bg-white text-slate-900 font-medium rounded-lg py-2.5 hover:bg-slate-100 transition"
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.55v-1.93c-3.2.69-3.87-1.54-3.87-1.54-.52-1.33-1.27-1.69-1.27-1.69-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.02 1.75 2.69 1.24 3.34.95.1-.74.4-1.24.72-1.53-2.55-.29-5.24-1.28-5.24-5.7 0-1.26.45-2.29 1.18-3.1-.12-.29-.51-1.46.11-3.04 0 0 .96-.31 3.16 1.18a10.93 10.93 0 015.75 0c2.2-1.49 3.16-1.18 3.16-1.18.62 1.58.23 2.75.11 3.04.74.81 1.18 1.84 1.18 3.1 0 4.43-2.69 5.41-5.25 5.69.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.55A11.51 11.51 0 0023.5 12C23.5 5.65 18.35.5 12 .5z" />
          </svg>
          Continue with GitHub
        </a>
      </div>
    </div>
  );
}
