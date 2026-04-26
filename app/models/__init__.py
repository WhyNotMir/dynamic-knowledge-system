from app.models.project import Project
from app.models.source import Source, SourceStatus, SourceType
from app.models.source_fragment import ElementType, SourceFragment
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    CandidateStatus,
    ProposalKind,
    ProposalStatus,
    StructureProposal,
)
from app.models.article import Article, ArticleBlock, ArticleKind, ArticleStatus
from app.models.user import ProjectMember, ProjectRole, SYSTEM_USER_ID, User
from app.models.structural_block import StructuralBlock
from app.models.graph_edge import EdgeKind, GraphEdge
from app.models.alias import Alias, AliasSource
from app.models.block_multi_source import BlockMultiSource
from app.models.block_citation import BlockCitation, CitationStatus
from app.models.conflict import Conflict, ConflictKind, ConflictStatus
from app.models.anomaly import Anomaly, AnomalyKind, AnomalyStatus, AnomalyTarget
from app.models.revisions import ArticleRevision, BlockRevision, RevisionReason
from app.models.ingestion_event import IngestionEvent, IngestionEventLevel
from app.models.conversation import Conversation, Message, MessageRole
from app.models.langgraph_checkpoint import LangGraphCheckpoint

__all__ = [
    # Core
    "Project",
    "Source",
    "SourceStatus",
    "SourceType",
    "SourceFragment",
    "ElementType",
    "StructureProposal",
    "ProposalStatus",
    "ProposalKind",
    "ArticleCandidate",
    "CandidateStatus",
    "ArticleCandidateFragment",
    "Article",
    "ArticleBlock",
    "ArticleKind",
    "ArticleStatus",
    # Auth skeleton (Phase 5 activates)
    "User",
    "ProjectMember",
    "ProjectRole",
    "SYSTEM_USER_ID",
    # Taxonomy + graph (Phase 1, 3)
    "StructuralBlock",
    "GraphEdge",
    "EdgeKind",
    "Alias",
    "AliasSource",
    # Provenance + merging (Phase 6, 7)
    "BlockMultiSource",
    "BlockCitation",
    "CitationStatus",
    # Integrity (Phase 8)
    "Conflict",
    "ConflictKind",
    "ConflictStatus",
    "Anomaly",
    "AnomalyKind",
    "AnomalyStatus",
    "AnomalyTarget",
    # Versioning + audit (Phase 9, 11)
    "BlockRevision",
    "ArticleRevision",
    "RevisionReason",
    "IngestionEvent",
    "IngestionEventLevel",
    # Q&A (Phase 4)
    "Conversation",
    "Message",
    "MessageRole",
    # LangGraph persistence (Phase 2 hardening)
    "LangGraphCheckpoint",
]
