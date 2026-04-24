import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { repos, type GitHubRepo } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

export default function Dashboard() {
  const qc = useQueryClient();
  const logout = useAuthStore((s) => s.logout);

  const githubRepos = useQuery({
    queryKey: ["github-repos"],
    queryFn: repos.listGitHub,
  });

  const connected = useQuery({
    queryKey: ["connected-repos"],
    queryFn: repos.listConnected,
  });

  const connect = useMutation({
    mutationFn: (github_repo_id: number) => repos.connect(github_repo_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connected-repos"] }),
  });

  const connectedIds = new Set(connected.data?.map((r) => r.github_repo_id) ?? []);

  return (
    <div className="min-h-screen max-w-5xl mx-auto px-4 py-8">
      <header className="flex items-center justify-between mb-8">
        <h1 className="text-2xl font-semibold">Your repos</h1>
        <button
          onClick={logout}
          className="text-sm text-slate-400 hover:text-slate-200"
        >
          Sign out
        </button>
      </header>

      {githubRepos.isLoading && (
        <div className="text-slate-400">Loading repos…</div>
      )}
      {githubRepos.isError && (
        <div className="text-red-400">
          Couldn't reach GitHub. Try signing in again.
        </div>
      )}

      <ul className="space-y-3">
        {githubRepos.data?.map((r: GitHubRepo) => {
          const isConnected = connectedIds.has(r.github_repo_id);
          return (
            <li
              key={r.github_repo_id}
              className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-start justify-between gap-4"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <a
                    href={r.html_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-slate-100 hover:underline truncate"
                  >
                    {r.full_name}
                  </a>
                  {r.private && (
                    <span className="text-[10px] uppercase tracking-wide bg-slate-800 px-1.5 py-0.5 rounded">
                      private
                    </span>
                  )}
                  {r.language && (
                    <span className="text-xs text-slate-500">{r.language}</span>
                  )}
                </div>
                {r.description && (
                  <p className="text-sm text-slate-400 mt-1 truncate">
                    {r.description}
                  </p>
                )}
              </div>

              {isConnected ? (
                <span className="shrink-0 text-sm text-emerald-400">Connected</span>
              ) : (
                <button
                  onClick={() => connect.mutate(r.github_repo_id)}
                  disabled={connect.isPending}
                  className="shrink-0 text-sm bg-emerald-500 text-slate-950 font-medium px-3 py-1.5 rounded-lg hover:bg-emerald-400 disabled:opacity-50"
                >
                  {connect.isPending ? "Connecting…" : "Connect"}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
