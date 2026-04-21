import type {
  Project, Source, StructureProposal,
  ArticleCandidate, Article, ArticleListItem,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`${res.status} ${err}`);
  }
  // 204 No Content — parsing would throw. Used by DELETE endpoints.
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── Projects ─────────────────────────────────────────────────────────────────
export const api = {
  projects: {
    list: () => request<Project[]>("/projects"),
    create: (body: { name: string; description?: string; scope_hint?: string }) =>
      request<Project>("/projects", { method: "POST", body: JSON.stringify(body) }),
    get: (id: string) => request<Project>(`/projects/${id}`),
    remove: (id: string) =>
      request<void>(`/projects/${id}`, { method: "DELETE" }),
  },

  sources: {
    list: (projectId: string) =>
      request<Source[]>(`/projects/${projectId}/sources`),
    upload: async (projectId: string, file: File): Promise<Source> => {
      const form = new FormData();
      form.append("file", file);
      // NOTE: do NOT set Content-Type — the browser must add the multipart
      // boundary. We also cannot go through `request()` because it forces
      // application/json.
      const res = await fetch(`${BASE}/projects/${projectId}/sources`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`${res.status} ${body}`);
      }
      return (await res.json()) as Source;
    },
    remove: (projectId: string, sourceId: string) =>
      request<void>(`/projects/${projectId}/sources/${sourceId}`, {
        method: "DELETE",
      }),
  },

  structure: {
    propose: (projectId: string) =>
      request<{ proposal_id: string; message: string }>(
        `/projects/${projectId}/structure/propose`,
        { method: "POST" }
      ),
    listProposals: (projectId: string) =>
      request<StructureProposal[]>(`/projects/${projectId}/structure/proposals`),
    getProposal: (projectId: string, proposalId: string) =>
      request<StructureProposal>(
        `/projects/${projectId}/structure/proposals/${proposalId}`
      ),
    updateCandidate: (
      projectId: string,
      candidateId: string,
      body: { title?: string; suggested_section?: string; status?: string }
    ) =>
      request<ArticleCandidate>(
        `/projects/${projectId}/structure/candidates/${candidateId}`,
        { method: "PATCH", body: JSON.stringify(body) }
      ),
    confirmAll: (projectId: string, proposalId: string) =>
      request<{ confirmed_count: number; total_count: number }>(
        `/projects/${projectId}/structure/proposals/${proposalId}/confirm-all`,
        { method: "POST" }
      ),
  },

  articles: {
    build: (projectId: string, proposalId: string) =>
      request<{ article_ids: string[]; count: number }>(
        `/projects/${projectId}/articles/build`,
        { method: "POST", body: JSON.stringify({ proposal_id: proposalId }) }
      ),
    list: (projectId: string) =>
      request<ArticleListItem[]>(`/projects/${projectId}/articles`),
    get: (projectId: string, articleId: string) =>
      request<Article>(`/projects/${projectId}/articles/${articleId}`),
  },
};