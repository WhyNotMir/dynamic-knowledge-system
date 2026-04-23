"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowRight, ChevronDown, ChevronRight, MessageCircle, Quote, Send, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { AskCitation, AskResponse, ConversationMessage } from "@/lib/types";

const EXAMPLES = [
  "What does the knowledge base say about attention?",
  "Which hardware is mentioned for training?",
  "Summarize the main transformer architecture points.",
];

type ChatTurn = {
  question: string;
  answer: AskResponse;
};

function answerFromAssistantMessage(
  conversationId: string,
  assistant: ConversationMessage,
  user?: ConversationMessage
): AskResponse {
  const meta = assistant.meta_json ?? {};
  return {
    conversation_id: conversationId,
    user_message_id: user?.id ?? "",
    assistant_message_id: assistant.id,
    answer: assistant.content,
    citations: Array.isArray(meta.citations)
      ? (meta.citations as AskCitation[])
      : [],
    confidence: typeof meta.confidence === "number" ? meta.confidence : 0,
    insufficient_context:
      typeof meta.insufficient_context === "boolean"
        ? meta.insufficient_context
        : false,
  };
}

export default function AskPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const qc = useQueryClient();
  const [question, setQuestion] = useState("");
  const [lastQuestion, setLastQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [expandedCitations, setExpandedCitations] = useState<Set<string>>(new Set());
  const [streamingStatus, setStreamingStatus] = useState<string | null>(null);
  const [showAllConversations, setShowAllConversations] = useState(false);

  const conversations = useQuery({
    queryKey: ["conversations", projectId],
    queryFn: () => api.qa.listConversations(projectId),
  });
  const messages = useQuery({
    queryKey: ["conversation-messages", projectId, conversationId],
    queryFn: () => api.qa.listMessages(projectId, conversationId as string),
    enabled: Boolean(conversationId),
  });
  const sortedConversations = useMemo(() => {
    return [...(conversations.data ?? [])].sort((left, right) => {
      const updatedDelta =
        new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime();
      if (updatedDelta !== 0) return updatedDelta;
      return new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
    });
  }, [conversations.data]);

  const chatTurns = useMemo<ChatTurn[]>(() => {
    const turns: ChatTurn[] = [];
    if (messages.data?.length && conversationId) {
      let lastUser: ConversationMessage | undefined;
      for (const message of messages.data) {
        if (message.role === "user") {
          lastUser = message;
          continue;
        }
        if (message.role !== "assistant") continue;
        turns.push({
          question: lastUser?.content ?? "",
          answer: answerFromAssistantMessage(conversationId, message, lastUser),
        });
      }
    }

    if (
      answer &&
      !turns.some((turn) => turn.answer.assistant_message_id === answer.assistant_message_id)
    ) {
      turns.push({ question: lastQuestion, answer });
    }

    return turns.reverse();
  }, [answer, conversationId, lastQuestion, messages.data]);

  const ask = useMutation({
    mutationFn: (value: string) =>
      api.qa.askStream(
        projectId,
        {
          question: value,
          conversation_id: conversationId ?? undefined,
        },
        (event, data) => {
          if (event === "status") {
            setStreamingStatus(String(data.message ?? "Working..."));
          }
        }
      ),
    onSuccess: (response, value) => {
      setAnswer(response);
      setConversationId(response.conversation_id);
      setLastQuestion(value);
      setQuestion("");
      setStreamingStatus(null);
      qc.invalidateQueries({ queryKey: ["conversations", projectId] });
      qc.invalidateQueries({
        queryKey: ["conversation-messages", projectId, response.conversation_id],
      });
    },
    onError: () => {
      setStreamingStatus(null);
    },
  });

  const deleteConversation = useMutation({
    mutationFn: (id: string) => api.qa.deleteConversation(projectId, id),
    onSuccess: (_response, deletedId) => {
      if (conversationId === deletedId) {
        setConversationId(null);
        setAnswer(null);
        setLastQuestion("");
        setQuestion("");
        setExpandedCitations(new Set());
      }
      qc.invalidateQueries({ queryKey: ["conversations", projectId] });
      qc.removeQueries({ queryKey: ["conversation-messages", projectId, deletedId] });
    },
  });

  const visibleConversations = showAllConversations
    ? sortedConversations
    : sortedConversations.slice(0, 5);
  const isFreshChat = !conversationId && !answer && !lastQuestion;

  function submitQuestion() {
    const value = question.trim();
    if (!value || ask.isPending) return;
    setStreamingStatus("Starting...");
    ask.mutate(value);
  }

  function toggleCitations(id: string) {
    setExpandedCitations((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function startNewChat() {
    setConversationId(null);
    setAnswer(null);
    setLastQuestion("");
    setQuestion("");
    setExpandedCitations(new Set());
    setStreamingStatus(null);
  }

  return (
    <div className="min-h-full bg-vault-bg px-8 py-10">
      <div className="mx-auto grid max-w-6xl gap-8 lg:grid-cols-[minmax(0,1fr)_340px]">
        <main className="space-y-8">
          <section className="space-y-4">
            <p className="text-xs font-mono uppercase tracking-[0.28em] text-vault-gold">
              Strict RAG Q&A
            </p>
            <h1 className="text-display text-4xl font-semibold leading-tight text-vault-text">
              Ask the knowledge base
            </h1>
            <p className="max-w-2xl text-sm leading-7 text-vault-muted">
              Answers are grounded in retrieved article blocks. If the base
              does not contain enough evidence, the answer is marked as
              insufficient context.
            </p>
          </section>

          <section className="rounded-2xl border border-vault-border bg-vault-surface p-4 shadow-[0_18px_60px_rgba(0,0,0,0.18)]">
            <Textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault();
                  submitQuestion();
                }
              }}
              placeholder="Ask a question about this project..."
              className="min-h-32 resize-none border-vault-border bg-vault-bg text-vault-text placeholder:text-vault-muted"
            />
            <div className="mt-3 flex items-center justify-between gap-3">
              <p className="text-xs text-vault-muted">
                Press Cmd/Ctrl + Enter to ask.
              </p>
              <Button
                onClick={submitQuestion}
                disabled={!question.trim() || ask.isPending}
                className="bg-vault-gold text-vault-bg hover:bg-vault-gold/90"
              >
                {ask.isPending ? streamingStatus ?? "Searching..." : "Ask"}
                <Send size={14} className="ml-2" />
              </Button>
            </div>
          </section>

          {ask.isError && (
            <div className="rounded-xl border border-vault-error/30 bg-vault-error/10 px-4 py-3 text-sm text-vault-error">
              {ask.error instanceof Error ? ask.error.message : "Ask request failed."}
            </div>
          )}

          {chatTurns.length > 0 ? (
            <section className="space-y-8">
              {chatTurns.map((turn) => {
                const citationsOpen = expandedCitations.has(turn.answer.assistant_message_id);
                return (
                <article key={turn.answer.assistant_message_id} className="space-y-3">
                  <div className="rounded-2xl border border-vault-border bg-vault-surface p-6">
                    <div className="mb-4 flex items-start justify-between gap-4">
                      <div>
                        <p className="mb-2 text-xs font-mono uppercase tracking-[0.22em] text-vault-gold/70">
                          Question
                        </p>
                        <h2 className="text-xl font-semibold text-vault-text">
                          {turn.question}
                        </h2>
                      </div>
                      <span
                        className={cn(
                          "rounded-full border px-3 py-1 text-xs font-mono uppercase tracking-[0.18em]",
                          turn.answer.insufficient_context
                            ? "border-vault-error/30 bg-vault-error/10 text-vault-error"
                            : "border-vault-success/30 bg-vault-success/10 text-vault-success"
                        )}
                      >
                        {turn.answer.insufficient_context ? "insufficient" : "grounded"}
                      </span>
                    </div>
                    <p className="text-[15px] leading-8 text-vault-text">
                      {turn.answer.answer}
                    </p>
                    <p className="mt-4 text-xs font-mono text-vault-muted">
                      confidence {turn.answer.confidence.toFixed(2)} · saved to conversation
                    </p>
                  </div>

                  <div className="rounded-2xl border border-vault-border bg-vault-surface/60">
                    <button
                      type="button"
                      onClick={() => toggleCitations(turn.answer.assistant_message_id)}
                      className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
                    >
                      <span className="text-xs font-mono uppercase tracking-[0.22em] text-vault-gold">
                        {turn.answer.citations.length} citations
                      </span>
                      <span className="flex items-center gap-2 text-xs text-vault-muted">
                        {citationsOpen ? "Hide evidence" : "Show evidence"}
                        {citationsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      </span>
                    </button>
                    <AnimatePresence initial={false}>
                      {citationsOpen && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.18, ease: "easeOut" }}
                          className="overflow-hidden"
                        >
                          <div className="grid gap-3 border-t border-vault-border px-4 py-4">
                            {turn.answer.citations.length > 0 ? (
                              turn.answer.citations.map((citation) => (
                                <Link
                                  key={`${turn.answer.assistant_message_id}-${citation.block_id}`}
                                  href={`/projects/${projectId}/articles/${citation.article_id}#block-${citation.block_id}`}
                                  className="group rounded-xl border border-vault-border bg-vault-bg p-4 transition-colors hover:border-vault-gold/40"
                                >
                                  <div className="mb-2 flex items-center justify-between gap-3">
                                    <p className="font-medium text-vault-text group-hover:text-vault-gold">
                                      {citation.article_title}
                                    </p>
                                    <ArrowRight size={15} className="text-vault-muted group-hover:text-vault-gold" />
                                  </div>
                                  <div className="mb-3 flex flex-wrap items-center gap-2 text-xs font-mono text-vault-muted">
                                    {citation.page_number != null && <span>p.{citation.page_number}</span>}
                                    {citation.section_path && <span>{citation.section_path}</span>}
                                    <span>score {citation.score.toFixed(2)}</span>
                                  </div>
                                  <p className="line-clamp-3 text-sm leading-6 text-vault-muted">
                                    <Quote size={13} className="mr-1 inline text-vault-gold/70" />
                                    {citation.content}
                                  </p>
                                </Link>
                              ))
                            ) : (
                              <div className="rounded-xl border border-vault-border bg-vault-bg px-4 py-6 text-sm text-vault-muted">
                                No citations were found for this question.
                              </div>
                            )}
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                </article>
                );
              })}
            </section>
          ) : isFreshChat ? (
            <section className="rounded-2xl border border-dashed border-vault-border bg-vault-surface/40 p-8 text-center">
              <MessageCircle className="mx-auto mb-3 text-vault-gold/70" size={24} />
              <p className="text-vault-text">You are starting a fresh conversation.</p>
              <p className="mt-2 text-sm text-vault-muted">
                Ask a new question below, or reopen any recent chat from the sidebar.
              </p>
            </section>
          ) : (
            <section className="rounded-2xl border border-dashed border-vault-border bg-vault-surface/40 p-8 text-center">
              <MessageCircle className="mx-auto mb-3 text-vault-gold/70" size={24} />
              <p className="text-vault-text">Ask a question to retrieve evidence from your articles.</p>
              <p className="mt-2 text-sm text-vault-muted">
                The first version is retrieval-grounded and citation-first.
              </p>
            </section>
          )}
        </main>

        <aside className="space-y-4">
          <div className="rounded-2xl border border-vault-border bg-vault-surface p-5">
            <p className="mb-4 text-xs font-mono uppercase tracking-[0.22em] text-vault-gold/70">
              Try asking
            </p>
            <div className="space-y-2">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  onClick={() => setQuestion(example)}
                  className="w-full rounded-lg border border-vault-border bg-vault-bg px-3 py-2 text-left text-sm leading-6 text-vault-muted transition-colors hover:border-vault-gold/40 hover:text-vault-text"
                >
                  {example}
                </button>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-vault-border bg-vault-surface p-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <p className="text-xs font-mono uppercase tracking-[0.22em] text-vault-gold/70">
                Conversations
              </p>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={startNewChat}
                  disabled={ask.isPending}
                  className="text-xs font-mono text-vault-gold transition-colors hover:text-vault-text disabled:opacity-40"
                >
                  New chat
                </button>
                {(sortedConversations.length ?? 0) > 5 && (
                  <button
                    type="button"
                    onClick={() => setShowAllConversations((value) => !value)}
                    className="text-xs font-mono text-vault-muted transition-colors hover:text-vault-gold"
                  >
                    {showAllConversations ? "Show recent" : "View all"}
                  </button>
                )}
              </div>
            </div>
            {sortedConversations.length ? (
              <div className={cn("space-y-3", showAllConversations && "max-h-[520px] overflow-y-auto pr-1")}>
                {visibleConversations.map((conversation) => (
                  <div
                    key={conversation.id}
                    className={cn(
                      "flex items-start gap-2 rounded-lg border px-3 py-2 transition-colors hover:border-vault-gold/40",
                      conversation.id === conversationId
                        ? "border-vault-gold/40 bg-vault-gold/10"
                        : "border-vault-border bg-vault-bg"
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        setConversationId(conversation.id);
                        setAnswer(null);
                        setLastQuestion("");
                        setQuestion("");
                        setExpandedCitations(new Set());
                      }}
                      className="min-w-0 flex-1 text-left"
                    >
                      <p className="line-clamp-2 text-sm text-vault-text">
                        {conversation.title ?? "Untitled conversation"}
                      </p>
                      <p className="mt-1 text-xs font-mono text-vault-muted">
                        {conversation.message_count} messages
                      </p>
                    </button>
                    <button
                      type="button"
                      onClick={() => deleteConversation.mutate(conversation.id)}
                      disabled={deleteConversation.isPending}
                      className="rounded-md p-1 text-vault-muted transition-colors hover:bg-vault-error/10 hover:text-vault-error disabled:opacity-40"
                      aria-label="Delete conversation"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm leading-6 text-vault-muted">
                Ask your first question to start a saved conversation.
              </p>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
