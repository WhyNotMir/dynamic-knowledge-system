"use client";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useMemo } from "react";
import { ReactFlow, Background, Controls, MiniMap, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "@/lib/api";

export default function GraphPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const { data: articles = [] } = useQuery({
    queryKey: ["articles", projectId],
    queryFn: () => api.articles.list(projectId),
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
  );
}