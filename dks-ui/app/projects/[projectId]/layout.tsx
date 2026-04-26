"use client";
import { useQuery } from "@tanstack/react-query";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  BookOpen, FileText, MessageCircle, Share2,
  ChevronRight, ChevronDown, Layers, ArrowLeft,
} from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ArticleSidebarGroup } from "@/lib/types";

function groupHasActiveChild(group: ArticleSidebarGroup, pathname: string, projectId: string) {
  const articlePath = (articleId: string) => `/projects/${projectId}/articles/${articleId}`;
  return (
    group.articles.some((article) => pathname === articlePath(article.id)) ||
    (group.groups?.some((child) => groupHasActiveChild(child, pathname, projectId)) ?? false)
  );
}

function SidebarSection({
  name, articles, projectId, depth = 0, groups = [], totalCount,
}: {
  name: string;
  articles: ArticleSidebarGroup["articles"];
  projectId: string;
  depth?: number;
  groups?: ArticleSidebarGroup[];
  totalCount?: number;
}) {
  const pathname = usePathname();
  const articlePath = (articleId: string) => `/projects/${projectId}/articles/${articleId}`;
  const hasActive = articles.some((a) => pathname === articlePath(a.id));
  const childHasActive = groups.some((child) => groupHasActiveChild(child, pathname, projectId));
  const [expanded, setExpanded] = useState(depth < 1 || hasActive || childHasActive);
  const isOpen = expanded;
  const computedTotalCount = articles.length + groups.reduce((acc, child) => acc + child.total_count, 0);

  return (
    <div className="mb-1" style={{ paddingLeft: depth === 0 ? 0 : Math.min(depth * 10, 20) }}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          "flex items-start gap-1.5 px-3 py-1.5 w-full text-left transition-colors rounded-lg",
          depth === 0
            ? "text-xs font-mono uppercase tracking-widest text-vault-muted hover:text-vault-text"
            : "text-[11px] font-mono uppercase tracking-[0.22em] text-vault-muted/75 hover:text-vault-text"
        )}
      >
        <span className="mt-0.5 shrink-0">
          {isOpen ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        </span>
        <span className="min-w-0 whitespace-normal break-words leading-5">{name}</span>
        <span className="ml-auto shrink-0 rounded-full border border-vault-border px-1.5 py-0.5 text-[10px] tracking-normal text-vault-muted/80">
          {totalCount ?? computedTotalCount}
        </span>
      </button>
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            key="content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15, ease: "easeOut" }}
            className="overflow-hidden"
          >
            {groups.map((child) => (
              <SidebarSection
                key={child.key}
                name={child.label}
                articles={child.articles}
                groups={child.groups}
                totalCount={child.total_count}
                depth={depth + 1}
                projectId={projectId}
              />
            ))}
            {articles.map((a) => {
              const active = pathname === articlePath(a.id);
              return (
                <Link
                  key={a.id}
                  href={articlePath(a.id)}
                  className={cn(
                    "block px-4 py-2.5 text-sm rounded mx-1 transition-all duration-150 will-change-transform",
                    active
                      ? "bg-vault-gold-10 text-vault-gold border-l-2 border-vault-gold pl-3.5"
                      : "text-vault-muted hover:text-vault-text hover:bg-vault-surface-2"
                  )}
                  style={{ marginLeft: depth > 0 ? Math.min(depth * 6, 18) : 0 }}
                >
                  <span className="flex items-start gap-2 min-w-0">
                    {a.kind === "node" && (
                      <span className="mt-0.5 shrink-0 text-[10px] font-mono uppercase tracking-wide text-vault-gold/80">
                        Node
                      </span>
                    )}
                    <span className="min-w-0 whitespace-normal break-words leading-5">
                      {a.title}
                    </span>
                  </span>
                </Link>
              );
            })}
          </motion.div>
        )}
      </AnimatePresence>
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
  const { data: sidebarGroups = [] } = useQuery({
    queryKey: ["articles-sidebar", projectId],
    queryFn: () => api.articles.sidebar(projectId),
  });

  const navItems = [
    { href: `/projects/${projectId}/articles`, icon: BookOpen,  label: "Articles"  },
    { href: `/projects/${projectId}/ask`,      icon: MessageCircle, label: "Ask" },
    { href: `/projects/${projectId}/sources`,  icon: FileText,  label: "Sources"   },
    { href: `/projects/${projectId}/inbox`,    icon: Layers,    label: "Inbox"     },
    { href: `/projects/${projectId}/graph`,    icon: Share2,    label: "Graph"     },
  ];

  return (
    <div className="flex h-screen overflow-hidden bg-vault-bg">
      {/* Sidebar */}
      <aside className="w-72 lg:w-80 flex-shrink-0 border-r border-vault-border flex flex-col bg-vault-surface overflow-hidden">
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
            <>
              {sidebarGroups.map((group) => (
                <SidebarSection
                  key={group.key}
                  name={group.label}
                  articles={group.articles}
                  groups={group.groups}
                  totalCount={group.total_count}
                  projectId={projectId}
                />
              ))}
            </>
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
