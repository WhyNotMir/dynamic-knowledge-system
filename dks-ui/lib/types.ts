export type SourceType   = "pdf" | "docx";
export type SourceStatus = "pending" | "processing" | "done" | "failed";
export type ElementType  = "heading" | "paragraph" | "list_item" | "table" | "caption";
export type ProposalStatus   = "pending" | "ready" | "reviewed";
export type CandidateStatus  = "proposed" | "confirmed" | "rejected" | "merged";
export type ArticleStatus    = "draft" | "published";

export interface Project {
  id: string;
  name: string;
  description: string | null;
  scope_hint: string | null;
  created_at: string;
  updated_at: string;
}

export interface Source {
  id: string;
  project_id: string;
  filename: string;
  source_type: SourceType;
  status: SourceStatus;
  error_message: string | null;
  doc_metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface SourceFragment {
  id: string;
  source_id: string;
  project_id: string;
  content: string;
  element_type: ElementType;
  heading_level: number | null;
  page_number: number | null;
  section_path: string | null;
  position_index: number;
  created_at: string;
}

export interface ArticleCandidate {
  id: string;
  title: string;
  suggested_section: string | null;
  source_section_path: string | null;
  fragment_ids: string[];
  fragment_count: number;
  status: CandidateStatus;
  confidence: number | null;
  created_at: string;
}

export interface StructureProposal {
  id: string;
  project_id: string;
  status: ProposalStatus;
  candidates: ArticleCandidate[];
  created_at: string;
}

export interface ArticleBlock {
  id: string;
  fragment_id: string;
  content: string;
  element_type: string;
  position_index: number;
  page_number: number | null;
  section_path: string | null;
}

export interface Article {
  id: string;
  project_id: string;
  candidate_id: string | null;
  title: string;
  suggested_section: string | null;
  status: ArticleStatus;
  blocks: ArticleBlock[];
  created_at: string;
}

export interface ArticleListItem {
  id: string;
  title: string;
  suggested_section: string | null;
  status: ArticleStatus;
  block_count: number;
  created_at: string;
}