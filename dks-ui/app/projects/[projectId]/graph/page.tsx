"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { ReactFlow, Background, Controls, MiniMap, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function GraphPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const qc = useQueryClient();
  const [newBlock, setNewBlock] = useState("");
  const [editingBlockId, setEditingBlockId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");

  const { data: articles = [] } = useQuery({
    queryKey: ["articles", projectId],
    queryFn: () => api.articles.list(projectId),
  });
  const { data: structuralBlocks = [] } = useQuery({
    queryKey: ["structural-blocks", projectId],
    queryFn: () => api.structuralBlocks.list(projectId),
  });

  const createBlock = useMutation({
    mutationFn: () => api.structuralBlocks.create(projectId, { name: newBlock }),
    onSuccess: () => {
      setNewBlock("");
      qc.invalidateQueries({ queryKey: ["structural-blocks", projectId] });
      qc.invalidateQueries({ queryKey: ["articles", projectId] });
    },
  });

  const removeBlock = useMutation({
    mutationFn: (blockId: string) => api.structuralBlocks.remove(projectId, blockId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["structural-blocks", projectId] });
      qc.invalidateQueries({ queryKey: ["articles", projectId] });
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
    },
  });

  const { nodes, edges } = useMemo(() => {
    const sectionColors: Record<string, string> = {};
    const palette = ["#D4A853", "#6B9FD4", "#8BB56B", "#C47DC7", "#D47B6B"];
    let ci = 0;

    const nodes: Node[] = articles.map((a, i) => {
      const sec = a.suggested_section ?? "General";
      if (!sectionColors[sec]) sectionColors[sec] = palette[ci++ % palette.length];
      const angle = (i / articles.length) * 2 * Math.PI;
      const r = 280;
      return {
        id: a.id,
        position: { x: 400 + r * Math.cos(angle), y: 300 + r * Math.sin(angle) },
        data: { label: a.title },
        style: {
          background: "#111113",
          border: `1px solid ${sectionColors[sec]}40`,
          color: "#F5F0E8",
          borderRadius: "6px",
          padding: "8px 14px",
          fontSize: "12px",
          fontFamily: "var(--font-dm-sans)",
          maxWidth: "160px",
          textAlign: "center" as const,
        },
      };
    });

    return { nodes, edges: [] as Edge[] };
  }, [articles]);

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
            placeholder="New block"
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
          {structuralBlocks.map((block) => (
            <div key={block.id} className="rounded-lg border border-vault-border bg-vault-bg px-3 py-2">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm text-vault-text">{block.name}</p>
                  {editingBlockId === block.id ? (
                    <div className="flex items-center gap-2 mt-2">
                      <Input
                        value={editingName}
                        onChange={(e) => setEditingName(e.target.value)}
                        className="h-8 bg-vault-surface border-vault-border text-vault-text"
                      />
                      <Button
                        onClick={() => renameBlock.mutate({ blockId: block.id, name: editingName })}
                        disabled={!editingName.trim() || renameBlock.isPending}
                        className="h-8 bg-vault-gold text-vault-bg hover:bg-vault-gold/90"
                      >
                        Save
                      </Button>
                    </div>
                  ) : null}
                  {block.children.length > 0 && (
                    <p className="text-xs text-vault-muted mt-1">
                      {block.children.map((child) => child.name).join(", ")}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      setEditingBlockId(block.id);
                      setEditingName(block.name);
                    }}
                    className="text-xs font-mono text-vault-muted hover:text-vault-gold transition-colors"
                  >
                    Rename
                  </button>
                  <button
                    onClick={() => removeBlock.mutate(block.id)}
                    className="text-xs font-mono text-vault-muted hover:text-vault-error transition-colors"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </aside>

      <div style={{ width: "100%", height: "100%" }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          style={{ background: "#09090B" }}
        >
          <Background color="#1C1C1F" gap={24} />
          <Controls style={{ background: "#111113", border: "1px solid #1C1C1F" }} />
          <MiniMap
            style={{ background: "#111113", border: "1px solid #1C1C1F" }}
            nodeColor="#D4A853"
          />
        </ReactFlow>
      </div>
    </div>
  );
}
