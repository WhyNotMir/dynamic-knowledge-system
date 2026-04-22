"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  Layers,
  Play,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { ArticleCandidate, InboxItem } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";


function InboxGatePanel({ item }: { item: InboxItem }) {
  return (
    <div className="mb-6 rounded-lg border border-vault-gold/20 bg-vault-gold-05 p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[11px] font-mono uppercase tracking-[0.22em] text-vault-gold">
            Human Review Gate
          </p>
          <p className="mt-2 text-sm text-vault-text">
            {item.gate.reason}
          </p>
        </div>
        <span className="shrink-0 rounded-full border border-vault-gold/25 px-2 py-1 text-[11px] font-mono text-vault-gold">
          Manual
        </span>
      </div>

      <div className="mt-4">
        <p className="text-[11px] font-mono uppercase tracking-[0.18em] text-vault-muted mb-2">
          Available now
        </p>
        <div className="flex flex-wrap gap-1.5">
          {item.gate.available_actions.map((action) => (
            <span
              key={action.action}
              className="rounded-full border border-vault-border bg-vault-bg px-2.5 py-1 text-[11px] font-mono text-vault-muted"
            >
              {action.label}
            </span>
          ))}
        </div>
      </div>

      {item.gate.reserved_actions.length > 0 && (
        <div className="mt-4">
          <p className="text-[11px] font-mono uppercase tracking-[0.18em] text-vault-muted mb-2">
            Reserved destructive actions
          </p>
          <div className="flex flex-wrap gap-1.5">
            {item.gate.reserved_actions.map((action) => (
              <span
                key={action.action}
                className="rounded-full border border-vault-error/20 bg-vault-surface px-2.5 py-1 text-[11px] font-mono text-vault-muted"
              >
                {action.label}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


function CandidateCard({
  candidate, projectId,
}: {
  candidate: ArticleCandidate; projectId: string;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(candidate.title);

  const update = useMutation({
    mutationFn: (body: Parameters<typeof api.structure.updateCandidate>[2]) =>
      api.structure.updateCandidate(projectId, candidate.id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inbox", projectId] });
      qc.invalidateQueries({ queryKey: ["proposals", projectId] });
      setEditing(false);
    },
  });

  const statusColors = {
    proposed: "text-vault-muted border-vault-border",
    confirmed: "text-vault-success border-vault-success/30 bg-vault-success/5",
    rejected: "text-vault-error border-vault-error/30 bg-vault-error/5",
    merged: "text-vault-warning border-vault-warning/30",
  };

  return (
    <div
      className={cn(
        "p-4 border rounded-lg transition-all",
        candidate.status === "confirmed"
          ? "border-vault-gold/30 bg-vault-gold-05"
          : "border-vault-border bg-vault-surface"
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {editing ? (
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") update.mutate({ title });
                if (e.key === "Escape") {
                  setTitle(candidate.title);
                  setEditing(false);
                }
              }}
              autoFocus
              className="w-full bg-transparent text-sm font-medium text-vault-text border-b border-vault-gold/50 outline-none pb-0.5"
            />
          ) : (
            <button
              onClick={() => setEditing(true)}
              className="text-sm font-medium text-vault-text text-left hover:text-vault-gold transition-colors"
            >
              {candidate.title}
            </button>
          )}
          <div className="flex items-center gap-3 mt-1.5">
            <span className="text-xs font-mono text-vault-muted">
              {candidate.fragment_count} fragments
            </span>
            {candidate.source_section_path && (
              <span className="text-xs font-mono text-vault-muted truncate">
                ← {candidate.source_section_path}
              </span>
            )}
          </div>
          {candidate.internal_headings && candidate.internal_headings.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {candidate.internal_headings.map((heading) => (
                <span
                  key={heading}
                  className="text-[11px] font-mono px-2 py-1 rounded-full border border-vault-border text-vault-muted bg-vault-bg"
                >
                  {heading}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <span className={cn("text-xs px-2 py-0.5 rounded-full border font-mono", statusColors[candidate.status])}>
            {candidate.status}
          </span>
          {candidate.status !== "confirmed" && (
            <button
              onClick={() => update.mutate({ status: "confirmed" })}
              className="p-1 rounded hover:bg-vault-success/10 text-vault-muted hover:text-vault-success transition-colors"
            >
              <Check size={13} />
            </button>
          )}
          {candidate.status !== "rejected" && (
            <button
              onClick={() => update.mutate({ status: "rejected" })}
              className="p-1 rounded hover:bg-vault-error/10 text-vault-muted hover:text-vault-error transition-colors"
            >
              <X size={13} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}


function ReviewSection({
  name, candidates, projectId,
}: {
  name: string; candidates: ArticleCandidate[]; projectId: string;
}) {
  const [expanded, setExpanded] = useState(true);
  return (
    <div className="mb-6">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left text-xs font-mono uppercase tracking-widest text-vault-gold mb-3 flex items-center gap-2 hover:text-vault-gold/80 transition-colors"
      >
        {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        <span>{name}</span>
        <span className="text-vault-muted ml-auto normal-case tracking-normal">
          {candidates.length}
        </span>
      </button>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div className="space-y-2">
              {candidates.map((c) => (
                <CandidateCard key={c.id} candidate={c} projectId={projectId} />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}


function ProposalPanel({ item, projectId }: { item: InboxItem; projectId: string }) {
  const qc = useQueryClient();
  const proposal = item.proposal;

  const confirmedCount = proposal.candidates.filter((c) => c.status === "confirmed").length;
  const proposedCount = proposal.candidates.filter((c) => c.status === "proposed").length;

  const build = useMutation({
    mutationFn: () => api.articles.build(projectId, proposal.id),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["articles", projectId] });
      qc.invalidateQueries({ queryKey: ["inbox", projectId] });
      qc.invalidateQueries({ queryKey: ["proposals", projectId] });
      toast.success(`Built ${d.count} articles`);
    },
    onError: () => toast.error("Build failed"),
  });

  const confirmAll = useMutation({
    mutationFn: () => api.structure.confirmAll(projectId, proposal.id),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["inbox", projectId] });
      qc.invalidateQueries({ queryKey: ["proposals", projectId] });
      toast.success(
        d.confirmed_count > 0
          ? `Confirmed ${d.confirmed_count} candidates`
          : "All candidates already reviewed"
      );
    },
    onError: () => toast.error("Failed to confirm all"),
  });

  const sections = proposal.candidates.reduce<Record<string, ArticleCandidate[]>>((acc, c) => {
    const key = c.suggested_section ?? "Uncategorized";
    (acc[key] ??= []).push(c);
    return acc;
  }, {});

  return (
    <div>
      <InboxGatePanel item={item} />

      <div className="flex items-center justify-between mb-6">
        <div>
          <span
            className={cn(
              "text-xs font-mono px-2 py-1 rounded-full border",
              proposal.status === "ready"
                ? "text-vault-gold border-vault-gold/30 bg-vault-gold-05"
                : "text-vault-muted border-vault-border"
            )}
          >
            {proposal.status}
          </span>
          <span className="text-xs text-vault-muted font-mono ml-3">
            {proposal.candidates.length} candidates · {confirmedCount} confirmed · {formatDate(proposal.created_at)}
          </span>
        </div>
        {proposal.status === "ready" && (
          <div className="flex items-center gap-2">
            {proposedCount > 0 && (
              <Button
                onClick={() => confirmAll.mutate()}
                disabled={confirmAll.isPending}
                variant="outline"
                className="border-vault-border text-vault-text hover:border-vault-gold/50 hover:text-vault-gold gap-2"
                title={`Confirm all ${proposedCount} still-proposed candidates`}
              >
                <CheckCheck size={14} />
                {confirmAll.isPending ? "Confirming…" : `Accept All (${proposedCount})`}
              </Button>
            )}
            <Button
              onClick={() => build.mutate()}
              disabled={build.isPending || confirmedCount === 0}
              className="bg-vault-gold text-vault-bg hover:bg-vault-gold/90 gap-2 disabled:opacity-40 disabled:cursor-not-allowed"
              title={
                confirmedCount === 0
                  ? "Confirm at least one candidate before building"
                  : `Build ${confirmedCount} articles`
              }
            >
              <Play size={13} />
              {build.isPending ? "Building…" : `Build Articles (${confirmedCount})`}
            </Button>
          </div>
        )}
      </div>

      {Object.entries(sections).map(([section, candidates]) => (
        <ReviewSection
          key={section}
          name={section}
          candidates={candidates}
          projectId={projectId}
        />
      ))}
    </div>
  );
}


export function ReviewInboxPageClient() {
  const { projectId } = useParams<{ projectId: string }>();
  const qc = useQueryClient();

  const { data: inbox = [], isLoading } = useQuery({
    queryKey: ["inbox", projectId],
    queryFn: () => api.inbox.list(projectId),
    refetchOnMount: "always",
    refetchInterval: (query) => {
      const data = (query.state.data ?? []) as InboxItem[];
      return data[0]?.status === "pending" ? 3000 : false;
    },
  });

  const propose = useMutation({
    mutationFn: () => api.structure.propose(projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inbox", projectId] });
      qc.invalidateQueries({ queryKey: ["proposals", projectId] });
      toast.success("Structure analysis started");
    },
    onError: () => toast.error("Failed to start analysis"),
  });

  const latest = inbox[0]?.proposal;
  const latestItem = inbox[0];

  return (
    <div className="max-w-4xl mx-auto px-8 py-12">
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-display text-3xl font-semibold text-vault-text mb-2">Inbox</h1>
          <p className="text-vault-muted text-sm">Review pending candidate proposals before building.</p>
        </div>
        <Button
          onClick={() => propose.mutate()}
          disabled={propose.isPending}
          variant="outline"
          className="border-vault-border text-vault-text hover:border-vault-gold/50 hover:text-vault-gold gap-2"
        >
          <Layers size={14} />
          {propose.isPending ? "Analyzing…" : "New Proposal"}
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-16 bg-vault-surface rounded-lg animate-pulse" />
          ))}
        </div>
      ) : !latest || !latestItem ? (
        <div className="text-center py-24 border border-dashed border-vault-border rounded-lg">
          <Layers size={28} className="mx-auto text-vault-muted mb-4" />
          <p className="text-vault-muted text-sm mb-4">No inbox items yet.</p>
          <Button
            onClick={() => propose.mutate()}
            className="bg-vault-gold text-vault-bg hover:bg-vault-gold/90"
          >
            Analyse Structure
          </Button>
        </div>
      ) : (
        <ProposalPanel item={latestItem} projectId={projectId} />
      )}
    </div>
  );
}
