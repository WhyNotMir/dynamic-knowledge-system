# DKS — session handoff

> This file is the single source of truth for future sessions. Read it first
> before touching anything. Updated: **2026-04-20**.
>
> **Plan version:** v2 (2026-04-20). The old Phase 1–5 plan (§ 9 in v1) is
> superseded. The new phased path is § 7 below. Core invariants in § 6 are
> non-negotiable and enforced in CI.

---

## 0. TL;DR

DKS is a document-ingestion → article-generation system. User uploads DOCX/PDF,
backend ingests → segments → embeds fragments → an LLM proposes article
"candidates" → user confirms them → backend builds `Article` + `ArticleBlock`
rows.

**Where we are:** confirm-before-build flow just landed. Backend and frontend
both respect `CandidateStatus.CONFIRMED` as the gate. Tests green. Dev UX works
end-to-end.

**What's next (in order):** follow the new phased plan in § 7. Immediate
focus is **Phase 0 — Foundation** (data model extensions, agent framework
skeleton, LangChain refactor of `structure_agent.py`, invariant CI tests,
golden fixtures). Phase 0 unblocks everything else, especially Phase 1
(Wiki shape: multi-topic, Structural Blocks, Knowledge Nodes, rich blocks,
inline markup, auto-TOC).

---

## 1. Stack & architecture

### Backend
- FastAPI (async)
- SQLAlchemy 2.0 async + `asyncpg`
- PostgreSQL + `pgvector` extension
- `arq` worker on Redis for background jobs
- `python-docx` + `PyMuPDF` (fitz) for extraction
- Groq API for LLM (title/section generation), with `_fallback_title` on rate-limit
- `tenacity` for retries
- `loguru` for logging
- Pydantic v2 schemas (`from_attributes=True`, `computed_field`, `Field(exclude=True)`)

### Frontend (`dks-ui/`)
- Next.js 16 (App Router) — **Turbopack is default**; prone to `aggregation_update.rs` panics, can be switched off with `next dev --webpack`
- TanStack React Query v5
- Tailwind CSS + custom `vault-*` design tokens
- `framer-motion` (AnimatePresence for collapsibles)
- `sonner` for toasts
- `lucide-react` for icons
- `radix-ui` for primitives, shadcn-style `@/components/ui/*`

### Infra
- `docker compose` with `ui`, `api`, `worker`, `db`, `redis` services
- Alembic migrations

---

## 2. Data model

### Core tables / models

```
Project
  └─ Source (one doc — docx/pdf)
       └─ SourceFragment (heading/paragraph/list_item/table/caption)
            └─ embedding (vector)

Project
  └─ StructureProposal (PENDING → READY → REVIEWED)
       └─ ArticleCandidate (PROPOSED → CONFIRMED / REJECTED / MERGED)
            └─ ArticleCandidateFragment  (join: candidate ↔ fragment,
                                          has position_index for order)

Project
  └─ Article (has candidate_id back-pointer, title, status=draft, …)
       └─ ArticleBlock (element_type, content, position_index,
                        fragment_id ← origin)
```

### State machines
- `ProposalStatus`: `PENDING → READY → REVIEWED`
  - `REVIEWED` set when `/articles/build` completes, prevents re-building.
- `CandidateStatus`: `PROPOSED → CONFIRMED | REJECTED | MERGED`
  - `MERGED` is reserved for future cross-proposal dedupe (not wired yet).

### Pipeline

```
POST /projects                              → create project
POST /projects/{id}/sources  (multipart)    → upload doc, enqueue ingest
GET  /projects/{id}/sources                 → poll until status=done
POST /projects/{id}/structure/propose       → enqueue proposer
GET  /projects/{id}/structure/proposals/{p} → poll until status=ready
PATCH /projects/{id}/structure/candidates/{c} → rename / confirm / reject
POST /projects/{id}/structure/proposals/{p}/confirm-all
                                            → bulk-confirm proposed → confirmed
POST /projects/{id}/articles/build          → materialise CONFIRMED candidates
GET  /projects/{id}/articles                → list with block_count, doc order
GET  /projects/{id}/articles/{aid}          → detail with ordered blocks
```

---

## 3. Repo layout (files that matter)

### Backend
- `app/api/projects.py`, `app/api/sources.py`, `app/api/structure.py`, `app/api/articles.py` — REST endpoints
- `app/domain/ingestion/extractor.py` — DOCX + PDF extraction, heading classification
- `app/domain/ingestion/segmentor.py`, `app/domain/ingestion/embedding_service.py`
- `app/domain/ingestion/ingestion_service.py` — orchestrates upload → fragments
- `app/domain/clustering/center_detector.py` — groups fragments into candidates (section_path first, KMeans on embeddings for orphans)
- `app/domain/articles/structure_service.py` — LLM pass that names candidates (Groq)
- `app/domain/articles/article_builder.py` — materialises confirmed candidates into articles
- `app/models/*` — SQLAlchemy models
- `app/schemas/*` — Pydantic schemas (note: `structure.py` uses `computed_field` + `Field(exclude=True)` pattern — see § 4 "0 fragments" fix)
- `app/workers/ingestion_worker.py` — arq `WorkerSettings` with `ingest_source` + `propose_structure`

### Frontend (`dks-ui/`)
- `app/projects/[projectId]/layout.tsx` — collapsible sidebar with auto-expand on active child
- `app/projects/[projectId]/review/page.tsx` — proposals + candidates + Accept All + Build Articles
- `app/projects/[projectId]/articles/page.tsx` — articles list (block counts, doc order)
- `app/projects/[projectId]/articles/[articleId]/page.tsx` — article detail
- `lib/api.ts` — fetch wrappers, all keyed on `queryKey: [..., projectId]`
- `lib/types.ts` — TypeScript mirrors of backend schemas

### Tests
- `tests/helpers.py` — `make_docx`, `unique_docx`, `confirm_all_candidates`
- `tests/conftest.py` — `client`, `session_factory`, `project` fixtures
- `tests/test_pipeline_articles_build.py` — pipeline + confirmation gate tests
- `tests/test_ui_flow.py` — end-to-end happy path mirroring the UI
- `tests/test_pipeline_errors.py` — failure modes

---

## 4. Work completed in this session

Chronological, keyed by the change's centre of gravity.

### 4.1 "0 fragments" display bug (Pydantic v2)
- Candidates were showing `0 fragments` in the UI even when they had fragments.
- Root cause: `candidate_fragments` was read in a `@computed_field` but never
  declared as a model field, so Pydantic v2 under `from_attributes=True`
  silently dropped it before the computed field ran.
- Fix (`app/schemas/structure.py`): declare it as a field with `exclude=True`
  so Pydantic loads it from the ORM but doesn't emit it in JSON.

```python
candidate_fragments: list[Any] = Field(default_factory=list, exclude=True)
model_config = {"from_attributes": True}

@computed_field
@property
def fragment_ids(self) -> list[uuid.UUID]:
    sorted_items = sorted(self.candidate_fragments, key=lambda i: i.position_index)
    return [i.fragment_id for i in sorted_items]

@computed_field
@property
def fragment_count(self) -> int:
    return len(self.candidate_fragments)
```

### 4.2 Document-order sorting
**Articles list** (`app/api/articles.py`): articles now ordered by the
earliest `SourceFragment.position_index` across their blocks (NULLS LAST),
tiebreak by `created_at`. Uses `func.min(SourceFragment.position_index)`
with `outerjoin` on `ArticleBlock` → `SourceFragment` + `group_by(Article.id)`.

**Review page** (`app/api/structure.py`): `_candidate_first_position` helper
+ `_sort_candidates_by_document_order` mutate the proposal in place before
serialising. Both list + detail endpoints use `selectinload(...fragment)`
so `position_index` is already loaded.

### 4.3 Collapsible UI
- `app/projects/[projectId]/layout.tsx` — sidebar sections collapse via
  `useState` + `AnimatePresence`; auto-expands when an active child route
  lives inside it.
- `app/projects/[projectId]/review/page.tsx` — new `ReviewSection` component
  wraps each group (by `suggested_section`) in a chevron-toggle.

### 4.4 Confirmation gate (**behaviour change**)

Previously: "Accept ✓" button on a candidate did nothing functionally —
the builder only filtered out `REJECTED`, so `PROPOSED` and `CONFIRMED`
both got materialised. User feedback: *"такое чувство что ничего"*.

Changed to **Option A** (strict allow-list):
- `app/domain/articles/article_builder.py` — filter changed from
  `if candidate.status == REJECTED: continue` to
  `if candidate.status != CONFIRMED: continue`.
- New bulk endpoint: `POST /projects/{id}/structure/proposals/{pid}/confirm-all`
  flips all `PROPOSED` → `CONFIRMED` in one `UPDATE`, returns
  `{confirmed_count, total_count}`. Idempotent, safe to re-run.
- New schema: `ConfirmAllResponse`.
- Frontend: `Accept All (N)` button (shows when `proposedCount > 0`) +
  `Build Articles (N)` disabled when `confirmedCount === 0`.
- Tests: added `test_build_skips_unconfirmed_candidates`,
  `test_build_only_materialises_confirmed`,
  `test_confirm_all_flips_only_proposed`,
  `test_confirm_all_unknown_proposal_returns_404`.
- `_prepare_ready_proposal` in existing tests now calls
  `confirm_all_candidates(...)` before `/articles/build` so happy-path
  tests still assert `count >= 2`.
- Test helper `confirm_all_candidates` lives in `tests/helpers.py`.

### 4.5 Build-button-disappears bug

Repro: confirm 2/20 candidates → Build Articles → click "New Proposal" →
button never re-appears.

Root cause: `build.onSuccess` only invalidated `["articles"]`, not
`["proposals"]`, so the old `READY` proposal stayed cached. Meanwhile the
`refetchInterval` polling logic stopped polling for the new `PENDING`
proposal because the cached top-of-list said `REVIEWED`.

Fix in `dks-ui/app/projects/[projectId]/review/page.tsx`:
- `build.onSuccess`: also call `qc.invalidateQueries({queryKey: ["proposals", projectId]})`.
- `useQuery` for proposals: add `refetchOnMount: "always"` so returning to
  `/review` always re-hydrates from the API.

Three-line fix, nothing else touched.

---

## 5. Known issues / parked work

### 5.1 Duplicate candidate titles + non-sequential order (parked)
User flagged (2026-04-18) that built articles sometimes have duplicate
titles and appear out of document order. Three hypotheses, not yet
investigated:
1. LLM hallucinates the same title across semantically-similar clusters.
   → Fix: de-dupe titles before save, or feed the LLM neighbouring titles
     as "avoid these".
2. The proposer tries to be "thematic" and fragments one logical article
   into N sub-topics with overlapping titles.
   → Fix: see § 6, multi-topic articles.
3. Ordering issue is downstream of (1)/(2) — once duplicates exist,
   grouping them by title breaks document order.

Parked per user: *"хорошо оставим это на потом"*.

### 5.2 Turbopack panics in `next dev`
`aggregation_update.rs:1864` panics (`inner_of_upper_lost_followers ...
don't exist as upper or follower edges`) in `dks-ui` dev server. Triage:
1. `rm -rf dks-ui/.next` and restart the container.
2. If it recurs, switch to webpack: `"dev": "next dev --webpack"` in
   `dks-ui/package.json`.
3. Try `npm install next@latest` — such bugs are fixed regularly.

### 5.3 `MERGED` candidate status unused
Enum value exists in `app/models/article_candidate.py` but no code paths
set or consume it. Reserved for future cross-proposal dedupe (see § 9.4.6).

---

## 6. Core product invariants

Two rules that are never violated in any phase. Enforced in CI.

### 6.1 Source document order preservation

Within each source, blocks appear in the same order as in the original
document. Monotonicity is checked as follows: for each `(source_id,
article_id)` pair, the blocks from that source in that article must have
a **monotonically increasing** `source_position_index`. Between different
sources blocks may interleave, but only by an explicit decision of the
Routing / Placement agent.

CI test: `test_source_order_monotonic` — after any build, for every
`(source_id, article_id)` pair, `source_position_index` values are
strictly non-decreasing.

### 6.2 Content immutability

`ArticleBlock.content` equals `SourceFragment.content` 1:1. No
LLM-rewriting of content. The **single exception** is `SynthesisAgent`
during merge of two duplicates, under a strict contract: no new facts,
only connective tissue (bridging phrases) for readability, with mandatory
verification by `CitationVerifier`.

CI test: `test_content_immutability` — for every non-synthesized block,
`block.content == block.fragment.content`. Synthesized blocks are flagged
separately and run through verifier assertions.

---

## 7. Phased delivery plan (v2, agreed 2026-04-20)

This supersedes the old § 9 plan. The ordering below is what we build
and ship, strictly.

### Phase 0 — Foundation

**Goal:** prepare the data model, agent framework, LangChain refactor,
and invariant tests so that all later phases add logic without rewriting
the base.

**Data model extensions (migrations):**

- `SourceFragment`: `content_hash`, `inline_spans` (array of
  `{start, end, style, data}`).
- `Source.type`: extend enum to `docx | pdf | url | text | markdown`
  (only `docx` / `pdf` active for now).
- `Article`: `kind` (`article | node`), `description`, `summary`,
  `slug` (unique per project), `structural_block_id`, `revision_count`,
  `aliases`; `status` extended to `draft | published | outdated |
  deprecated`.
- `ArticleBlock`: `meta_json`, `list_level`, `group_id`,
  `source_position_index`, `added_at`.
- **New entities:**
  - `StructuralBlock` (`id`, `project_id`, `parent_id`, `name`,
    `description`, `position_index` — tree).
  - `GraphEdge` (`from_article`, `to_article`, `kind: hard | soft`,
    `source_block_id`, `score`).
  - `Alias` (`article_id`, `text`, `confidence`, `source`).
  - `BlockCitation` (`block_id`, `fragment_id`, `confidence`,
    `validated_at`, `verifier_version`, `context`).
  - `Conflict` (`block_a`, `block_b`, `kind`, `explanation`, `status`).
  - `Anomaly` (`target_id`, `kind`, `score`, `status`).
  - `BlockRevision` / `ArticleRevision` (`previous_state`, `reason`,
    `change_summary`, `actor`).
  - `IngestionEvent` (audit).
  - `BlockMultiSource` (block ↔ fragment many-to-many).
  - `Conversation` / `Message` (for Q&A).
  - `User` (`id`, `email`, `name`, `auth_provider_id`, `created_at` —
    skeleton; default `system-user`).
  - `ProjectMember` (`project_id`, `user_id`, `role: owner | editor |
    viewer` — skeleton).

**Agent framework skeleton:**

```
app/agents/
  base.py              protocol, retry, LLM factory
  prompts/             ChatPromptTemplate-s
  chains/              LangChain agents
  tools/               vector_search, hash_dedup, url_fetch, outlier
  graph/               LangGraph StateGraph modules
  ml/                  non-LLM detectors
  state/               TypedDict shared
```

- Rewrite `structure_agent.py` on LangChain: `ChatGroq` +
  `ChatPromptTemplate` + `PydanticOutputParser`. Behaviour 1:1 (retry,
  fallback, per-candidate title + hierarchy).
- LangSmith tracing enabled globally.

**Invariant tests in CI:**

- `test_source_order_monotonic` — see § 6.1.
- `test_content_immutability` — see § 6.2.

**Golden fixtures skeleton:**

- `tests/golden/` with one golden `.docx`, baseline of expected candidates
  and titles.
- Fixture: "run pipeline on golden, compare to baseline".

**Unblocks:** everything else.

---

### Phase 1 — Wiki shape

**Goal:** turn "doc → articles" into IBM-Think-Topics-shaped content with
a Structural Blocks hierarchy, rich blocks, LLM-composed titles, and
internal structure.

**Multi-topic candidates:**
- `center_detector` groups by the top level of `section_path` (H1), not
  by leaf.
- Internal H2/H3 survive in the candidate as structure.
- `ArticleCandidateSchema` gets `internal_headings: list[str]`.
- Review UI renders internal headings under each card.

**Hierarchical Structural Blocks:**
- `StructuralBlock` with `parent_id` — tree of arbitrary depth.
- Manual CRUD in UI: create, rename, move, delete node.
- `StructuralTaxonomyAgent` (LangChain, on-demand) analyses existing
  articles and proposes a Structural Blocks tree. User approves in Inbox.
- `Article.structural_block_id` — reference to any node in the tree (not
  only leaf).
- Breadcrumb navigation on Article Detail.

**Knowledge Nodes:**
- `Article.kind` activated (`article | node`).
- `article` vs `node` threshold is a project setting (`min_blocks`,
  `min_chars`).
- UI badge "Node", filter on articles list.

**TitleAgent (LangChain):**
- Input: H1 hint, `internal_headings`, `fragment_preview`, `project.name`,
  `kb_summary` (empty initially, filled by `KBSummarizer`).
- Output Pydantic:
  `{title, description, suggested_structural_block_id}`.
- Heuristic fallback preserved.

**KBSummarizer (LangChain):**
- Generates ~10-line KB summary from titles + descriptions of all
  articles.
- Runs on-demand and when the base grows by N articles.
- Result lives in `Project.summary` (or dedicated field), passed as
  domain context to other agents.

**Rich blocks (source-driven):**
- `ElementType` extended: `quote`, `code_block`, `image`, `footnote`.
- DOCX extractor: styles (`Quote`, `IntenseQuote`, `Code`), monospace runs.
- PDF extractor: monospace font detection at span level, heuristics for
  quotes (indent + quote marks).
- **Invariant:** extractor does not invent blocks. If the source has
  none, there is none.

**Inline markup:**
- DOCX: pass over runs, save array of `{start, end, style ∈ {bold,
  italic, code, link}, data}`.
- PDF: PyMuPDF spans → detect bold / italic / monospace.
- `meta.inline_spans` on `SourceFragment` and `ArticleBlock`.
- UI renders inline spans via a markdown-subset.

**Structured tables:**
- DOCX: `doc.tables` → `meta.table_data: {rows: [[cell, ...]]}`.
- PDF: `pdfplumber.extract_tables`. Vision-LLM fallback deferred to
  Phase 12.
- UI: real `<table>`.

**Images:**
- DOCX: `inline_shapes` + `part.related_parts` → binary in object
  storage, `meta.image_ref`.
- PDF: `page.get_images()` + `doc.extract_image()` → same.
- Caption/alt empty at this phase (`CaptionAgent` in Phase 12).

**Group detection (for future mid-insertion):**
- Segmentor assigns `group_id` to blocks:
  - Consecutive `list_item`s of the same `list_level`.
  - Phrased sequences ("Step 1/2/3", "Method A/B/C", "Option 1/2").
- Groups stored on `SourceFragment`, carried into `ArticleBlock`.

**Auto-TOC on Article Detail:**
- Sidebar "On this page" from blocks with `element_type = heading`.
- Scrollspy highlighting.

**ArticleBuilder updates:**
- Skip H1 fragment that matches the title hint (don't duplicate title
  as heading block).
- Copy `meta_json`, `inline_spans`, `group_id`, `list_level` from
  fragment to block.
- Set `source_position_index` on block (= `fragment.position_index`).

**Tests:** invariants § 6.1 and § 6.2 stay green. New tests for `group_id`
(group blocks contiguous by `position_index`), and rich blocks (source
→ block structure preserved).

**Unblocks:** visually complete product, but still without links, Q&A,
incremental.

---

### Phase 2 — Orchestration (LangGraph backbone)

**Goal:** pipeline becomes a resumable `StateGraph`. Unified Review Inbox.
HITL gates before destructive ops.

**IngestPipeline StateGraph:**
- Nodes: `extract → segment → embed → hash_dedup_new → cluster →
  route_candidates → execute → link → await_review (interrupt) →
  publish`.
- At this phase Routing and Linker are stubs: Routing always returns
  `create_article`; Linker is a no-op. Real ones arrive in Phases 3 and 7.
- `arq` remains the trigger; LangGraph is the logic inside the job.
- Postgres checkpointer (new LangGraph tables).
- Dead-letter queue: a failing candidate goes to DLQ, the rest proceed.
  Admin can retry.

**Review Inbox:**
- `/projects/{id}/inbox` replaces `/review`.
- Item types (only `new_candidates` active now): `new_candidates`,
  `proposed_merges`, `proposed_restructures`, `flagged_anomalies`,
  `flagged_contradictions`, `stalled_blocks`, `taxonomy_proposals`.
- Per-item actions: `confirm`, `reject`, `rename`, `view-source`,
  `view-reasoning`.
- Bulk ops: `confirm-all` / `reject-all` / send to deep review.

**HITL gates:**
- Always on first run of a project (cold start).
- Always for destructive ops: `merge`, `restructure`, `promote_node`,
  `mass-reroute` (added in Phase 7).
- Auto-apply for low-risk (`create_article`, append-at-end at high
  confidence) — with logging.

**IngestionEvent audit log:**
- Every StateGraph node writes an event.
- Admin trace surfaces in later phases.

**Unblocks:** platform for agents. Any agent becomes a node in the graph,
resumable after worker restart.

---

### Phase 3 — Linker & Knowledge Graph

**Goal:** articles stop being islands. The graph gets hard edges from
inline mentions and soft edges from semantic proximity.

**AliasAgent (LangChain):**
- On article create/update, generates aliases (synonyms, abbreviations,
  related terminology).
- Output Pydantic: `{aliases: [{text, confidence}]}`.
- Stored in `Alias` with `source=auto`.
- UI for manual editing: add / remove / confirm.

**LinkerService (deterministic, not an agent):**
- Builds an Aho–Corasick automaton from all project aliases.
- Scans `content` of each `ArticleBlock` → finds first occurrences of
  other articles.
- Stop-list for common terms.
- Rule: wrap the **first** occurrence of an alias in a block, store in
  `meta.links: [{start, end, article_id, source=hard}]`.
- Persist in `GraphEdge` as hard edges.

**Soft edges:**
- Article-level embedding = mean of block embeddings (or dedicated
  embedding of description + summary).
- For each article, top-K cosine neighbours → soft edges in `GraphEdge`
  (`kind=soft`, `score`).
- Refresh when article changes.

**Graph UI upgrade:**
- Real edges: hard (solid), soft (dashed), toggleable.
- Filter by Structural Block with tree traversal (pick a node → show
  articles under it and its descendants).
- Level-of-detail: when > N nodes, cluster by Structural Block.
- Hover → description tooltip. Click → jump.
- Node search by title.

**Article Detail additions:**
- "Referenced by" — incoming hard edges.
- "Related articles" — top-5 soft edges.
- Inline cross-refs (from `meta.links`) clickable.

**Unblocks:** wiki becomes connected; wiki-graph UX complete.

---

### Phase 4 — Q&A v1 (strict RAG)

**Goal:** user asks a question → gets an answer with clickable citations
and an explicit "don't know" when data is absent.

**Block-level retrieval:**
- Retriever over pgvector: k-NN cosine over embeddings from
  `ArticleBlock.fragment_id → SourceFragment.embedding` (join, no
  duplication).
- Scope: within the project, top-K = 15.
- Post-filter: diversity (max 3 blocks per article).

**QAAgent (LangChain):**
- Chain: retriever → strict-RAG prompt → `ChatGroq` →
  `PydanticOutputParser`.
- Output Pydantic: `{answer, citations: [{block_id, fragment_id,
  verbatim_quote}], confidence, insufficient_context}`.
- If `insufficient_context=true` → response "There is no data about X.
  There is information about Y, Z."

**API:**
- `POST /projects/{id}/ask` with SSE stream.
- `GET /projects/{id}/conversations`, `POST /projects/{id}/conversations/
  {cid}/messages`.

**BlockCitation persistence:**
- Each use of a block in an answer → a record (`status=unvalidated` for
  now).
- Aggregate "most-cited blocks" → badge on block.

**Chat UI:**
- `/projects/{id}/ask` — streaming chat.
- Citations as footnote-refs, click → block in context of article in
  right panel.
- Conversation history in sidebar.

**Calibration:**
- Golden fixtures extended: questions with answer-in-base +
  unanswerable questions.
- `scripts/calibrate_rag.py` sweeps thresholds, finds optimum F1 ("don't
  know" recall vs answer precision).
- Thresholds live in `Project.settings`.

**Unblocks:** headline feature works. Product demo-ready.

---

### Phase 5 — Auth & Multi-tenancy activation

**Goal:** product deployable for real users. Multi-tenancy, sharing,
row-level scoping.

**Auth backend:**
- Google OAuth + email magic links (passwordless via Resend/SendGrid).
  No passwords.
- Sessions: server-side (Redis) or JWT with rotation.
- Endpoints: `/auth/login`, `/auth/callback`, `/auth/logout`,
  `/auth/magic-link`.

**Auth middleware:**
- All API endpoints authenticated.
- Resolve `current_user` from session / JWT.
- 401 if not authenticated.

**Row-level scoping:**
- All Project-scoped resources filtered by
  `ProjectMember(user_id, project_id)`.
- SQLAlchemy-level query filter via middleware or repository pattern.
- Vector search, graph, Q&A — all gated by RBAC.

**Sharing UI:**
- `/projects/{id}/members` — members + roles.
- Invite by email → pending invitation → magic link. On registration
  via link, auto-add to project.
- Remove member, change role (owner only).
- Roles: `owner` (all rights incl. delete), `editor` (upload, edit,
  approve), `viewer` (read + Q&A).

**User dashboard:**
- `/dashboard` — list of own projects (owner or member).
- Profile settings: email, name, connected auth providers.
- Data export / deletion (GDPR hook).

**Migration from system-user:**
- Backfill: every existing Project gets `owner_id = system_user.id`.
- On first real-user registration, ownership transfer via admin (or
  immediately, for personal dev deployments).

**Infra:**
- Staging env on managed hosting (Fly.io / Railway / Render to start).
- Env configs: dev / staging / prod.
- Secrets management (`dotenv-vault` or Vault).

**Unblocks:** real users on staging; early feedback via issues.

---

### Phase 6 — Citation trust

**Goal:** citations stop being decoration. Every claim is actually
supported.

**CitationVerifier (LangChain):**
- Post-process after `QAAgent`.
- For each claim in answer: separate LLM call "Does this claim
  explicitly follow from the cited blocks? Yes/No/Partial".
- Output Pydantic: `{claim, supported, supporting_blocks,
  unsupported_reason?}`.
- Unsupported claims → stripped from answer, or answer regenerated
  without them, or flagged `partially_supported`.
- Verbatim quote in citations must be a substring of one of the
  supporting blocks.

**BlockCitation enrichment:**
- `validated_at`, `confidence`, `verifier_version`.
- UI distinguishes verified vs unverified in chat.

**Provenance UI:**
- On each `ArticleBlock` — "View source" → opens source document on the
  page with the paragraph.
- On citations in Q&A — "View in source".

**Eval metrics (pytest, not full dashboard yet):**
- Citation precision (% validated among all emitted).
- Hallucination rate (% claims flagged unsupported).

**Unblocks:** product trust. Prerequisite for production marketing.

---

### Phase 7 — Incremental integration (living KB)

**Goal:** 2nd, 3rd, N-th document enriches the base rather than creating
duplicates. Core product value — "living wiki".

**DedupService (deterministic):**
- Hash dedup: exact duplicates by `content_hash` → skip, `source_id`
  added to `BlockMultiSource` of the existing block.
- Cosine dedup: per-fragment top-K search over existing blocks.
  - `cosine > high_threshold (~0.95)` → near-duplicate (merge candidate).
  - `merge_threshold < cosine < high_threshold (0.88–0.95)` →
    semantic-duplicate (merge candidate).
  - `< merge_threshold` → new material.
- Thresholds calibrated on golden fixtures.

**RoutingAgent (Stage 1, coarse):**
- Input: compact index of existing articles (`id`, `title`,
  `description`, `structural_block_path`, short `summary`,
  article-embedding similarity vs candidate) + candidate preview
  (~2K chars + `internal_headings`).
- Output Pydantic:
  ```
  action: create_article | create_node | append_pending
        | merge_pending | promote_node | propose_restructure
        | flag_anomaly
  target_article_id: UUID | null
  confidence: float
  reasoning: str
  ```
- ~2–3K tokens, cheap.

**PlacementAgent (Stage 2A, fine, append-only):**
- Called only if Stage 1 = `append_pending`.
- Pre-step retrieval: top-K (~10) blocks of target article by cosine +
  their neighbours by `position_index`.
- Input: new candidate full text + target article TOC (headings +
  section leads) + retrieved top-K blocks full text.
- Output Pydantic:
  ```
  insertion_strategy: end_of_section | extend_group |
                      insert_after_block | insert_before_block
  target_section_id, target_group_id, target_block_id
  confidence, reasoning
  ```
- ~5–10K tokens; invoked ~50–70% of candidates.
- Mid-insertion allowed: LLM sees real text of neighbouring
  methods/params and decides e.g. "this is a new method in list A/B/C,
  `extend_group` position after C".

**SynthesisAgent (Stage 2B, merge-only):**
- Called if Stage 1 = `merge_pending`.
- Pre-step retrieval: top-3 merge candidates in target article.
- Input: new block full text + K target candidates full text + short
  surrounding context.
- Output Pydantic:
  ```
  chosen_target_block_id
  merged_content (subject to invariant § 6.2)
  merged_inline_spans
  source_attribution: [fragment_id_a, fragment_id_b]
  connective_additions: [str]
  confidence, reasoning
  ```
- `CitationVerifier` runs immediately after — checks every fact in
  merged is in A or B. On fail → merge rolls back, both blocks kept.
- `BlockMultiSource` ties merged block to both source fragments.

**Executor (plain Python):**
- Switch on `action`.
- Validate target IDs (exists, belongs to project, not regenerating).
- Apply with invariants § 6.1 and § 6.2 preserved:
  - `end_of_section` / `extend_group` (append at end of group) →
    auto-apply if `confidence > 0.9`.
  - `insert_after_block` / `insert_before_block` / `extend_group with
    reorder` → HITL gate in Inbox with diff-preview "was → will be".
  - `merge_pending` / `promote_node` → always HITL with preview of
    synthesized content.
  - `propose_restructure` → always HITL with before/after plan.
- Pre-check: validate `target_article/section/block` IDs before apply.
  On failure → retry with explicit valid list, then fallback to
  `end_of_section`.

**Cascade invalidator:**
- Any article change (new blocks, merged, reordered) → article marked
  `outdated`.
- Async job: regenerate title / description / summary, recompute
  article-level embedding, refresh soft edges.
- After regen → status back to `published`.
- Linker re-scans only affected blocks for aliases.

**Proposal model extension:**
- `StructureProposal.kind` extended: `initial | incremental_update`.
- For `incremental_update` the Inbox shows a preview: "7 actions: 3
  append, 2 merge, 2 create_article".

**Unblocks:** living knowledge base. Qualitative differentiator vs
plain RAG-over-documents. Core value delivered.

---

### Phase 8 — Trust scanners (anomaly + contradiction)

**Goal:** the base finds its own anomalies and contradictions for user
review.

**OutlierDetector (ML, not LLM):**
- `IsolationForest` on article-level embeddings → off-topic articles.
- Per-article LOF on block embeddings → outlier blocks inside an
  article.
- Per-source: mean embedding of source vs project centroid → off-domain
  documents.
- Records in `Anomaly` table.

**ContradictionJudge (LangChain):**
- `MaintenancePipeline`: for every pair of blocks with `cosine > 0.85`
  and `source_id_a != source_id_b` → LLM pairwise judge.
- Output Pydantic: `{contradicts: bool, kind?, explanation?}`.
- Positives → `Conflict` with status `open`.
- Cache already-checked pairs; re-check only when a block changes.
- Optimisation: priority scan of new blocks after each ingest; full
  base scan weekly.

**MaintenancePipeline StateGraph:**
- Nodes: `refresh_kb_summary → outlier_scan →
  contradiction_pairwise_scan → regenerate_outdated_articles`.
- Trigger: cron (weekly) or "N new documents" (per-project config).

**UI:**
- `/projects/{id}/anomalies` — list with actions (`ignore`,
  `remove_block`, `mark_valid`, `explain`).
- `/projects/{id}/conflicts` — side-by-side pairs, actions
  (`resolve_with_a`, `resolve_with_b`, `edit_one`, `mark_ignored`).

**Unblocks:** integrity layer. Critical for legal / medical / research
use cases.

---

### Phase 9 — Semantic versioning

**Goal:** every change in the base is reversible and traceable.

**BlockRevision / ArticleRevision:**
- Snapshot on every change: previous content/state, reason (`ingest`,
  `synthesis`, `restructure`, `manual`), actor (agent name or
  `user_id`), timestamp.
- Stored in dedicated tables, not stuffed into `meta_json`.

**ChangeSummarizer (LangChain):**
- From revision diff, generates human-readable summary: "Added section X
  based on document Y", "Merged two blocks about Z".
- Async after each revision.

**Diff UI:**
- Article Detail "History" tab: list of revisions with `change_summary`.
- Click → side-by-side diff (block text, structure).
- Rollback action: for a block — any revision; for an article —
  previous full snapshot.

**Activity feed:**
- `/projects/{id}/activity` — chronological feed of project changes.
- Filter by type: `article_created`, `block_merged`,
  `conflict_resolved`, `structure_rebuilt`.

**Unblocks:** transparency of base evolution + ability to undo bad
agent decisions.

---

### Phase 10 — Advanced Q&A

**Goal:** answers to complex multi-step questions, long conversations.

**QueryDecomposer (LangChain):**
- Input: complex query.
- Output Pydantic: `{subquestions: [str], aggregation_strategy: compare
  | summarize | synthesize}`.
- Each sub-query → separate retrieval → intermediate answer.
- Aggregation via final LLM call.

**Conversation memory:**
- Short history (last K turns) — inline in prompt.
- Long-history summarisation: when history > N tokens, summarise older
  via a separate LLM call, cache in `Conversation.summary`.

**Re-ranking:**
- Optional cross-encoder (e.g. `ms-marco-MiniLM`) after retriever.
- Per-project flag (more expensive, better precision).

**Source diversity:**
- Answer must cover the question with blocks from at least M different
  articles, otherwise caveat "single-article source".

**Unblocks:** Q&A strong enough for real complex queries.

---

### Phase 11 — Observability prod-grade

**Goal:** product ready for operation at scale. Monitoring, eval, admin.

**Eval dashboard (`/admin/metrics`):**
- Clustering precision/recall, title similarity, citation precision,
  "don't know" recall, duplicate / contradiction precision.
- Metric history over time (CI-integrated).
- A/B prompt testing: two prompt versions on golden fixtures →
  comparison report.

**Admin tools:**
- `/admin/traces` — LangSmith-style traces of all agent calls, search
  by `run_id` / `project_id` / `agent_name`.
- `/admin/replay` — "re-run pipeline for source X with current prompts".
- `/admin/dryrun` — "what would RoutingAgent do with candidate Y, no
  apply".

**DigestAgent (LangChain, scheduled):**
- Weekly (per-project config): "This week: N documents, M articles
  created, K updated, L contradictions".
- Delivery: email (Resend) or Slack webhook.

**Audit log UI:**
- `/projects/{id}/audit` — full `IngestionEvent` with filters.

**Monitoring infra:**
- Sentry — error tracking.
- Structured logs + log aggregator (Grafana Loki / Datadog).
- Metrics dashboards — Prometheus / Grafana.

**Unblocks:** production-readiness; operationally supportable.

---

### Phase 12 — Research-grade ingestion

**Goal:** close the quality ceiling on complex content. Add URL
ingestion.

**PDF tables advanced:**
- `pdfplumber` as primary.
- Fallback to vision-LLM (page → image → structured JSON table) for
  complex layouts.
- Confidence per table.

**Formulas:**
- DOCX: OMML → LaTeX via `docx2tex` or `pandoc` subprocess.
- PDF: `pix2tex` (open) primary, Mathpix API optional paid.
- `meta.latex` on block; UI renders via KaTeX.

**CaptionAgent (vision-LLM):**
- Alt-text and caption for all `image` blocks.
- Regenerated on article change.

**Research Agent (URL ingestion):**
- `POST /projects/{id}/sources` with `type=url`.
- Fetcher: `httpx` + `trafilatura` for main-content extraction.
- Then passes through the normal ingest pipeline.
- Optional: periodic re-fetch → triggers incremental update on page
  change.

**Unblocks:** full coverage of source formats and multimodality.

---

### Phase 13 — Commercial launch

**Goal:** product monetised. Plans, billing, quota.

**Plans:**
- Free tier: limit N projects, M documents/project, K LLM calls/month,
  no Q&A memory, no graph, no incremental.
- Paid tier(s): full access, higher quotas. Starter / Pro / Enterprise
  gradation possible.

**Usage tracking:**
- Per-project quota on documents, LLM tokens, Q&A queries.
- `IngestionEvent.llm_cost_cents` summed per user.

**Stripe integration:**
- Subscriptions (monthly / yearly).
- Payment method management.
- Webhooks on plan changes (upgrade / downgrade / cancellation).

**Billing dashboard:**
- `/billing` — usage charts, upcoming invoice, payment history.
- Plan upgrade / downgrade UI.

**Enforcement:**
- Hard limits on free (block upload / Q&A when exceeded).
- Soft warnings approaching limits (email + in-app).

**Workspace (optional, for team plans):**
- Added if a team plan is needed — additive migration.
  `Project.workspace_id`, `WorkspaceMember`.
- Billing per workspace rather than per user.

**Unblocks:** SaaS monetisation.

---

### Phase 14 — Feedback loop + Integrations & Embed

**Goal:** final layer — user feedback (deliberately deferred from
earlier), public sharing, integration API.

**User Feedback loop:**
- `BlockFeedback` entity: `{block_id, user_id, kind ∈ {wrong_topic,
  duplicate, outdated, wrong_claim, missing_citation, link_wrong},
  note, status, escalation_count, created_at}`.
- `POST /blocks/{id}/feedback` endpoint.
- `FeedbackPipeline` (LangGraph sub-graph):
  - Nodes: `receive → classify → apply_action → cascade_invalidate →
    log_revision`.
  - `wrong_topic` → re-route via `RoutingAgent` with constraint "not
    into this article".
  - `duplicate` → trigger `SynthesisAgent` on the specified pair.
  - `outdated` → mark block, await replacement at ingest.
  - `wrong_claim` → to Inbox `on_review`.
  - `missing_citation` → re-run `CitationVerifier`.
  - `link_wrong` → re-run `Linker` on the block.
- Escalation: counter of moves per block; after N (config) →
  auto-pipeline locked, escalate to Inbox `stalled_blocks`.
- UI: per-block `...` menu with feedback kinds. Page
  `/projects/{id}/feedback`.

**Public sharing:**
- Optional public read-only URL for article or full KB.
- Per-project setting "make public".
- Public page without sidebar/admin, with Q&A (optional public Q&A).

**Embed widget:**
- iframe Q&A chat for embedding on sites.
- API key + allowed origins.

**Public API:**
- REST API for external integrations.
- API keys management in `/settings/api-keys`.
- Rate-limiting per key.

**Webhooks:**
- Events: `document.ingested`, `article.created`,
  `conflict.detected`, `digest.ready`.
- Delivery retry with exponential backoff.

**Connectors (select subset):**
- Slack bot for Q&A in channel.
- Notion import (one-shot).
- Confluence sync (periodic).

**Unblocks:** the final layer. Feedback closes the self-improving loop
of the base on real users.

---

## 8. Phase dependency summary

- **0** → everything (foundation).
- **1** → 2, 3, 4, 7 (wiki shape first).
- **2** → 3, 7, 8 (all StateGraph consumers).
- **3** → 4 (Q&A without graph is possible, but related/backlinks
  require it), 7 (Routing reads aliases).
- **4** → 5 (auth once the product shows value), 6 (verification over
  Q&A).
- **5** → everything production-level downstream.
- **6** → 7 (synthesis without verifier = hallucinations).
- **7** → 8, 9 (incremental generates events for scanners and
  versioning).
- **8, 9** — relatively independent, can parallelise.
- **10** — depends only on 4; can follow any of 6/7/8/9.
- **11** — on top of everything; needed for prod.
- **12** — independent branch; can parallel with 1.
- **13** — after 11.
- **14** — final, depends on 11+13.

---

## 9. Superseded material (for reference)

The sections below capture the older plan (v1, 2026-04-19) and earlier
product-direction discussion. They remain useful as background and for
rationale behind specific design choices (IBM Think Topics shape,
source-driven block types, title-composition policy, project-name =
document title, etc.). The **operational plan is § 7**.

### 9.1 Product direction — multi-topic articles (v1 rationale)

**Current behaviour (before Phase 1 lands):** the structure proposer
creates one `ArticleCandidate` per thematic sub-topic, so every
"article" ends up being a single short section. That's wrong for how
the user thinks about articles.

**What the user actually wants:**
- An **article** is a larger unit that bundles several related sub-topics
  under multiple headings — closer to a chapter / feature page than a
  single section.
- Headings inside an article survive as `ArticleBlock`s with
  `element_type = "heading"`, giving the rendered article real internal
  structure (H2/H3 etc.).
- Grouping should be **structural** (follow the source document's
  heading hierarchy where possible) rather than purely thematic.

**Implementation notes:**
- `detect_centers` in `app/domain/clustering/center_detector.py` currently
  treats every `section_path` as its own candidate. Needs to group by a
  *higher* level (e.g. top-level heading) and keep sub-headings as
  internal structure.
- `structure_service.py` should feed the LLM the top-level grouping and
  ask it to name the overall article, not each sub-section.
- `ArticleBuilder` already emits `ArticleBlock` per fragment — preserving
  headings is just a matter of not dropping `element_type = "heading"`
  fragments during build.
- Review UI grouping (currently by `suggested_section`) needs a re-think:
  a candidate should show a preview of its internal headings, not just a
  title.

> User quote (2026-04-18):
> *"я бы все же хотел что б статьи могли содержать несколько подтем
> а не как сейчас что это отдельная тема потому что в статье должно
> все же быть несколько заголовков и тд"*

### 9.2 Title generation policy — headings are HINTS, not titles (v1)

**Important correction (2026-04-19):** do NOT use heading fragments as
article titles directly. Always route through the LLM, passing the H1 +
internal H2/H3 headings as *structural hints*. The LLM composes a clean
title that may differ from any literal heading, and may also propose a
short description.

Rationale:
- Source headings often don't read well as standalone article titles
  ("1. Introduction", "Section 3", etc.).
- The LLM can see the whole internal structure at once and produce a
  title that summarises the *scope* of the article, not just echoes
  the first line.
- This keeps article naming consistent across sources with different
  heading conventions.

In `article_builder`, still skip the H1 fragment whose content matches
the hint (so we don't render it twice: once as article title, once as
top heading block). Internal H2/H3 headings are preserved as heading
blocks and drive both the internal structure and the auto-TOC.

> User quote (2026-04-19):
> *"я б все таки не брал название из тайтлов а только брал его что б
> агент мог собрать правильное название и структуру"*

### 9.3 Project name = document top-level title (v1)

The **document's top-level (H1) title is the Project name hint**, and
also a signal to the LLM about the overall domain when it composes
article titles and structure.

Concretely:
- On upload, the extractor's first H1 (before any body text) is stored
  on `Source.title` and offered as a default `Project.name` if the
  project doesn't have one yet.
- When calling `propose_structure`, pass `project.name` (or the
  top-level title) as domain context to the LLM so it can pick titles
  that are consistent with the subject matter of the whole corpus.

> User quote (2026-04-19):
> *"название проекта это верхний тайтл, он также может дать подсказку
> от чего строить статьи"*

---

### 9.4 Knowledge-base shape — insights from IBM Think Topics (v1)

User pointed at `ibm.com/think/topics/llm-temperature` as the target "look".
After seeing the actual page (screenshots, 2026-04-19), the correct model is:

#### 9.4.1 Core principle — **DKS reflects the source**

There is **no section rubric** (the IBM H2s are *subtopics of the article*,
not slots from a closed template). Block types appear only when the source
contains them. If the source has a code block, preserve it. If it doesn't,
don't invent one.

This is a correction of an earlier (wrong) guess that IBM used a fixed
"What is / Why / How / Use cases / Best practices" schema.

#### 9.4.2 Block types observed on the IBM page

Already modelled in `ElementType`: `heading`, `paragraph`, `list_item`,
`table`, `caption`.

**Missing from `ElementType`:**
- `quote` / `blockquote` (with attribution in `meta_json`)
- `code_block` (with language in `meta_json`)
- `footnote` / `citation` (numbered refs; inline superscript + a References section)

#### 9.4.3 Missing inline markup
IBM pages use `**bold term**: description...` patterns extensively.
Currently we lose inline formatting:
- PDF extractor uses `is_bold` only to classify whole blocks as headings.
- DOCX extractor uses `para.text`, which flattens runs.

**Fix:** preserve inline spans as `(start, end, style)` tuples in
`meta_json`, or store `content` as a minimal markdown/HTML subset that
captures bold/italic/link/code spans.

#### 9.4.4 Inline cross-references (topic graph)
IBM's real power is that every named technical concept inline is a link
to another topic page. Derivable automatically for DKS:
- Build a dictionary of article titles + aliases per project.
- Post-build pass: scan each `ArticleBlock.content` for known aliases,
  wrap in links. Persist as `meta_json.links: [{start, end, article_id}]`.
- Show backlinks on article detail ("Referenced by: ...").

#### 9.4.5 Cheap UI wins (unblocked by multi-topic)
- **Auto-TOC**: sidebar "On this page" derived from `ArticleBlock`s where
  `element_type = "heading"`, sorted by `position_index`.
- **Related topics**: cosine similarity on article-level embedding (mean
  of fragment embeddings) → top 3-5 siblings. Render a card grid at end.

#### 9.4.6 Topic identity — the encyclopedia step
IBM's `/think/topics/llm-temperature` is a **stable canonical URL**.
One topic, one page, regardless of which sources fed it.

DKS is currently document-centric (one source → N articles in one
proposal). To become topic-centric:
- Add `Article.slug: str` (unique per project).
- On new proposals, detect when a new candidate matches an existing
  article by embedding/title similarity → offer "merge into existing"
  instead of creating a new article. (This is where the dormant
  `MERGED` status gets wired.)
- Backlinks + cross-refs update when articles merge.

This is a later-stage change — probably after rich content lands.

---

### 9.5 Rich content preservation roadmap (v1)

Extended with § 9.4 insights. Largely absorbed into Phase 1 and Phase 12
in the new plan.

#### 9.5.1 Block types
1. **Nested lists** — add `list_level: int | None` to `SourceFragment` /
   `ArticleBlock`. DOCX: read `w:numPr/w:ilvl` from python-docx. PDF:
   infer from prefix + indentation. UI renders nested `<ul>`.

2. **Images** — new `element_type = "image"`. Extract binaries:
   - DOCX via `doc.inline_shapes` + `part.related_parts`
   - PDF via PyMuPDF `page.get_images()` + `doc.extract_image()`
   Store alongside the source file, keep URL + alt in `meta_json`.
   `position_index` keeps them inline with surrounding paragraphs.

3. **Blockquotes** — new `element_type = "quote"`. DOCX: `Quote` /
   `Intense Quote` styles. PDF: heuristic on indent + quotation marks.
   Attribution in `meta_json.attribution`.

4. **Code blocks** — new `element_type = "code_block"`. DOCX: monospace
   runs or `Code` style. PDF: monospace font detection at span level.
   Language hint (if any) in `meta_json.language`.

5. **Footnotes / citations** — new `element_type = "footnote"`. Inline
   superscript refs stored as inline markup spans (see 8.3); the
   footnote bodies rendered as a "References" block at article end.

6. **Tables as structured data** — currently the DOCX path serialises
   tables as `a | b | c\nd | e | f` strings, losing structure. Switch
   to storing rows × cells in `meta_json` (`{"rows": [[...]]}`) and
   render as real `<table>`. PDF tables need a dedicated extractor
   (`pdfplumber.extract_tables` or `camelot`); PyMuPDF's text-block
   approach doesn't detect tables.

7. **LaTeX formulas** — new `element_type = "formula"`, store LaTeX in
   `meta_json.latex`. UI renders via KaTeX.
   - DOCX: OMML → LaTeX conversion. Options: pandoc subprocess
     (reliable, adds external dep) or lxml + XSLT.
   - PDF: effectively impossible without OCR — formulas are rasterised
     glyphs. Realistic path is `pix2tex` / Mathpix API. Separate project.

#### 9.5.2 Schema additions
- `SourceFragment.list_level: Optional[int]`
- `SourceFragment.meta_json: JSONB` (reused by every new block type)
- Extend `ElementType` enum: `quote`, `code_block`, `image`, `formula`, `footnote`
- `ArticleBuilder` must copy `meta_json` forward to `ArticleBlock`,
  not just `content`.

#### 9.5.3 Inline markup (per § 9.4.3)
- `SourceFragment.inline_spans: list[{start, end, style, data}]` in
  `meta_json`. `style ∈ {bold, italic, code, link}`. For `link`,
  `data` holds URL or (later) `article_id` for cross-references.

---

### 9.6 Old phased delivery plan (v1, 2026-04-19) — SUPERSEDED

The v1 plan compressed everything into five phases (IBM visual shape →
Topic graph → Growing KB → Q&A + LangChain/LangGraph → nice-to-haves).
It has been replaced by the 15-phase plan in § 7. Mapping from v1 to v2:

- v1 Phase 1 (IBM visual shape) → v2 **Phase 1** (Wiki shape) +
  **Phase 0** (schema prep for rich blocks / inline markup).
- v1 Phase 2 (Topic graph: slug, cross-refs, backlinks, graph UI) →
  v2 **Phase 3** (Linker & Knowledge Graph).
- v1 Phase 3 (Growing KB: incremental ingest, topic matching, merged
  candidates) → v2 **Phase 7** (Incremental integration) — now much
  more rigorous: DedupService + RoutingAgent + PlacementAgent +
  SynthesisAgent with HITL gates and cascade invalidation.
- v1 Phase 4 (LangChain/LangGraph refactor + RAG) splits across
  v2 **Phase 0** (LangChain refactor of `structure_agent.py`),
  v2 **Phase 2** (LangGraph `IngestPipeline` backbone), v2 **Phase 4**
  (strict RAG Q&A), v2 **Phase 6** (CitationVerifier), v2 **Phase 10**
  (advanced Q&A).
- v1 Phase 5 (nested lists, images, structured tables, LaTeX) →
  **Phase 1** (nested lists, images, structured tables baseline) +
  **Phase 12** (research-grade: vision-LLM tables, LaTeX formulas,
  URL ingestion, captions).

v1 didn't cover: Structural Blocks hierarchy, Knowledge Nodes, auth /
multi-tenancy, citation trust as a distinct phase, anomaly /
contradiction scanners, semantic versioning, observability, commercial
launch, feedback loop. All of these are first-class phases in v2.

---

## 10. How to resume work in a new session

### Stack-up commands
1. Pull latest; `docker compose up -d db redis`.
2. Backend: `uvicorn app.main:app --reload` (or `docker compose up api`).
3. Worker: `arq app.workers.ingestion_worker.WorkerSettings`.
4. Frontend: `cd dks-ui && npm run dev`.
   - If Turbopack panics, switch `dev` to `next dev --webpack`.
5. Open `http://localhost:3000`.

### Relevant test entry points
- `pytest tests/test_ui_flow.py -x` — full happy-path smoke test.
- `pytest tests/test_pipeline_articles_build.py -x` — build + confirmation gate.

### Immediate next action — start of Phase 0

**First task in the new chat: begin Phase 0 — Foundation (§ 7, Phase 0).**
Phase 0 is purely prep: migrations + agent framework skeleton + LangChain
refactor + invariant tests + golden fixtures. No new product behaviour.

Reading order:
1. Re-read § 6 (core invariants — non-negotiable).
2. Re-read § 7, Phase 0 end-to-end to understand the full scope of the
   foundation before starting migrations.
3. Start with data-model migrations in small, reviewable slices:
   a. Extend existing tables: `SourceFragment.content_hash`,
      `SourceFragment.inline_spans`; `Source.type` enum widening;
      `Article.kind`, `description`, `summary`, `slug`,
      `structural_block_id`, `revision_count`, `aliases`, status enum
      widening; `ArticleBlock.meta_json`, `list_level`, `group_id`,
      `source_position_index`, `added_at`.
   b. New tables in logical groups: graph (`StructuralBlock`,
      `GraphEdge`, `Alias`), integrity (`BlockCitation`, `Conflict`,
      `Anomaly`), provenance (`BlockRevision`, `ArticleRevision`,
      `IngestionEvent`, `BlockMultiSource`), Q&A (`Conversation`,
      `Message`), auth skeleton (`User`, `ProjectMember`).
   c. Backfill `source_position_index` on existing `ArticleBlock` rows
      from `fragment.position_index`. Needed for invariant test #1.
4. Scaffold `app/agents/` directory per § 7, Phase 0 layout (`base.py`,
   `prompts/`, `chains/`, `tools/`, `graph/`, `ml/`, `state/`).
5. Port `structure_agent.py` to LangChain: `ChatGroq` +
   `ChatPromptTemplate` + `PydanticOutputParser`. Behaviour must be
   1:1: retry, fallback title, per-candidate title + hierarchy.
   Tests stay green.
6. Wire LangSmith tracing globally (env-gated).
7. Add invariant tests:
   - `test_source_order_monotonic` — for every `(source_id,
     article_id)` pair after build, `source_position_index` is
     non-decreasing.
   - `test_content_immutability` — `block.content == block.fragment.content`
     for all non-synthesized blocks.
8. Create `tests/golden/` with one `.docx` fixture + baseline
   `expected.json` (candidate titles, fragment counts) + a fixture
   that runs the pipeline and compares.

Only after Phase 0 is green start on Phase 1 (Wiki shape).

### Ground rules from user
- **No saved data yet.** The dev DB is empty / disposable. Migrations
  can be destructive: `DROP TYPE` + recreate enums, drop/recreate
  tables, no backfill scripts, no downgrade-safety. Treat
  `alembic upgrade head` on a fresh DB as the only supported path
  until real users arrive (Phase 5).
- **§ 6.1 (source order)** — blocks from the same source in the
  same article are monotonically ordered by `source_position_index`.
  CI-enforced.
- **§ 6.2 (content immutability)** — `ArticleBlock.content ==
  SourceFragment.content` for all non-synthesized blocks. Only
  `SynthesisAgent` may produce merged content, and only under
  `CitationVerifier` assertions (no new facts, connective tissue only).
  CI-enforced.
- Preserve source fidelity. Do not invent block types not present in
  the source. (§ 9.4.1)
- Do not echo source headings as article titles. LLM composes titles
  from heading hints. (§ 9.2)
- Follow the phase order in § 7 strictly. Do not jump ahead (e.g. don't
  start Phase 4 Q&A before Phase 3 Linker, don't start Phase 7
  incremental before Phase 6 CitationVerifier).
- HITL gates on destructive ops (merge, restructure, promote_node,
  mass-reroute) are mandatory from Phase 2 onwards.

### User language preference
User writes in Russian, often code-switched with English for technical
terms. Respond in Russian by default unless asked otherwise. Keep
engineering discussion direct and dense — the user reads fast and
dislikes filler.
