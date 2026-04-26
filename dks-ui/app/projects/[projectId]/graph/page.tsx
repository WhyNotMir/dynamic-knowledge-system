"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { StructuralBlock } from "@/lib/types";

type TopicNodeData = {
  label: string;
  section: string;
  kind: "article" | "node";
};

function StructuralBlockTree({
  block,
  depth = 0,
  articleCount,
  activeBlockId,
  onSelect,
  onBeginRename,
  onBeginCreateChild,
  onDelete,
}: {
  block: StructuralBlock;
  depth?: number;
  articleCount: number;
  activeBlockId: string | null;
  onSelect: (blockId: string | null) => void;
  onBeginRename: (block: StructuralBlock) => void;
  onBeginCreateChild: (block: StructuralBlock) => void;
  onDelete: (blockId: string) => void;
}) {
  const [expanded, setExpanded] = useState(depth < 1);
  const hasChildren = block.children.length > 0;

  return (
    <div className="space-y-2">
      <div
        className={cn(
          "rounded-xl border px-3 py-3 transition-colors",
          activeBlockId === block.id
            ? "border-vault-gold/40 bg-vault-gold/10"
            : "border-vault-border bg-vault-bg"
        )}
        style={{ marginLeft: Math.min(depth * 14, 28) }}
      >
        <div className="flex items-start justify-between gap-3">
          <button
            type="button"
            onClick={() => onSelect(activeBlockId === block.id ? null : block.id)}
            className="min-w-0 text-left"
          >
            <p className="text-sm text-vault-text">{block.name}</p>
            <p className="mt-1 text-xs text-vault-muted leading-5">
              {articleCount} article{articleCount !== 1 ? "s" : ""}
              {block.description ? ` • ${block.description}` : ""}
            </p>
          </button>
          <div className="flex items-center gap-2 text-[11px] font-mono uppercase tracking-[0.18em]">
            <button
              onClick={() => onBeginCreateChild(block)}
              className="text-vault-muted hover:text-vault-gold transition-colors"
            >
              Child
            </button>
            <button
              onClick={() => onBeginRename(block)}
              className="text-vault-muted hover:text-vault-gold transition-colors"
            >
              Rename
            </button>
            <button
              onClick={() => onDelete(block.id)}
              className="text-vault-muted hover:text-vault-error transition-colors"
            >
              Delete
            </button>
          </div>
        </div>
        {hasChildren && (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="mt-3 text-[11px] font-mono uppercase tracking-[0.18em] text-vault-muted hover:text-vault-text"
          >
            {expanded ? "Hide" : `Children (${block.children.length})`}
          </button>
        )}
      </div>
      {expanded &&
        block.children.map((child) => (
          <StructuralBlockTree
            key={child.id}
            block={child}
            depth={depth + 1}
            articleCount={articleCount}
            activeBlockId={activeBlockId}
            onSelect={onSelect}
            onBeginRename={onBeginRename}
            onBeginCreateChild={onBeginCreateChild}
            onDelete={onDelete}
          />
        ))}
    </div>
  );
}

function countArticlesInBranch(block: StructuralBlock, counts: Map<string, number>): number {
  return (counts.get(block.id) ?? 0) + block.children.reduce(
    (acc, child) => acc + countArticlesInBranch(child, counts),
    0
  );
}

function TopicNode({ data }: NodeProps<Node<TopicNodeData>>) {
  return (
    <div className="relative min-w-[240px] max-w-[240px] rounded-2xl border border-vault-border bg-[#111113]/96 px-4 py-3 shadow-[0_16px_40px_rgba(0,0,0,0.18)] backdrop-blur">
      <Handle
        type="target"
        position={Position.Top}
        className="!h-2.5 !w-2.5 !border-2 !border-vault-bg !bg-vault-gold/80"
      />
      <p className="mb-2 text-[10px] font-mono uppercase tracking-[0.24em] text-vault-gold/65">
        {data.section}
      </p>
      <p className="text-[15px] font-medium leading-6 text-vault-text">{data.label}</p>
      {data.kind === "node" && (
        <span className="mt-3 inline-flex rounded-full border border-vault-gold/20 bg-vault-gold/8 px-2 py-1 text-[10px] font-mono uppercase tracking-[0.2em] text-vault-gold/80">
          Node
        </span>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        className="!h-2.5 !w-2.5 !border-2 !border-vault-bg !bg-vault-gold/80"
      />
    </div>
  );
}

const nodeTypes = {
  topic: TopicNode,
};

export default function GraphPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const qc = useQueryClient();
  const layoutStorageKey = `dks:graph-layout:${projectId}`;
  const [newBlock, setNewBlock] = useState("");
  const [newBlockParentId, setNewBlockParentId] = useState<string | null>(null);
  const [editingBlockId, setEditingBlockId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [query, setQuery] = useState("");
  const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<TopicNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [layoutVersion, setLayoutVersion] = useState("");

  const { data: articles = [] } = useQuery({
    queryKey: ["articles", projectId],
    queryFn: () => api.articles.list(projectId),
  });
  const { data: graph } = useQuery({
    queryKey: ["graph", projectId],
    queryFn: () => api.graph.get(projectId),
  });
  const { data: structuralBlocks = [] } = useQuery({
    queryKey: ["structural-blocks", projectId],
    queryFn: () => api.structuralBlocks.list(projectId),
  });

  const articleCountByBlock = useMemo(() => {
    const counts = new Map<string, number>();
    for (const article of articles) {
      if (!article.structural_block_id) continue;
      counts.set(article.structural_block_id, (counts.get(article.structural_block_id) ?? 0) + 1);
    }
    return counts;
  }, [articles]);

  const createBlock = useMutation({
    mutationFn: () =>
      api.structuralBlocks.create(projectId, {
        name: newBlock,
        parent_id: newBlockParentId,
      }),
    onSuccess: () => {
      setNewBlock("");
      setNewBlockParentId(null);
      qc.invalidateQueries({ queryKey: ["structural-blocks", projectId] });
      qc.invalidateQueries({ queryKey: ["articles", projectId] });
      qc.invalidateQueries({ queryKey: ["graph", projectId] });
    },
  });

  const removeBlock = useMutation({
    mutationFn: (blockId: string) => api.structuralBlocks.remove(projectId, blockId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["structural-blocks", projectId] });
      qc.invalidateQueries({ queryKey: ["articles", projectId] });
      qc.invalidateQueries({ queryKey: ["graph", projectId] });
    },
  });
  const renameBlock = useMutation({
    mutationFn: (args: { blockId: string; name: string }) =>
      api.structuralBlocks.update(projectId, args.blockId, { name: args.name }),
    onSuccess: () => {
      setEditingBlockId(null);
      setEditingName("");
      qc.invalidateQueries({ queryKey: ["structural-blocks", projectId] });
      qc.invalidateQueries({ queryKey: ["articles", projectId] });
      qc.invalidateQueries({ queryKey: ["graph", projectId] });
    },
  });

  const graphLayout = useMemo(() => {
    const allBlocks: typeof structuralBlocks = [];
    const append = (items: typeof structuralBlocks) => {
      for (const item of items) {
        allBlocks.push(item);
        append(item.children);
      }
    };
    append(structuralBlocks);

    const byParent = new Map<string | null, string[]>();
    for (const block of allBlocks) {
      byParent.set(block.id, []);
    }
    for (const block of allBlocks) {
      const children = byParent.get(block.parent_id ?? null) ?? [];
      children.push(block.id);
      byParent.set(block.parent_id ?? null, children);
    }

    const sectionColors: Record<string, string> = {};
    const palette = ["#D4A853", "#6B9FD4", "#8BB56B", "#C47DC7", "#D47B6B"];
    let ci = 0;

    const blockIdsToInclude = new Set<string>();
    const collectBlockIds = (items: StructuralBlock[]) => {
      for (const item of items) {
        if (item.id === selectedBlockId) {
          blockIdsToInclude.add(item.id);
          const walk = (node: StructuralBlock) => {
            for (const child of node.children) {
              blockIdsToInclude.add(child.id);
              walk(child);
            }
          };
          walk(item);
        } else {
          collectBlockIds(item.children);
        }
      }
    };
    if (selectedBlockId) {
      collectBlockIds(structuralBlocks);
    }

    const filteredArticles = articles.filter((article) => {
      const matchesQuery =
        !query.trim() ||
        article.title.toLowerCase().includes(query.trim().toLowerCase());
      const matchesBlock =
        !selectedBlockId ||
        (article.structural_block_id ? blockIdsToInclude.has(article.structural_block_id) : false);
      return matchesQuery && matchesBlock;
    });

    const bySection = new Map<string, typeof filteredArticles>();
    for (const article of filteredArticles) {
      const section = article.suggested_section ?? "Ungrouped";
      const group = bySection.get(section) ?? [];
      group.push(article);
      bySection.set(section, group);
    }

    const sections = [...bySection.entries()].sort((left, right) => left[0].localeCompare(right[0]));
    const nextNodes: Node<TopicNodeData>[] = [];
    sections.forEach(([section, group], sectionIndex) => {
      if (!sectionColors[section]) {
        sectionColors[section] = palette[ci++ % palette.length];
      }
      group.forEach((a, rowIndex) => {
        nextNodes.push({
          id: a.id,
          type: "topic",
          position: { x: sectionIndex * 340, y: rowIndex * 170 },
          data: {
            label: a.title,
            section,
            kind: (a.kind ?? "article") as "article" | "node",
          },
        });
      });
    });

    const visibleNodeIds = new Set(nextNodes.map((node) => node.id));
    const nextEdges: Edge[] = (graph?.edges ?? [])
      .filter((edge) => edge.kind === "hard")
      .filter((edge) => visibleNodeIds.has(edge.from_article_id) && visibleNodeIds.has(edge.to_article_id))
      .map((edge) => ({
        id: edge.id,
        source: edge.from_article_id,
        target: edge.to_article_id,
        animated: false,
        type: "smoothstep",
        style: {
          stroke: "#D4A853",
          strokeWidth: 1.4,
          opacity: 0.45,
        },
      }));

    return { nodes: nextNodes, edges: nextEdges };
  }, [articles, graph?.edges, query, selectedBlockId, structuralBlocks]);

  const nextLayoutVersion = useMemo(
    () =>
      JSON.stringify({
        nodeIds: graphLayout.nodes.map((node) => node.id),
        nodeSections: graphLayout.nodes.map((node) => node.data.section),
        edgeIds: graphLayout.edges.map((edge) => edge.id),
      }),
    [graphLayout.edges, graphLayout.nodes]
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (nodes.length === 0) return;

    const storedPositions = Object.fromEntries(
      nodes.map((node) => [
        node.id,
        {
          x: node.position.x,
          y: node.position.y,
        },
      ])
    );
    window.localStorage.setItem(layoutStorageKey, JSON.stringify(storedPositions));
  }, [layoutStorageKey, nodes]);

  useEffect(() => {
    if (layoutVersion === nextLayoutVersion) return;

    let savedPositions = new Map<string, { x: number; y: number }>();
    if (typeof window !== "undefined") {
      try {
        const raw = window.localStorage.getItem(layoutStorageKey);
        if (raw) {
          const parsed = JSON.parse(raw) as Record<string, { x: number; y: number }>;
          savedPositions = new Map(Object.entries(parsed));
        }
      } catch {
        savedPositions = new Map();
      }
    }

    setNodes((current) => {
      const previousById = new Map(current.map((node) => [node.id, node]));
      return graphLayout.nodes.map((node) => {
        const saved = savedPositions.get(node.id);
        const previous = previousById.get(node.id);
        if (saved) {
          return {
            ...node,
            position: saved,
          };
        }
        return previous
          ? {
              ...node,
              position: previous.position,
            }
          : node;
      });
    });
    setEdges(graphLayout.edges);
    setLayoutVersion(nextLayoutVersion);
  }, [graphLayout.edges, graphLayout.nodes, layoutStorageKey, layoutVersion, nextLayoutVersion, setEdges, setNodes]);

  function resetLayout() {
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(layoutStorageKey);
    }
    setNodes(graphLayout.nodes);
    setEdges(graphLayout.edges);
    setLayoutVersion(nextLayoutVersion);
  }

  return (
    <div className="grid h-full lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="border-r border-vault-border bg-vault-surface p-5 overflow-y-auto">
        <p className="text-xs font-mono uppercase tracking-widest text-vault-gold mb-3">
          Structural Blocks
        </p>
        <div className="flex gap-2 mb-4">
          <Input
            value={newBlock}
            onChange={(e) => setNewBlock(e.target.value)}
            placeholder={newBlockParentId ? "Child structural block" : "Structural block name"}
            className="bg-vault-bg border-vault-border text-vault-text"
          />
          <Button
            onClick={() => createBlock.mutate()}
            disabled={!newBlock.trim() || createBlock.isPending}
            className="bg-vault-gold text-vault-bg hover:bg-vault-gold/90"
          >
            Add
          </Button>
        </div>
        <div className="space-y-2">
          {newBlockParentId && (
            <div className="rounded-lg border border-vault-gold/30 bg-vault-gold/10 px-3 py-2 text-xs text-vault-gold">
              New block will be added inside the selected block.
              <button
                type="button"
                onClick={() => setNewBlockParentId(null)}
                className="ml-2 underline underline-offset-4"
              >
                Clear
              </button>
            </div>
          )}
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search"
            className="bg-vault-bg border-vault-border text-vault-text"
          />
          <Button
            type="button"
            variant="outline"
            onClick={resetLayout}
            className="w-full border-vault-border bg-vault-bg text-vault-text hover:bg-vault-surface hover:text-vault-text"
          >
            Reset layout
          </Button>
          {editingBlockId && (
            <div className="rounded-xl border border-vault-border bg-vault-bg px-3 py-3">
              <p className="mb-2 text-[11px] font-mono uppercase tracking-[0.18em] text-vault-gold/70">
                Rename block
              </p>
              <div className="flex items-center gap-2">
                <Input
                  value={editingName}
                  onChange={(e) => setEditingName(e.target.value)}
                  className="h-9 bg-vault-surface border-vault-border text-vault-text"
                />
                <Button
                  onClick={() => renameBlock.mutate({ blockId: editingBlockId, name: editingName })}
                  disabled={!editingName.trim() || renameBlock.isPending}
                  className="h-9 bg-vault-gold text-vault-bg hover:bg-vault-gold/90"
                >
                  Save
                </Button>
              </div>
            </div>
          )}
          {structuralBlocks.length === 0 ? (
            <div className="rounded-xl border border-dashed border-vault-border bg-vault-bg px-4 py-5 text-sm leading-6 text-vault-muted">
              No blocks yet.
            </div>
          ) : (
            structuralBlocks.map((block) => (
              <StructuralBlockTree
                key={block.id}
                block={block}
                articleCount={countArticlesInBranch(block, articleCountByBlock)}
                activeBlockId={selectedBlockId}
                onSelect={setSelectedBlockId}
                onBeginRename={(value) => {
                  setEditingBlockId(value.id);
                  setEditingName(value.name);
                }}
                onBeginCreateChild={(value) => {
                  setNewBlockParentId(value.id);
                }}
                onDelete={(blockId) => removeBlock.mutate(blockId)}
              />
            ))
          )}
        </div>
      </aside>

      <div style={{ width: "100%", height: "100%" }}>
        {graphLayout.nodes.length === 0 && (
          <div className="pointer-events-none absolute inset-y-0 right-0 left-[300px] z-10 flex items-center justify-center">
            <div className="rounded-2xl border border-vault-border bg-vault-surface/90 px-6 py-5 text-center backdrop-blur">
              <p className="text-[11px] font-mono uppercase tracking-[0.22em] text-vault-gold/70">
                Graph
              </p>
              <p className="mt-3 max-w-sm text-sm leading-6 text-vault-muted">
                {selectedBlockId
                  ? "No articles in this block."
                  : "No articles match the current search."}
              </p>
            </div>
          </div>
        )}
        <ReactFlow
          className="graph-flow"
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          fitView
          proOptions={{ hideAttribution: true }}
          style={{ background: "#09090B" }}
        >
          <Background color="#1C1C1F" gap={24} />
          <Controls
            position="bottom-left"
            style={{
              background: "transparent",
              border: "none",
              borderRadius: 0,
              boxShadow: "none",
            }}
            showInteractive={false}
          />
        </ReactFlow>
        <style jsx global>{`
          .graph-flow .react-flow__controls {
            box-shadow: none !important;
            border: none !important;
            background: transparent !important;
            overflow: visible !important;
            gap: 10px;
            display: flex;
            flex-direction: row;
            left: 18px;
            bottom: 18px;
          }

          .graph-flow .react-flow__controls-button {
            width: 28px;
            height: 28px;
            border: none !important;
            background: transparent !important;
            color: #d4a853 !important;
            box-shadow: none !important;
          }

          .graph-flow .react-flow__controls-button svg {
            fill: currentColor;
          }

          .graph-flow .react-flow__controls-button:hover {
            color: #f0d48a !important;
            background: transparent !important;
          }
        `}</style>
      </div>
    </div>
  );
}
