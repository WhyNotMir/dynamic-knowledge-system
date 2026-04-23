"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { FileText, MapPin, Quote } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Article, ArticleBlock, GraphPayload, StructuralBlock } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const LIST_PREFIX_RE = /^(([\u2022*•◦-])|(\d+[\.\)]))\s*/;
const HEADING_PREFIX_RE = /^(\d+(?:\.\d+)*)\s+(.+)$/;
const MULTISPACE_RE = /\s+/g;

function normalizeText(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

function looksLikeRealHeading(value: string) {
  const stripped = normalizeText(value);
  if (!stripped || stripped.length > 90) return false;

  const lowered = stripped.toLowerCase();
  if (["<eos>", "arxiv:", "[cs.", "wsj", "gnmt", "en-de", "en-fr"].some((marker) => lowered.includes(marker))) {
    return false;
  }

  const alpha = [...stripped].filter((char) => /[A-Za-z]/.test(char)).length;
  const digits = [...stripped].filter((char) => /\d/.test(char)).length;
  if (alpha < 3) return false;
  if (digits > alpha) return false;

  return true;
}

function parseHeadingLabel(value: string) {
  const stripped = normalizeText(value);
  const match = stripped.match(HEADING_PREFIX_RE);
  if (!match) {
    return {
      depth: 1,
      label: stripped,
      prefix: null as string | null,
    };
  }

  return {
    depth: match[1].split(".").length,
    label: match[2].trim(),
    prefix: match[1],
  };
}

function escapeRegex(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

type LinkTarget = {
  articleId: string;
  label: string;
};

function collectLinkRanges(content: string, targets: LinkTarget[]) {
  const candidates: Array<{ start: number; end: number; target: LinkTarget }> = [];
  for (const target of targets) {
    const escaped = escapeRegex(target.label);
    const pattern = /[A-Za-z0-9]/.test(target.label)
      ? new RegExp(`(?<!\\w)${escaped}(?!\\w)`, "gi")
      : new RegExp(escaped, "gi");
    for (const match of content.matchAll(pattern)) {
      const start = match.index;
      if (start == null) continue;
      candidates.push({
        start,
        end: start + match[0].length,
        target,
      });
    }
  }

  candidates.sort((left, right) => {
    if (left.start !== right.start) return left.start - right.start;
    return (right.end - right.start) - (left.end - left.start);
  });

  const accepted: Array<{ start: number; end: number; target: LinkTarget }> = [];
  let cursor = -1;
  for (const candidate of candidates) {
    if (candidate.start < cursor) continue;
    accepted.push(candidate);
    cursor = candidate.end;
  }
  return accepted;
}

function renderStyledText(
  content: string,
  inlineSpans: ArticleBlock["inline_spans"],
  segmentStart = 0
) {
  if (!inlineSpans || inlineSpans.length === 0) {
    return content;
  }

  const relevant = inlineSpans
    .filter((span) => span.end > segmentStart && span.start < segmentStart + content.length)
    .map((span) => ({
      ...span,
      start: Math.max(span.start - segmentStart, 0),
      end: Math.min(span.end - segmentStart, content.length),
    }))
    .sort((a, b) => a.start - b.start || a.end - b.end);

  if (relevant.length === 0) {
    return content;
  }

  const parts: React.ReactNode[] = [];
  let cursor = 0;

  for (const [index, span] of relevant.entries()) {
    if (span.start > cursor) {
      parts.push(<span key={`text-${segmentStart}-${index}-${cursor}`}>{content.slice(cursor, span.start)}</span>);
    }

    const text = content.slice(span.start, span.end);
    let node: React.ReactNode = text;
    if (span.style === "bold") {
      node = <strong>{text}</strong>;
    } else if (span.style === "italic") {
      node = <em>{text}</em>;
    } else if (span.style === "code") {
      node = <code className="rounded bg-vault-surface px-1 py-0.5 text-[0.95em]">{text}</code>;
    }
    parts.push(<span key={`span-${segmentStart}-${index}-${span.start}`}>{node}</span>);
    cursor = Math.max(cursor, span.end);
  }

  if (cursor < content.length) {
    parts.push(<span key={`tail-${segmentStart}-${cursor}`}>{content.slice(cursor)}</span>);
  }

  return parts;
}

function isNoiseBlock(block: ArticleBlock) {
  const stripped = normalizeText(block.content);
  if (!stripped) return true;
  if (["image", "table", "quote", "code_block"].includes(block.element_type)) return false;
  if (block.element_type === "heading") return !looksLikeRealHeading(stripped);

  const lowered = stripped.toLowerCase();
  if (lowered === "<eos>" || lowered === "eos") return true;
  if (["arxiv:", "[cs.", "gnmt", "en-de", "en-fr", "wsj"].some((marker) => lowered.includes(marker))) {
    return true;
  }
  if (/^[\W\d_]+$/.test(stripped)) return true;
  if (!/[A-Za-z]/.test(stripped) && /\d/.test(stripped)) return true;

  return false;
}

function getScrollParent(element: HTMLElement | null): HTMLElement | Window {
  if (!element) return window;

  let current = element.parentElement;
  while (current) {
    const style = window.getComputedStyle(current);
    const overflowY = style.overflowY;
    if ((overflowY === "auto" || overflowY === "scroll") && current.scrollHeight > current.clientHeight) {
      return current;
    }
    current = current.parentElement;
  }

  return window;
}

function renderTable(content: string) {
  const rows = content
    .split("\n")
    .map((row) => row.split("|").map((cell) => cell.trim()).filter(Boolean))
    .filter((row) => row.length > 0);

  if (rows.length === 0) {
    return <p className="text-vault-text leading-relaxed text-[15px]">{content}</p>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-vault-border bg-vault-surface">
      <table className="min-w-full text-sm">
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${rowIndex}-${row.join("-")}`} className="border-b last:border-b-0 border-vault-border">
              {row.map((cell, cellIndex) => (
                <td key={`${rowIndex}-${cellIndex}`} className="px-3 py-2 text-vault-text align-top">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderInlineContent(block: ArticleBlock, projectId: string, linkTargets: LinkTarget[]) {
  const ranges = collectLinkRanges(block.content, linkTargets);
  if (ranges.length === 0) {
    return renderStyledText(block.content, block.inline_spans);
  }

  const parts: React.ReactNode[] = [];
  let cursor = 0;

  for (const [index, range] of ranges.entries()) {
    if (range.start > cursor) {
      parts.push(
        <span key={`plain-${index}-${cursor}`}>
          {renderStyledText(block.content.slice(cursor, range.start), block.inline_spans, cursor)}
        </span>
      );
    }

    parts.push(
      <Link
        key={`link-${range.target.articleId}-${range.start}`}
        href={`/projects/${projectId}/articles/${range.target.articleId}`}
        className="text-vault-gold underline decoration-vault-gold/35 underline-offset-4 hover:text-vault-text transition-colors"
      >
        {renderStyledText(
          block.content.slice(range.start, range.end),
          block.inline_spans,
          range.start
        )}
      </Link>
    );
    cursor = range.end;
  }

  if (cursor < block.content.length) {
    parts.push(
      <span key={`tail-${cursor}`}>
        {renderStyledText(block.content.slice(cursor), block.inline_spans, cursor)}
      </span>
    );
  }

  return parts;
}

type HoveredCitation = {
  id: string;
  pageNumber: number | null;
  sectionPath: string | null;
};

function Block({
  block,
  index,
  projectId,
  linkTargets,
  onHoverCitation,
  onLeaveCitation,
}: {
  block: ArticleBlock;
  index: number;
  projectId: string;
  linkTargets: LinkTarget[];
  onHoverCitation: (value: HoveredCitation | null) => void;
  onLeaveCitation: () => void;
}) {
  const isHeading = block.element_type === "heading";
  const isList = block.element_type === "list_item";
  const isTable = block.element_type === "table";
  const isCaption = block.element_type === "caption";
  const isQuote = block.element_type === "quote";
  const isImage = block.element_type === "image";
  const isFootnote = block.element_type === "footnote";
  const headingMeta = isHeading ? parseHeadingLabel(block.content) : null;

  let body;
  if (isHeading) {
    if ((headingMeta?.depth ?? 1) <= 1) {
      body = (
        <div className="pt-4 pb-1">
          <div className="mb-3 h-px w-14 bg-vault-gold/55" />
          <h3 className="text-display text-[2rem] md:text-[2.25rem] font-semibold text-vault-text leading-[1.02]">
            {headingMeta?.label ?? renderInlineContent(block)}
          </h3>
        </div>
      );
    } else if (headingMeta?.depth === 2) {
      body = (
        <div className="pt-3 pb-1">
          <p className="mb-2 text-[11px] font-mono uppercase tracking-[0.26em] text-vault-gold/60">
            Section
          </p>
          <h3 className="text-display text-[1.65rem] md:text-[1.85rem] font-semibold text-vault-text leading-[1.08]">
            {headingMeta.label}
          </h3>
        </div>
      );
    } else {
      body = (
        <div className="space-y-1 pt-2">
          <p className="text-[11px] font-mono uppercase tracking-[0.22em] text-vault-gold/60">
            Topic
          </p>
          <h3 className="text-display text-[1.2rem] md:text-[1.35rem] font-semibold text-vault-text leading-[1.15]">
            {headingMeta?.label ?? renderInlineContent(block)}
          </h3>
        </div>
      );
    }
  } else if (isList) {
    const items = block.content
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => item.replace(LIST_PREFIX_RE, ""));
    body = (
      <ul className="list-disc pl-7 space-y-3 marker:text-vault-gold">
        {items.map((item) => (
          <li key={item} className="text-vault-text leading-8 text-[15px] pl-1">
            <span>{item}</span>
          </li>
        ))}
      </ul>
    );
  } else if (isTable) {
    body = renderTable(block.content);
  } else if (isCaption) {
    body = (
      <p className="text-sm italic text-vault-muted leading-relaxed">
        {block.content}
      </p>
    );
  } else if (isQuote) {
    body = (
          <blockquote className="border-l-2 border-vault-gold/60 pl-4 text-vault-text italic leading-relaxed">
        <div className="flex items-start gap-2">
          <Quote size={14} className="mt-1 text-vault-gold shrink-0" />
          <span>{renderInlineContent(block, projectId, linkTargets)}</span>
        </div>
      </blockquote>
    );
  } else if (isImage) {
    const imageRef = typeof block.meta_json?.image_ref === "string" ? block.meta_json.image_ref : null;
    body = imageRef ? (
      <figure className="rounded-xl border border-vault-border bg-vault-surface p-4">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={`${API_BASE}${imageRef}`}
          alt={typeof block.meta_json?.alt === "string" ? block.meta_json.alt : "Source image"}
          className="mx-auto max-h-[720px] w-auto max-w-full rounded-lg object-contain bg-white"
        />
      </figure>
    ) : (
      <p className="text-vault-muted text-sm">Image asset unavailable.</p>
    );
  } else if (isFootnote) {
    body = (
      <p className="text-sm text-vault-muted leading-relaxed border-l border-vault-border pl-3">
        {renderInlineContent(block, projectId, linkTargets)}
      </p>
    );
  } else {
    body = (
      <p className="text-vault-text leading-relaxed text-[15px]">
        {renderInlineContent(block, projectId, linkTargets)}
      </p>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.03, duration: 0.3 }}
      className="relative"
      onMouseEnter={() =>
        onHoverCitation({
          id: block.id,
          pageNumber: block.page_number,
          sectionPath: block.section_path,
        })
      }
      onMouseLeave={onLeaveCitation}
    >
      <div className={cn(isHeading ? "py-1.5" : "py-0.5")}>
        {body}
      </div>
    </motion.div>
  );
}

export default function ArticlePage() {
  const { projectId, articleId } = useParams<{ projectId: string; articleId: string }>();
  const qc = useQueryClient();
  const [activeHeadingId, setActiveHeadingId] = useState<string | null>(null);
  const [hoveredCitation, setHoveredCitation] = useState<HoveredCitation | null>(null);
  const [aliasModalOpen, setAliasModalOpen] = useState(false);
  const [aliasDraft, setAliasDraft] = useState("");
  const [aliasModalDraft, setAliasModalDraft] = useState<string[]>([]);
  const pageRef = useRef<HTMLDivElement | null>(null);

  const { data: article, isLoading } = useQuery({
    queryKey: ["article", articleId],
    queryFn: () => api.articles.get(projectId, articleId),
  });
  const { data: graph } = useQuery({
    queryKey: ["graph", projectId],
    queryFn: () => api.graph.get(projectId),
  });

  const { data: structuralBlocks = [] } = useQuery({
    queryKey: ["structural-blocks", projectId],
    queryFn: () => api.structuralBlocks.list(projectId),
  });

  const visibleBlocks = useMemo(
    () => article?.blocks.filter((block) => !isNoiseBlock(block)) ?? [],
    [article]
  );

  const headingBlocks = useMemo(
    () => visibleBlocks.filter((block) => block.element_type === "heading"),
    [visibleBlocks]
  );

  const displayBlockCount = useMemo(
    () => visibleBlocks.filter((block) => block.element_type !== "heading").length,
    [visibleBlocks]
  );

  const titleAlias = useMemo(() => article?.aliases?.[0] ?? article?.title ?? "", [article]);
  const aliasList = useMemo(() => article?.aliases ?? (article?.title ? [article.title] : []), [article]);
  const manualAliasList = useMemo(() => aliasList.slice(1), [aliasList]);

  const linkTargets = useMemo(() => {
    const seen = new Set<string>();
    const targets: LinkTarget[] = [];
    for (const node of graph?.nodes ?? []) {
      if (node.id === articleId) continue;
      const labels = [node.title, ...(node.aliases ?? [])]
        .map((label) => label.replace(MULTISPACE_RE, " ").trim())
        .filter((label) => label.length >= 2);
      for (const label of labels) {
        const key = `${node.id}:${label.toLowerCase()}`;
        if (seen.has(key)) continue;
        seen.add(key);
        targets.push({ articleId: node.id, label });
      }
    }
    return targets.sort((left, right) => right.label.length - left.label.length);
  }, [articleId, graph?.nodes]);

  const updateAliases = useMutation({
    mutationFn: (aliases: string[]) => api.articles.updateAliases(projectId, articleId, aliases),
    onMutate: async (nextAliases) => {
      setAliasModalDraft(nextAliases);
      setAliasDraft("");

      await Promise.all([
        qc.cancelQueries({ queryKey: ["article", articleId] }),
        qc.cancelQueries({ queryKey: ["graph", projectId] }),
      ]);

      const previousArticle = qc.getQueryData<Article>(["article", articleId]);
      const previousGraph = qc.getQueryData<GraphPayload>(["graph", projectId]);
      const fullAliases = titleAlias ? [titleAlias, ...nextAliases] : nextAliases;

      if (previousArticle) {
        qc.setQueryData<Article>(["article", articleId], {
          ...previousArticle,
          aliases: fullAliases,
        });
      }

      if (previousGraph) {
        qc.setQueryData<GraphPayload>(["graph", projectId], {
          ...previousGraph,
          nodes: previousGraph.nodes.map((node) =>
            node.id === articleId ? { ...node, aliases: fullAliases } : node
          ),
        });
      }

      return { previousArticle, previousGraph };
    },
    onError: (_error, _aliases, context) => {
      if (context?.previousArticle) {
        qc.setQueryData(["article", articleId], context.previousArticle);
      }
      if (context?.previousGraph) {
        qc.setQueryData(["graph", projectId], context.previousGraph);
      }
      setAliasModalDraft(context?.previousArticle?.aliases?.slice(1) ?? manualAliasList);
    },
    onSuccess: (response) => {
      setAliasModalDraft(response.aliases.slice(1));
      qc.setQueryData<Article>(["article", articleId], (current) =>
        current ? { ...current, aliases: response.aliases } : current
      );
      qc.setQueryData<GraphPayload>(["graph", projectId], (current) =>
        current
          ? {
              ...current,
              nodes: current.nodes.map((node) =>
                node.id === articleId ? { ...node, aliases: response.aliases } : node
              ),
            }
          : current
      );
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["article", articleId] });
      qc.invalidateQueries({ queryKey: ["graph", projectId] });
    },
  });

  const tocBlocks = useMemo(
    () => headingBlocks.filter((block) => looksLikeRealHeading(block.content)),
    [headingBlocks]
  );

  useEffect(() => {
    if (tocBlocks.length === 0) return;

    let frame = 0;
    const scrollParent = getScrollParent(pageRef.current);

    const updateActiveHeading = () => {
      frame = 0;
      const containerTop = scrollParent instanceof Window ? 0 : scrollParent.getBoundingClientRect().top;
      const threshold = containerTop + 160;
      let nextActiveId = tocBlocks[0]?.id ?? null;
      const isNearBottom =
        scrollParent instanceof Window
          ? window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 24
          : scrollParent.scrollTop + scrollParent.clientHeight >= scrollParent.scrollHeight - 24;

      if (isNearBottom) {
        nextActiveId = tocBlocks[tocBlocks.length - 1]?.id ?? nextActiveId;
      } else {
        for (const block of tocBlocks) {
          const node = document.getElementById(`block-${block.id}`);
          if (!node) continue;

          const top = node.getBoundingClientRect().top;
          if (top <= threshold) {
            nextActiveId = block.id;
          } else {
            break;
          }
        }
      }

      setActiveHeadingId((current) => (current === nextActiveId ? current : nextActiveId));
    };

    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(updateActiveHeading);
    };

    updateActiveHeading();
    scrollParent.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);

    return () => {
      if (frame) {
        cancelAnimationFrame(frame);
      }
      scrollParent.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [tocBlocks]);

  function saveAliases(nextAliases: string[]) {
    updateAliases.mutate(nextAliases);
  }

  function addAliasToModal() {
    const value = aliasDraft.trim();
    if (!value) return;
    const exists =
      titleAlias.toLowerCase() === value.toLowerCase() ||
      aliasModalDraft.some((alias) => alias.toLowerCase() === value.toLowerCase());
    if (exists) {
      setAliasDraft("");
      return;
    }
    saveAliases([...aliasModalDraft, value]);
  }

  function removeAliasFromModal(value: string) {
    saveAliases(aliasModalDraft.filter((alias) => alias !== value));
  }

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

  const flatBlocks: StructuralBlock[] = [];
  const walk = (items: StructuralBlock[]) => {
    for (const item of items) {
      flatBlocks.push(item);
      walk(item.children);
    }
  };
  walk(structuralBlocks);

  const blockById = new Map(flatBlocks.map((block) => [block.id, block]));
  const breadcrumb: StructuralBlock[] = [];
  let cursor = article.structural_block_id ? blockById.get(article.structural_block_id) : undefined;
  while (cursor) {
    breadcrumb.unshift(cursor);
    cursor = cursor.parent_id ? blockById.get(cursor.parent_id) : undefined;
  }

  return (
    <div ref={pageRef} className="max-w-7xl mx-auto px-8 py-12 grid gap-10 lg:grid-cols-[minmax(0,1fr)_280px]">
      {/* Header */}
      <div>
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-10"
        >
          {breadcrumb.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-xs font-mono uppercase tracking-widest text-vault-muted mb-3">
              {breadcrumb.map((item, index) => (
                <span key={item.id} className="inline-flex items-center gap-2">
                  {index > 0 && <span className="text-vault-border">/</span>}
                  <Link href={`/projects/${projectId}/graph`} className="hover:text-vault-gold transition-colors">
                    {item.name}
                  </Link>
                </span>
              ))}
            </div>
          )}
          {article.suggested_section && (
            <p className="text-xs font-mono uppercase tracking-widest text-vault-gold mb-3">
              {article.suggested_section}
            </p>
          )}
          <h1 className="text-display text-4xl font-semibold text-vault-text leading-tight mb-4 max-w-5xl">
            {article.title}
          </h1>
          {article.description && (
            <p className="text-vault-muted leading-relaxed text-[15px] max-w-2xl mb-4">
              {article.description}
            </p>
          )}
          <div className="flex items-center gap-4 text-xs text-vault-muted font-mono">
            <span>{displayBlockCount} blocks</span>
            {article.kind === "node" && (
              <>
                <span className="w-1 h-1 rounded-full bg-vault-border" />
                <span className="px-2 py-0.5 rounded-full border border-vault-gold/30 text-vault-gold bg-vault-gold/10">
                  node
                </span>
              </>
            )}
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

        <div className="space-y-3">
          {visibleBlocks.map((block, i) => (
            <div key={block.id} id={`block-${block.id}`} data-block-id={block.id}>
              <Block
                block={block}
                index={i}
                projectId={projectId}
                linkTargets={linkTargets}
                onHoverCitation={setHoveredCitation}
                onLeaveCitation={() => setHoveredCitation((current) => (current?.id === block.id ? null : current))}
              />
            </div>
          ))}
        </div>
      </div>

      {(tocBlocks.length > 0 || aliasList.length > 0) && (
        <aside className="hidden lg:block">
          <div className="sticky top-8 space-y-4">
            {tocBlocks.length > 0 && (
              <div className="rounded-xl border border-vault-border bg-vault-surface p-4">
                <p className="text-xs font-mono uppercase tracking-widest text-vault-gold mb-3">
                  On this page
                </p>
                <div className="space-y-2">
                  {tocBlocks.map((block) => (
                    (() => {
                      const headingMeta = parseHeadingLabel(block.content);
                      return (
                  <a
                    key={block.id}
                    href={`#block-${block.id}`}
                    onClick={(event) => {
                      event.preventDefault();
                      setActiveHeadingId(block.id);
                      document.getElementById(`block-${block.id}`)?.scrollIntoView({
                        behavior: "smooth",
                        block: "start",
                      });
                    }}
                    className={cn(
                      "block text-sm transition-colors",
                      headingMeta.depth === 1 && "font-semibold",
                      headingMeta.depth === 2 && "pl-2 font-medium",
                      headingMeta.depth >= 3 && "pl-5 text-[13px]",
                      activeHeadingId === block.id
                        ? "text-vault-gold"
                        : "text-vault-muted hover:text-vault-text"
                    )}
                  >
                    {headingMeta.label}
                  </a>
                      );
                    })()
                  ))}
                </div>
              </div>
            )}
            <div className="rounded-xl border border-vault-border bg-vault-surface p-4 space-y-3">
              <p className="text-[11px] font-mono uppercase tracking-[0.22em] text-vault-gold/70">
                Links
              </p>
              <button
                onClick={() => {
                  setAliasModalDraft(manualAliasList);
                  setAliasDraft("");
                  setAliasModalOpen(true);
                }}
                className="w-full rounded-lg border border-vault-border bg-vault-bg px-3 py-2 text-left text-sm text-vault-text hover:border-vault-gold/40 transition-colors"
              >
                Manage aliases
              </button>
              <p className="text-xs leading-5 text-vault-muted">
                Mentions of saved aliases inside article text become clickable links.
              </p>
            </div>
          </div>
        </aside>
      )}

      {hoveredCitation && (
        <div className="hidden lg:block pointer-events-none fixed bottom-6 right-8 z-20 max-w-[360px]">
          <div className="space-y-1.5 text-xs font-mono text-vault-muted/90 leading-5 transition-all duration-200">
            {hoveredCitation.pageNumber && (
              <p className="flex items-center gap-2">
                <FileText size={11} className="shrink-0 text-vault-gold/70" />
                <span>p.{hoveredCitation.pageNumber}</span>
              </p>
            )}
            {hoveredCitation.sectionPath && (
              <p className="flex items-start gap-2">
                <MapPin size={11} className="mt-0.5 shrink-0 text-vault-gold/70" />
                <span>{hoveredCitation.sectionPath}</span>
              </p>
            )}
          </div>
        </div>
      )}

      {aliasModalOpen && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/55 px-6">
          <div className="w-full max-w-xl rounded-2xl border border-vault-border bg-vault-surface p-6 shadow-[0_24px_80px_rgba(0,0,0,0.35)]">
            <div className="flex items-start justify-between gap-4 mb-5">
              <div>
                <p className="text-[11px] font-mono uppercase tracking-[0.22em] text-vault-gold/70 mb-2">
                  Aliases
                </p>
                <h3 className="text-xl font-semibold text-vault-text">{article.title}</h3>
              </div>
              <button
                onClick={() => setAliasModalOpen(false)}
                className="rounded-md border border-vault-border px-3 py-1.5 text-sm text-vault-muted hover:text-vault-text transition-colors"
              >
                Close
              </button>
            </div>

            <div className="space-y-4">
              <div className="flex flex-wrap gap-2">
                {titleAlias && (
                  <div className="inline-flex items-center gap-2 rounded-full border border-vault-border px-3 py-1.5 text-sm text-vault-text">
                    {titleAlias}
                    <span className="text-[10px] font-mono uppercase tracking-[0.18em] text-vault-gold/70">
                      title
                    </span>
                  </div>
                )}
                {aliasModalDraft.map((alias) => (
                  <div
                    key={alias}
                    className="inline-flex items-center gap-2 rounded-full border border-vault-border px-3 py-1.5 text-sm text-vault-text"
                  >
                    {alias}
                    <button
                      onClick={() => removeAliasFromModal(alias)}
                      className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] text-vault-muted hover:bg-vault-gold/10 hover:text-vault-gold transition-colors"
                      title="Remove alias"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>

              <div className="flex gap-2">
                <input
                  value={aliasDraft}
                  onChange={(event) => setAliasDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      addAliasToModal();
                    }
                  }}
                  placeholder="Add alias"
                  className="w-full rounded-md border border-vault-border bg-vault-bg px-3 py-2 text-sm text-vault-text outline-none placeholder:text-vault-muted"
                />
                <button
                  onClick={addAliasToModal}
                  disabled={!aliasDraft.trim() || updateAliases.isPending}
                  className="rounded-md bg-vault-gold px-4 py-2 text-sm font-medium text-vault-bg disabled:opacity-50"
                >
                  {updateAliases.isPending ? "Saving..." : "Add"}
                </button>
              </div>

              <p className="pt-2 text-xs leading-5 text-vault-muted">
                The first alias is the article title and stays fixed. Everything else can be removed and controls inline article hyperlinks.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
