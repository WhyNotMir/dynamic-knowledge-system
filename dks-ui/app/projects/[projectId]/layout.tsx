"use client";
import { useQuery } from "@tanstack/react-query";
import { useParams, usePathname, useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  BookOpen, FileText, GitBranch, Share2,
  ChevronRight, ChevronDown, Layers, ArrowLeft,
} from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ArticleListItem } from "@/lib/types";

function groupBySection(articles: ArticleListItem[]) {
  const groups: Record<string, ArticleListItem[]> = {};
  for (const a of articles) {
    const key = a.suggested_section ?? "Uncategorized";
    if (!groups[key]) groups[key] = [];
    groups[key].push(a);
  }
  return groups;
}

function SidebarSection({
  name, articles, projectId,
}: {
  name: string; articles: ArticleListItem[]; projectId: string;
}) {
  const pathname = usePathname();
  return (
    <div className="mb-1">
      <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono uppercase tracking-widest text-vault-muted">
        <ChevronDown size={10} />
        <span>{name}</span>
      </div>
      {articles.map((a) => {
        const active = pathname.includes(a.id);
        return (
          <Link
            key={a.id}
            href={`/projects/${projectId}/articles/${a.id}`}
            className={cn(
              "block px-4 py-2 text-sm rounded mx-1 transition-all duration-150 truncate",
              active
                ? "bg-vault-gold-10 text-vault-gold border-l-2 border-vault-gold pl-3.5"
                : "text-vault-muted hover:text-vault-text hover:bg-vault-surface-2"
            )}
          >
            {a.title}
          </Link>
        );
      })}
    </div>
  );
}

export default function ProjectLayout({ children }: { children: React.ReactNode }) {
  const { projectId } = useParams<{ projectId: string }>();
  const pathname = usePathname();
  const router = useRouter();

  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.projects.get(projectId),
  });

  const { data: articles = [] } = useQuery({
    queryKey: ["articles", projectId],
    queryFn: () => api.articles.list(projectId),
  });

  const groups = groupBySection(articles);

  const navItems = [
    { href: `/projects/${projectId}/articles`, icon: BookOpen,  label: "Articles"  },
    { href: `/projects/${projectId}/sources`,  icon: FileText,  label: "Sources"   },
    { href: `/projects/${projectId}/review`,   icon: Layers,    label: "Review"    },
    { href: `/projects/${projectId}/graph`,    icon: Share2,    label: "Graph"     },
  ];

  return (
    <div className="flex h-screen overflow-hidden bg-vault-bg">
      {/* Sidebar */}
      <aside className="w-64 flex-shrink-0 border-r border-vault-border flex flex-col bg-vault-surface overflow-hidden">
        {/* Project name */}
        <div className="px-4 py-5 border-b border-vault-border">
          <button
            onClick={() => router.push("/projects")}
            className="flex items-center gap-1.5 text-vault-muted hover:text-vault-text transition-colors text-xs mb-3"
          >
            <ArrowLeft size={12} /> All Projects
          </button>
          <h2 className="text-display text-base font-semibold text-vault-text truncate">
            {project?.name ?? "…"}
          </h2>
        </div>

        {/* Nav */}
        <nav className="px-2 py-3 border-b border-vault-border space-y-0.5">
          {navItems.map(({ href, icon: Icon, label }) => {
            const active = pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2.5 px-3 py-2 rounded text-sm transition-all duration-150",
                  active
                    ? "bg-vault-gold-10 text-vault-gold"
                    : "text-vault-muted hover:text-vault-text hover:bg-vault-surface-2"
                )}
              >
                <Icon size={15} />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* Article tree */}
        <div className="flex-1 overflow-y-auto py-3">
          {articles.length === 0 ? (
            <p className="text-xs text-vault-muted px-4 py-2">No articles yet</p>
          ) : (
            Object.entries(groups).map(([section, arts]) => (
              <SidebarSection
                key={section}
                name={section}
                articles={arts}
                projectId={projectId}
              />
            ))
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-vault-border">
          <p className="text-xs font-mono text-vault-muted">
            {articles.length} article{articles.length !== 1 ? "s" : ""}
          </p>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto">
        {children}
      </main>
    </div>
  );
}