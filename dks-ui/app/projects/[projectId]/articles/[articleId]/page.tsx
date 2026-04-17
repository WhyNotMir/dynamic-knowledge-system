"use client";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { motion } from "framer-motion";
import { FileText, MapPin } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ArticleBlock } from "@/lib/types";

function Block({ block, index }: { block: ArticleBlock; index: number }) {
  const isHeading = block.element_type === "heading";
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.03, duration: 0.3 }}
      className="group relative"
    >
      <div className={cn("gold-line pl-4 py-1", isHeading && "border-l-vault-gold/70")}>
        {isHeading ? (
          <h3 className="text-display text-lg font-semibold text-vault-text mb-1">
            {block.content}
          </h3>
        ) : (
          <p className="text-vault-text leading-relaxed text-[15px]">
            {block.content}
          </p>
        )}

        {/* Citation */}
        <div className="flex items-center gap-3 mt-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
          {block.page_number && (
            <span className="flex items-center gap-1 text-xs font-mono text-vault-muted">
              <FileText size={10} />
              p.{block.page_number}
            </span>
          )}
          {block.section_path && (
            <span className="flex items-center gap-1 text-xs font-mono text-vault-muted truncate max-w-xs">
              <MapPin size={10} />
              {block.section_path}
            </span>
          )}
        </div>
      </div>
    </motion.div>
  );
}

export default function ArticlePage() {
  const { projectId, articleId } = useParams<{ projectId: string; articleId: string }>();

  const { data: article, isLoading } = useQuery({
    queryKey: ["article", articleId],
    queryFn: () => api.articles.get(projectId, articleId),
  });

  if (isLoading) {
    return (
      <div className="max-w-3xl mx-auto px-8 py-12 space-y-4">
        {[...Array(6)].map((_, i) => (
          <div key={i} className={cn("h-5 bg-vault-surface rounded animate-pulse", i === 0 && "h-8 w-2/3")} />
        ))}
      </div>
    );
  }

  if (!article) return null;

  return (
    <div className="max-w-3xl mx-auto px-8 py-12">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-10"
      >
        {article.suggested_section && (
          <p className="text-xs font-mono uppercase tracking-widest text-vault-gold mb-3">
            {article.suggested_section}
          </p>
        )}
        <h1 className="text-display text-4xl font-semibold text-vault-text leading-tight mb-4">
          {article.title}
        </h1>
        <div className="flex items-center gap-4 text-xs text-vault-muted font-mono">
          <span>{article.blocks.length} blocks</span>
          <span className="w-1 h-1 rounded-full bg-vault-border" />
          <span className={cn(
            "px-2 py-0.5 rounded-full border text-xs",
            article.status === "published"
              ? "border-vault-success/30 text-vault-success bg-vault-success/10"
              : "border-vault-border text-vault-muted"
          )}>
            {article.status}
          </span>
        </div>
        <div className="mt-6 border-t border-vault-border" />
      </motion.div>

      {/* Blocks */}
      <div className="space-y-5">
        {article.blocks.map((block, i) => (
          <Block key={block.id} block={block} index={i} />
        ))}
      </div>
    </div>
  );
}