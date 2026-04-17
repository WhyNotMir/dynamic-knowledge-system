"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { Plus, BookOpen, Clock, Loader2, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export default function ProjectsPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: api.projects.list,
  });

  const create = useMutation({
    mutationFn: () => api.projects.create({ name, description: desc || undefined }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setOpen(false);
      setName(""); setDesc("");
      toast.success("Project created");
      router.push(`/projects/${p.id}/sources`);
    },
    onError: () => toast.error("Failed to create project"),
  });

  const remove = useMutation({
    mutationFn: (projectId: string) => api.projects.remove(projectId),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["projects"] });
      toast.success("Project deleted");
    },
    onError: (e: unknown) => {
      toast.error(
        `Could not delete project: ${
          e instanceof Error ? e.message : "unknown error"
        }`
      );
    },
  });

  const confirmDelete = (
    e: React.MouseEvent,
    project: { id: string; name: string }
  ) => {
    // Stop the surrounding card click-handler (which navigates away).
    e.stopPropagation();
    if (remove.isPending) return;
    const ok = window.confirm(
      `Delete project "${project.name}" and all its sources, proposals and articles? This cannot be undone.`
    );
    if (ok) remove.mutate(project.id);
  };

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b border-vault-border glass sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-8 py-5 flex items-center justify-between">
          <div>
            <h1 className="text-display text-2xl font-semibold text-vault-gold tracking-wide">
              DKS
            </h1>
            <p className="text-xs text-vault-muted mt-0.5 font-mono uppercase tracking-widest">
              Dynamic Knowledge System
            </p>
          </div>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button className="bg-vault-gold text-vault-bg hover:bg-vault-gold/90 gap-2 font-medium">
                <Plus size={16} /> New Project
              </Button>
            </DialogTrigger>
            <DialogContent className="bg-vault-surface border-vault-border">
              <DialogHeader>
                <DialogTitle className="text-display text-xl text-vault-text">
                  New Project
                </DialogTitle>
              </DialogHeader>
              <div className="space-y-4 mt-2">
                <Input
                  placeholder="Project name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="bg-vault-bg border-vault-border text-vault-text placeholder:text-vault-muted"
                />
                <Textarea
                  placeholder="Description (optional)"
                  value={desc}
                  onChange={(e) => setDesc(e.target.value)}
                  className="bg-vault-bg border-vault-border text-vault-text placeholder:text-vault-muted resize-none"
                  rows={3}
                />
                <Button
                  onClick={() => create.mutate()}
                  disabled={!name.trim() || create.isPending}
                  className="w-full bg-vault-gold text-vault-bg hover:bg-vault-gold/90"
                >
                  {create.isPending ? "Creating…" : "Create Project"}
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-6xl mx-auto px-8 py-12">
        <div className="mb-10">
          <h2 className="text-display text-4xl font-medium text-vault-text mb-2">
            Your Projects
          </h2>
          <p className="text-vault-muted">
            Each project is an isolated knowledge space for a document corpus.
          </p>
        </div>

        {isLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="h-40 bg-vault-surface rounded-lg animate-pulse" />
            ))}
          </div>
        ) : projects.length === 0 ? (
          <div className="text-center py-24 border border-dashed border-vault-border rounded-lg">
            <BookOpen size={32} className="mx-auto text-vault-muted mb-4" />
            <p className="text-vault-muted">No projects yet. Create your first one.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <AnimatePresence initial={false}>
              {projects.map((p, i) => {
                const isDeleting = remove.isPending && remove.variables === p.id;
                return (
                  <motion.div
                    key={p.id}
                    initial={{ opacity: 0, y: 16 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8, scale: 0.96 }}
                    transition={{ delay: i * 0.05 }}
                    onClick={() => router.push(`/projects/${p.id}/articles`)}
                    className="group relative bg-vault-surface border border-vault-border rounded-lg p-6 cursor-pointer
                               hover:border-vault-gold/40 hover:bg-vault-surface-2 transition-all duration-200"
                  >
                    <div className="flex items-start justify-between mb-4">
                      <div className="w-8 h-8 rounded bg-vault-gold-05 border border-vault-gold/20
                                      flex items-center justify-center group-hover:border-vault-gold/40 transition-colors">
                        <BookOpen size={14} className="text-vault-gold" />
                      </div>
                      <button
                        type="button"
                        onClick={(e) => confirmDelete(e, p)}
                        disabled={isDeleting}
                        aria-label={`Delete project ${p.name}`}
                        title="Delete project"
                        className="rounded p-1.5 text-vault-muted opacity-0 transition
                                   group-hover:opacity-100 hover:bg-vault-surface hover:text-vault-error
                                   disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {isDeleting ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : (
                          <Trash2 size={14} />
                        )}
                      </button>
                    </div>
                    <h3 className="text-display text-lg font-medium text-vault-text mb-1 group-hover:text-vault-gold transition-colors">
                      {p.name}
                    </h3>
                    {p.description && (
                      <p className="text-sm text-vault-muted line-clamp-2 mb-4">
                        {p.description}
                      </p>
                    )}
                    <div className="flex items-center gap-1 text-xs text-vault-muted mt-auto">
                      <Clock size={11} />
                      <span>{formatDate(p.created_at)}</span>
                    </div>
                  </motion.div>
                );
              })}
            </AnimatePresence>
          </div>
        )}
      </main>
    </div>
  );
}