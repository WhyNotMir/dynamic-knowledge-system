"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useState } from "react";
import { motion } from "framer-motion";
import { Layers, Play, Check, X, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { cn, formatDate } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import type { StructureProposal, ArticleCandidate } from "@/lib/types";

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
      qc.invalidateQueries({ queryKey: ["proposals", projectId] });
      setEditing(false);
    },
  });

  const statusColors = {
    proposed:  "text-vault-muted border-vault-border",
    confirmed: "text-vault-success border-vault-success/30 bg-vault-success/5",
    rejected:  "text-vault-error border-vault-error/30 bg-vault-error/5",
    merged:    "text-vault-warning border-vault-warning/30",
  };

  return (
    <div className={cn(
      "p-4 border rounded-lg transition-all",
      candidate.status === "confirmed" ? "border-vault-gold/30 bg-vault-gold-05" : "border-vault-border bg-vault-surface"
    )}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {editing ? (
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") update.mutate({ title });
                if (e.key === "Escape") { setTitle(candidate.title); setEditing(false); }
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

function ProposalPanel({ proposal, projectId }: { proposal: StructureProposal; projectId: string }) {
  const qc = useQueryClient();

  const build = useMutation({
    mutationFn: () => api.articles.build(projectId, proposal.id),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["articles", projectId] });
      toast.success(`Built ${d.count} articles`);
    },
    onError: () => toast.error("Build failed"),
  });

  const sections = proposal.candidates.reduce<Record<string, ArticleCandidate[]>>((acc, c) => {
    const key = c.suggested_section ?? "Uncategorized";
    (acc[key] ??= []).push(c);
    return acc;
  }, {});

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <span className={cn(
            "text-xs font-mono px-2 py-1 rounded-full border",
            proposal.status === "ready"
              ? "text-vault-gold border-vault-gold/30 bg-vault-gold-05"
              : "text-vault-muted border-vault-border"
          )}>
            {proposal.status}
          </span>
          <span className="text-xs text-vault-muted font-mono ml-3">
            {proposal.candidates.length} candidates · {formatDate(proposal.created_at)}
          </span>
        </div>
        {proposal.status === "ready" && (
          <Button
            onClick={() => build.mutate()}
            disabled={build.isPending}
            className="bg-vault-gold text-vault-bg hover:bg-vault-gold/90 gap-2"
          >
            <Play size={13} />
            {build.isPending ? "Building…" : "Build Articles"}
          </Button>
        )}
      </div>

      {Object.entries(sections).map(([section, candidates]) => (
        <div key={section} className="mb-6">
          <p className="text-xs font-mono uppercase tracking-widest text-vault-gold mb-3 flex items-center gap-2">
            <ChevronRight size={10} /> {section}
          </p>
          <div className="space-y-2">
            {candidates.map((c) => (
              <CandidateCard key={c.id} candidate={c} projectId={projectId} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function ReviewPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const qc = useQueryClient();

  const { data: proposals = [], isLoading } = useQuery({
    queryKey: ["proposals", projectId],
    queryFn: () => api.structure.listProposals(projectId),
    // Poll every 3s while the latest proposal is still PENDING so the UI
    // transitions to READY automatically once the worker finishes.
    refetchInterval: (query) => {
      const data = query.state.data ?? [];
      return data[0]?.status === "pending" ? 3000 : false;
    },
  });

  const propose = useMutation({
    mutationFn: () => api.structure.propose(projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["proposals", projectId] });
      toast.success("Structure analysis started");
    },
    onError: () => toast.error("Failed to start analysis"),
  });

  const latest = proposals[0];

  return (
    <div className="max-w-4xl mx-auto px-8 py-12">
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-display text-3xl font-semibold text-vault-text mb-2">Review</h1>
          <p className="text-vault-muted text-sm">Confirm article candidates before building.</p>
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
      ) : !latest ? (
        <div className="text-center py-24 border border-dashed border-vault-border rounded-lg">
          <Layers size={28} className="mx-auto text-vault-muted mb-4" />
          <p className="text-vault-muted text-sm mb-4">No proposals yet.</p>
          <Button
            onClick={() => propose.mutate()}
            className="bg-vault-gold text-vault-bg hover:bg-vault-gold/90"
          >
            Analyse Structure
          </Button>
        </div>
      ) : (
        <ProposalPanel proposal={latest} projectId={projectId} />
      )}
    </div>
  );
}