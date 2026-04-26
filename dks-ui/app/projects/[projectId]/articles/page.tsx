"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";

export default function ArticlesIndex() {
  const { projectId } = useParams<{ projectId: string }>();
  const qc = useQueryClient();

  const { data: articles = [] } = useQuery({
    queryKey: ["articles", projectId],
    queryFn: () => api.articles.list(projectId),
  });

  const deleteAll = useMutation({
    mutationFn: () => api.articles.deleteAll(projectId),
    onSuccess: (payload) => {
      qc.invalidateQueries({ queryKey: ["articles", projectId] });
      qc.invalidateQueries({ queryKey: ["graph", projectId] });
      toast.success(
        payload.deleted_count > 0
          ? `Deleted ${payload.deleted_count} article${payload.deleted_count === 1 ? "" : "s"}`
          : "No articles to delete"
      );
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to delete articles");
    },
  });

  const handleDeleteAll = () => {
    if (articles.length === 0 || deleteAll.isPending) return;
    const confirmed = window.confirm(
      `Delete all ${articles.length} article${articles.length === 1 ? "" : "s"} in this project? This cannot be undone.`
    );
    if (!confirmed) return;
    deleteAll.mutate();
  };

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="w-full max-w-2xl rounded-3xl border border-vault-border bg-vault-surface p-8 text-center">
        <p className="mb-2 text-display text-2xl text-vault-text/30">Select an article</p>
        <p className="text-sm text-vault-muted">
          Choose from the sidebar to start reading
        </p>

        <div className="mt-8 border-t border-vault-border pt-6">
          <p className="mb-3 text-xs font-mono uppercase tracking-[0.28em] text-vault-muted">
            Project Articles
          </p>
          <p className="mb-5 text-sm text-vault-muted">
            {articles.length === 0
              ? "There are no built articles yet."
              : `${articles.length} built article${articles.length === 1 ? "" : "s"} currently in this project.`}
          </p>

          <button
            type="button"
            onClick={handleDeleteAll}
            disabled={articles.length === 0 || deleteAll.isPending}
            className="inline-flex items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-300 transition hover:bg-red-500/15 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Trash2 size={15} />
            {deleteAll.isPending ? "Deleting..." : "Delete All Articles"}
          </button>
        </div>
      </div>
    </div>
  );
}
