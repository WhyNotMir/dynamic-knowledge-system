export type SourceType   = "pdf" | "docx" | "url" | "text" | "markdown";
export type SourceStatus = "pending" | "processing" | "done" | "failed";
export type ElementType  =
  | "heading"
  | "paragraph"
  | "list_item"
  | "table"
  | "caption"
  | "quote"
  | "code_block"
  | "image"
  | "footnote"
  | "formula";
export type ProposalStatus   = "pending" | "ready" | "reviewed";
export type CandidateStatus  = "proposed" | "confirmed" | "rejected" | "merged";
export type ArticleStatus    = "draft" | "published" | "outdated" | "deprecated";
export type ArticleKind      = "article" | "node";
export type InboxItemType    = "new_candidates";
export type InboxActionType  =
  | "confirm"
  | "reject"
  | "rename"
  | "view_source"
  | "confirm_all"
  | "build_articles"
  | "merge"
  | "restructure"
  | "promote_node"
  | "mass_reroute";

export interface Project {
  id: string;
  name: string;
  description: string | null;
  scope_hint: string | null;
  summary?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Source {
  id: string;
  project_id: string;
  filename: string;
  title?: string | null;
  source_type: SourceType;
  status: SourceStatus;
  error_message: string | null;
  doc_metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface SourceFragment {
  id: string;
  source_id: string;
  content: string;
  content_hash?: string | null;
  element_type: ElementType;
  heading_level: number | null;
  list_level?: number | null;
  group_id?: string | null;
  page_number: number | null;
  section_path: string | null;
  position_index: number;
  inline_spans?: unknown[] | null;
  meta_json?: Record<string, unknown> | null;
}

export interface ArticleCandidate {
  id: string;
  title: string;
  suggested_section: string | null;
  source_section_path: string | null;
  fragment_ids: string[];
  fragment_count: number;
  internal_headings?: string[];
  status: CandidateStatus;
  confidence: number | null;
  created_at: string;
}

export interface StructureProposal {
  id: string;
  project_id: string;
  kind?: "initial" | "incremental_update";
  status: ProposalStatus;
  candidates: ArticleCandidate[];
  created_at: string;
}

export interface InboxItem {
  id: string;
  item_type: InboxItemType;
  project_id: string;
  proposal_id: string;
  status: ProposalStatus;
  title: string;
  created_at: string;
  gate: {
    requires_human_review: boolean;
    auto_apply_allowed: boolean;
    reason: string;
    available_actions: Array<{
      action: InboxActionType;
      label: string;
      destructive: boolean;
      enabled: boolean;
      requires_human_review: boolean;
      available_now: boolean;
    }>;
    reserved_actions: Array<{
      action: InboxActionType;
      label: string;
      destructive: boolean;
      enabled: boolean;
      requires_human_review: boolean;
      available_now: boolean;
    }>;
  };
  proposal: StructureProposal;
}

export interface ArticleBlock {
  id: string;
  fragment_id: string | null;
  content: string;
  element_type: ElementType;
  position_index: number;
  source_position_index?: number | null;
  page_number: number | null;
  section_path: string | null;
  list_level?: number | null;
  group_id?: string | null;
  inline_spans?: Array<{ start: number; end: number; style: string; data?: unknown }> | null;
  meta_json?: Record<string, unknown> | null;
  synthesized?: boolean;
}

export interface Article {
  id: string;
  project_id: string;
  candidate_id: string | null;
  structural_block_id?: string | null;
  title: string;
  slug?: string;
  kind?: ArticleKind;
  suggested_section: string | null;
  description?: string | null;
  summary?: string | null;
  status: ArticleStatus;
  aliases?: string[] | null;
  revision_count?: number;
  blocks: ArticleBlock[];
  created_at: string;
}

export interface ArticleListItem {
  id: string;
  title: string;
  slug?: string;
  kind?: ArticleKind;
  structural_block_id?: string | null;
  suggested_section: string | null;
  status: ArticleStatus;
  description?: string | null;
  block_count: number;
  created_at: string;
}

export interface StructuralBlock {
  id: string;
  project_id: string;
  parent_id: string | null;
  name: string;
  description?: string | null;
  position_index: number;
  children: StructuralBlock[];
  created_at: string;
  updated_at: string;
}
