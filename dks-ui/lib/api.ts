import type {
  Project, Source, StructureProposal, InboxItem,
  ArticleCandidate, Article, ArticleListItem,
  StructuralBlock, GraphPayload, AskResponse,
  ArticleSidebarGroup,
  ConversationListItem, ConversationMessage,
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

async function streamRequest<T>(
  path: string,
  init: RequestInit,
  onEvent?: (event: string, data: Record<string, unknown>) => void
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init.headers },
    ...init,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`${res.status} ${err}`);
  }
  if (!res.body) {
    throw new Error("Streaming response body is empty.");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalPayload: T | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const lines = frame.split("\n");
      const event = lines.find((line) => line.startsWith("event: "))?.slice(7);
      const data = lines
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice(6))
        .join("\n");
      if (!event || !data) continue;
      const payload = JSON.parse(data) as Record<string, unknown>;
      onEvent?.(event, payload);
      if (event === "answer") {
        finalPayload = payload as T;
      }
      if (event === "error") {
        throw new Error(String(payload.detail ?? "Streaming request failed."));
      }
    }
  }

  if (!finalPayload) {
    throw new Error("Streaming request finished without an answer event.");
  }
  return finalPayload;
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

  inbox: {
    list: (projectId: string) =>
      request<InboxItem[]>(`/projects/${projectId}/inbox`),
  },

  structuralBlocks: {
    list: (projectId: string) =>
      request<StructuralBlock[]>(`/projects/${projectId}/structural-blocks`),
    create: (
      projectId: string,
      body: { name: string; description?: string; parent_id?: string | null; position_index?: number }
    ) =>
      request<StructuralBlock>(`/projects/${projectId}/structural-blocks`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (
      projectId: string,
      blockId: string,
      body: { name?: string; description?: string; parent_id?: string | null; position_index?: number }
    ) =>
      request<StructuralBlock>(`/projects/${projectId}/structural-blocks/${blockId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    remove: (projectId: string, blockId: string) =>
      request<void>(`/projects/${projectId}/structural-blocks/${blockId}`, {
        method: "DELETE",
      }),
  },

  articles: {
    build: (projectId: string, proposalId: string) =>
      request<{ article_ids: string[]; count: number }>(
        `/projects/${projectId}/articles/build`,
        { method: "POST", body: JSON.stringify({ proposal_id: proposalId }) }
      ),
    deleteAll: (projectId: string) =>
      request<{ deleted_count: number }>(`/projects/${projectId}/articles`, {
        method: "DELETE",
      }),
    list: (projectId: string) =>
      request<ArticleListItem[]>(`/projects/${projectId}/articles`),
    sidebar: (projectId: string) =>
      request<ArticleSidebarGroup[]>(`/projects/${projectId}/articles/sidebar`),
    get: (projectId: string, articleId: string) =>
      request<Article>(`/projects/${projectId}/articles/${articleId}`),
    updateAliases: (projectId: string, articleId: string, aliases: string[]) =>
      request<{ article_id: string; aliases: string[] }>(
        `/projects/${projectId}/articles/${articleId}/aliases`,
        { method: "PUT", body: JSON.stringify({ aliases }) }
      ),
  },

  graph: {
    get: (projectId: string) =>
      request<GraphPayload>(`/projects/${projectId}/graph`),
  },

  qa: {
    ask: (
      projectId: string,
      body: {
        question: string;
        conversation_id?: string;
        top_k?: number;
        max_per_article?: number;
        min_score?: number;
      }
    ) =>
      request<AskResponse>(`/projects/${projectId}/ask`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    askStream: (
      projectId: string,
      body: {
        question: string;
        conversation_id?: string;
        top_k?: number;
        max_per_article?: number;
        min_score?: number;
      },
      onEvent?: (event: string, data: Record<string, unknown>) => void
    ) =>
      streamRequest<AskResponse>(
        `/projects/${projectId}/ask/stream`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
        onEvent
      ),
    listConversations: (projectId: string) =>
      request<ConversationListItem[]>(`/projects/${projectId}/conversations`),
    listMessages: (projectId: string, conversationId: string) =>
      request<ConversationMessage[]>(
        `/projects/${projectId}/conversations/${conversationId}/messages`
      ),
    deleteConversation: (projectId: string, conversationId: string) =>
      request<void>(`/projects/${projectId}/conversations/${conversationId}`, {
        method: "DELETE",
      }),
  },
};
