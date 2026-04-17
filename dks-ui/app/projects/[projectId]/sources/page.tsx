"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { motion, AnimatePresence } from "framer-motion";
import {
  Upload,
  FileText,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { formatDate, cn } from "@/lib/utils";
import type { Source } from "@/lib/types";

// Both pending and processing spin — pending = queued for worker,
// processing = worker has picked it up. From the user's POV it's one
// "in-flight" state, so the visual is identical: a rotating spinner.
const STATUS_CONFIG = {
  pending: {
    icon: Loader2,
    color: "text-vault-warning",
    label: "Queued",
    spin: true,
  },
  processing: {
    icon: Loader2,
    color: "text-vault-warning",
    label: "Processing",
    spin: true,
  },
  done: {
    icon: CheckCircle2,
    color: "text-vault-success",
    label: "Done",
    spin: false,
  },
  failed: {
    icon: AlertCircle,
    color: "text-vault-error",
    label: "Failed",
    spin: false,
  },
} satisfies Record<
  Source["status"],
  {
    icon: React.ComponentType<{ size?: number; className?: string }>;
    color: string;
    label: string;
    spin: boolean;
  }
>;

function SourceRow({
  source,
  onDelete,
  isDeleting,
}: {
  source: Source;
  onDelete: (id: string) => void;
  isDeleting: boolean;
}) {
  const cfg = STATUS_CONFIG[source.status];
  const Icon = cfg.icon;

  const handleDelete = () => {
    if (isDeleting) return;
    if (window.confirm(`Delete "${source.filename}"? This cannot be undone.`)) {
      onDelete(source.id);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -8, height: 0 }}
      className="flex items-center gap-4 border-b border-vault-border px-5 py-4 transition-colors hover:bg-vault-surface-2"
    >
      <FileText size={16} className="shrink-0 text-vault-muted" />

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-vault-text">
          {source.filename}
        </p>
        <p className="mt-0.5 font-mono text-xs text-vault-muted">
          {formatDate(source.created_at)}
        </p>
      </div>

      <span className="rounded border border-vault-border bg-vault-surface px-2 py-0.5 font-mono text-xs uppercase text-vault-muted">
        {source.source_type}
      </span>

      {/* AnimatePresence + key=status → smooth fade/scale on every state
          transition (queued → processing → done). Without it React reuses
          the same node and the swap is instantaneous, which is what made
          the indicator feel "frozen" on fast files. */}
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={source.status}
          initial={{ opacity: 0, scale: 0.85 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.85 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
          className={cn(
            "flex items-center gap-1.5 text-xs font-medium",
            cfg.color
          )}
        >
          <Icon size={13} className={cfg.spin ? "animate-spin" : ""} />
          {cfg.label}
        </motion.div>
      </AnimatePresence>

      <button
        type="button"
        onClick={handleDelete}
        disabled={isDeleting}
        aria-label={`Delete ${source.filename}`}
        title="Delete source"
        className={cn(
          "rounded p-1.5 text-vault-muted transition-colors",
          "hover:bg-vault-surface hover:text-vault-error",
          "disabled:cursor-not-allowed disabled:opacity-40"
        )}
      >
        {isDeleting ? (
          <Loader2 size={14} className="animate-spin" />
        ) : (
          <Trash2 size={14} />
        )}
      </button>
    </motion.div>
  );
}

export default function SourcesPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;
  const qc = useQueryClient();

  const { data: sources = [], isLoading } = useQuery<Source[]>({
    queryKey: ["sources", projectId],
    queryFn: () => api.sources.list(projectId),
    refetchInterval: (query) => {
      const data = query.state.data ?? [];
      return data.some(
        (s) => s.status === "pending" || s.status === "processing"
      )
        ? 3000
        : false;
    },
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.sources.upload(projectId, file),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["sources", projectId] });
      toast.success("File uploaded — processing started");
    },
    onError: () => {
      toast.error("Upload failed");
    },
  });

  const remove = useMutation({
    mutationFn: (sourceId: string) => api.sources.remove(projectId, sourceId),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["sources", projectId] });
      toast.success("Source deleted");
    },
    onError: (e: unknown) => {
      toast.error(
        `Could not delete source: ${e instanceof Error ? e.message : "unknown error"}`
      );
    },
  });

  const onDrop = useCallback(
    (files: File[]) => {
      files.forEach((file) => {
        upload.mutate(file);
      });
    },
    [upload]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [
        ".docx",
      ],
    },
    multiple: true,
  });

  return (
    <div className="mx-auto max-w-4xl px-8 py-12">
      <div className="mb-8">
        <h1 className="text-display mb-2 text-3xl font-semibold text-vault-text">
          Sources
        </h1>
        <p className="text-sm text-vault-muted">
          Upload PDF or DOCX files to extract knowledge.
        </p>
      </div>

      <div
        {...getRootProps()}
        className={cn(
          "mb-8 cursor-pointer rounded-lg border-2 border-dashed p-12 text-center transition-all duration-200",
          isDragActive
            ? "scale-[1.01] border-vault-gold bg-vault-gold-05"
            : "border-vault-border hover:border-vault-border-2 hover:bg-vault-surface"
        )}
      >
        <input {...getInputProps()} />
        <Upload
          size={28}
          className={cn(
            "mx-auto mb-3",
            isDragActive ? "text-vault-gold" : "text-vault-muted"
          )}
        />
        <p className="text-sm text-vault-text">
          {isDragActive ? "Drop to upload" : "Drag & drop PDF or DOCX"}
        </p>
        <p className="mt-1 text-xs text-vault-muted">or click to browse</p>
      </div>

      <div className="overflow-hidden rounded-lg border border-vault-border bg-vault-surface">
        <div className="border-b border-vault-border px-5 py-3">
          <span className="font-mono text-xs uppercase tracking-widest text-vault-muted">
            {sources.length} source{sources.length !== 1 ? "s" : ""}
          </span>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-sm text-vault-muted">Loading…</div>
        ) : sources.length === 0 ? (
          <div className="p-12 text-center text-sm text-vault-muted">
            No sources yet
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {sources.map((source) => (
              <SourceRow
                key={source.id}
                source={source}
                onDelete={(id) => remove.mutate(id)}
                isDeleting={remove.isPending && remove.variables === source.id}
              />
            ))}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}