from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.agents.chains.qa_agent import QAAgentOutput, answer_question
from app.domain.qa.retrieval import retrieve_relevant_blocks
from app.domain.qa.retrieval import RetrievalResult
from app.models.article import Article, ArticleBlock, ArticleKind, ArticleStatus
from app.models.block_citation import BlockCitation
from app.models.conversation import Conversation, Message, MessageRole
from app.models.source import Source, SourceStatus, SourceType
from app.models.source_fragment import ElementType, SourceFragment
from sqlalchemy import select


GOLDEN_QA_EVAL = Path(__file__).parent / "golden" / "phase4_qa_eval.json"


def _vector(first: float, second: float = 0.0) -> list[float]:
    return [first, second, *([0.0] * 1534)]


def _retrieval_result(
    *,
    block_id: uuid.UUID | None = None,
    article_id: uuid.UUID | None = None,
    fragment_id: uuid.UUID | None = None,
    title: str = "Evidence Article",
    content: str = "The retrieved block contains direct evidence.",
    score: float = 0.91,
) -> RetrievalResult:
    return RetrievalResult(
        block_id=block_id or uuid.uuid4(),
        article_id=article_id or uuid.uuid4(),
        article_title=title,
        fragment_id=fragment_id or uuid.uuid4(),
        content=content,
        element_type=ElementType.PARAGRAPH,
        page_number=1,
        section_path="Evidence",
        score=score,
    )


async def test_retrieval_applies_article_diversity(
    project, db, monkeypatch
):
    project_id = uuid.UUID(project["id"])

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [_vector(1.0, 0.0) for _ in texts]

    monkeypatch.setattr("app.domain.qa.retrieval.embed_texts", fake_embed_texts)

    source = Source(
        project_id=project_id,
        filename="retrieval.docx",
        source_type=SourceType.DOCX,
        storage_path="/tmp/retrieval.docx",
        status=SourceStatus.DONE,
    )
    db.add(source)
    await db.flush()

    first_article = Article(
        project_id=project_id,
        title="Alpha Article",
        slug="alpha-article",
        kind=ArticleKind.ARTICLE,
        status=ArticleStatus.DRAFT,
    )
    second_article = Article(
        project_id=project_id,
        title="Beta Article",
        slug="beta-article",
        kind=ArticleKind.ARTICLE,
        status=ArticleStatus.DRAFT,
    )
    db.add_all([first_article, second_article])
    await db.flush()

    fragments = [
        SourceFragment(
            source_id=source.id,
            content="Alpha block one about neural retrieval.",
            element_type=ElementType.PARAGRAPH,
            position_index=1,
            embedding=_vector(1.0, 0.0),
        ),
        SourceFragment(
            source_id=source.id,
            content="Alpha block two about neural retrieval.",
            element_type=ElementType.PARAGRAPH,
            position_index=2,
            embedding=_vector(0.99, 0.01),
        ),
        SourceFragment(
            source_id=source.id,
            content="Beta block about a neighbouring concept.",
            element_type=ElementType.PARAGRAPH,
            position_index=3,
            embedding=_vector(0.8, 0.2),
        ),
    ]
    db.add_all(fragments)
    await db.flush()

    db.add_all(
        [
            ArticleBlock(
                article_id=first_article.id,
                fragment_id=fragments[0].id,
                content=fragments[0].content,
                element_type=ElementType.PARAGRAPH,
                position_index=0,
                source_position_index=fragments[0].position_index,
            ),
            ArticleBlock(
                article_id=first_article.id,
                fragment_id=fragments[1].id,
                content=fragments[1].content,
                element_type=ElementType.PARAGRAPH,
                position_index=1,
                source_position_index=fragments[1].position_index,
            ),
            ArticleBlock(
                article_id=second_article.id,
                fragment_id=fragments[2].id,
                content=fragments[2].content,
                element_type=ElementType.PARAGRAPH,
                position_index=0,
                source_position_index=fragments[2].position_index,
            ),
        ]
    )
    await db.commit()

    results = await retrieve_relevant_blocks(
        project_id,
        "neural retrieval",
        db,
        top_k=3,
        max_per_article=1,
    )

    assert len(results) == 2
    assert [result.article_title for result in results] == [
        "Alpha Article",
        "Beta Article",
    ]
    assert len({result.article_id for result in results}) == 2
    assert all(result.score >= 0 for result in results)


async def test_retrieval_ignores_empty_queries(project, db):
    results = await retrieve_relevant_blocks(
        uuid.UUID(project["id"]),
        "   ",
        db,
    )
    assert results == []


async def test_qa_agent_normalises_llm_output(monkeypatch):
    valid = _retrieval_result(content="NVIDIA P100 GPUs were used for training.")
    invalid_id = uuid.uuid4()

    async def fake_invoke_llm(question, context):
        return QAAgentOutput(
            answer="  The model was trained on NVIDIA P100 GPUs.  ",
            citation_block_ids=[invalid_id, valid.block_id, valid.block_id],
            confidence=1.7,
            insufficient_context=False,
        )

    monkeypatch.setattr("app.agents.chains.qa_agent._offline_mode", lambda: False)
    monkeypatch.setattr("app.agents.chains.qa_agent._invoke_llm", fake_invoke_llm)

    answer = await answer_question("Which hardware was used?", [valid])

    assert answer.answer == "The model was trained on NVIDIA P100 GPUs."
    assert answer.citation_block_ids == [valid.block_id]
    assert answer.confidence == 1.0
    assert answer.insufficient_context is False


async def test_qa_agent_drops_citations_not_used_by_answer(monkeypatch):
    used = _retrieval_result(
        content="The decoder generates output sequence elements one at a time."
    )
    unused = _retrieval_result(
        content="The encoder maps input symbols into continuous representations."
    )

    async def fake_invoke_llm(question, context):
        return QAAgentOutput(
            answer="The decoder generates output sequence elements one at a time.",
            citation_block_ids=[used.block_id, unused.block_id],
            confidence=0.9,
            insufficient_context=False,
        )

    monkeypatch.setattr("app.agents.chains.qa_agent._offline_mode", lambda: False)
    monkeypatch.setattr("app.agents.chains.qa_agent._invoke_llm", fake_invoke_llm)

    answer = await answer_question("What is decoder?", [used, unused])

    assert answer.citation_block_ids == [used.block_id]


async def test_phase4_golden_qa_eval(client, project, db, monkeypatch):
    project_id = uuid.UUID(project["id"])

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            if "hardware" in lowered or "nvidia" in lowered:
                vectors.append(_vector(1.0, 0.0))
            elif "optimizer" in lowered or "adam" in lowered:
                vectors.append(_vector(0.0, 1.0))
            else:
                vectors.append(_vector(-1.0, 0.0))
        return vectors

    monkeypatch.setattr("app.domain.qa.retrieval.embed_texts", fake_embed_texts)

    source = Source(
        project_id=project_id,
        filename="phase4-qa-golden.docx",
        source_type=SourceType.DOCX,
        storage_path="/tmp/phase4-qa-golden.docx",
        status=SourceStatus.DONE,
    )
    db.add(source)
    await db.flush()

    article = Article(
        project_id=project_id,
        title="Training Details",
        slug="training-details",
        kind=ArticleKind.ARTICLE,
        status=ArticleStatus.DRAFT,
    )
    db.add(article)
    await db.flush()

    fragments = [
        SourceFragment(
            source_id=source.id,
            content="The training hardware used 8 NVIDIA P100 GPUs.",
            element_type=ElementType.PARAGRAPH,
            position_index=1,
            section_path="Hardware",
            page_number=7,
            embedding=_vector(1.0, 0.0),
        ),
        SourceFragment(
            source_id=source.id,
            content="The optimizer used in the experiment was Adam.",
            element_type=ElementType.PARAGRAPH,
            position_index=2,
            section_path="Optimizer",
            page_number=8,
            embedding=_vector(0.0, 1.0),
        ),
    ]
    db.add_all(fragments)
    await db.flush()

    db.add_all(
        [
            ArticleBlock(
                article_id=article.id,
                fragment_id=fragment.id,
                content=fragment.content,
                element_type=ElementType.PARAGRAPH,
                position_index=index,
                source_position_index=fragment.position_index,
                section_path=fragment.section_path,
                page_number=fragment.page_number,
            )
            for index, fragment in enumerate(fragments)
        ]
    )
    await db.commit()

    cases = json.loads(GOLDEN_QA_EVAL.read_text())
    assert len(cases) >= 3

    for case in cases:
        response = await client.post(
            f"/projects/{project['id']}/ask",
            json={
                "question": case["question"],
                "top_k": 5,
                "max_per_article": 3,
                "min_score": 0.2,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["insufficient_context"] is case["expected_insufficient_context"], case["id"]
        assert len(body["citations"]) >= case["min_citations"], case["id"]

        answer_text = " ".join(
            [body["answer"], *[citation["content"] for citation in body["citations"]]]
        )
        for term in case["expected_terms"]:
            assert term.lower() in answer_text.lower(), case["id"]


async def test_ask_endpoint_returns_retrieved_citations(
    client, project, db, monkeypatch
):
    project_id = uuid.UUID(project["id"])

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [_vector(1.0, 0.0) for _ in texts]

    monkeypatch.setattr("app.domain.qa.retrieval.embed_texts", fake_embed_texts)

    source = Source(
        project_id=project_id,
        filename="ask.docx",
        source_type=SourceType.DOCX,
        storage_path="/tmp/ask.docx",
        status=SourceStatus.DONE,
    )
    db.add(source)
    await db.flush()

    article = Article(
        project_id=project_id,
        title="Retrieval Article",
        slug="retrieval-article",
        kind=ArticleKind.ARTICLE,
        status=ArticleStatus.DRAFT,
    )
    db.add(article)
    await db.flush()

    fragment = SourceFragment(
        source_id=source.id,
        content="Neural retrieval uses embeddings to find relevant source blocks.",
        element_type=ElementType.PARAGRAPH,
        position_index=1,
        section_path="Retrieval",
        page_number=2,
        embedding=_vector(1.0, 0.0),
    )
    db.add(fragment)
    await db.flush()
    db.add(
        ArticleBlock(
            article_id=article.id,
            fragment_id=fragment.id,
            content=fragment.content,
            element_type=ElementType.PARAGRAPH,
            position_index=0,
            source_position_index=fragment.position_index,
            section_path=fragment.section_path,
            page_number=fragment.page_number,
        )
    )
    await db.commit()

    response = await client.post(
        f"/projects/{project['id']}/ask",
        json={"question": "How does neural retrieval work?", "top_k": 5},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation_id"]
    assert body["user_message_id"]
    assert body["assistant_message_id"]
    assert body["insufficient_context"] is False
    assert body["confidence"] > 0
    assert len(body["citations"]) == 1
    assert body["citations"][0]["article_title"] == "Retrieval Article"
    assert body["citations"][0]["page_number"] == 2

    messages = (
        await db.execute(
            select(Message).order_by(Message.position_index)
        )
    ).scalars().all()
    assert [message.role for message in messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert messages[0].content == "How does neural retrieval work?"
    assert messages[1].meta_json["citations"][0]["article_title"] == "Retrieval Article"

    citation_count = (
        await db.execute(select(BlockCitation))
    ).scalars().all()
    assert len(citation_count) == 1


async def test_ask_endpoint_returns_insufficient_context_for_empty_project(
    client, project, monkeypatch
):
    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [_vector(1.0, 0.0) for _ in texts]

    monkeypatch.setattr("app.domain.qa.retrieval.embed_texts", fake_embed_texts)

    response = await client.post(
        f"/projects/{project['id']}/ask",
        json={"question": "What is missing?"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["insufficient_context"] is True
    assert body["citations"] == []


async def test_ask_endpoint_short_circuits_weak_evidence(
    client, project, db, monkeypatch
):
    project_id = uuid.UUID(project["id"])

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [_vector(0.0, 1.0) for _ in texts]

    async def fail_if_called(question, context):
        raise AssertionError("QAAgent should not be called for weak evidence")

    monkeypatch.setattr("app.domain.qa.retrieval.embed_texts", fake_embed_texts)
    monkeypatch.setattr("app.api.qa.answer_question", fail_if_called)

    source = Source(
        project_id=project_id,
        filename="weak.docx",
        source_type=SourceType.DOCX,
        storage_path="/tmp/weak.docx",
        status=SourceStatus.DONE,
    )
    db.add(source)
    await db.flush()

    article = Article(
        project_id=project_id,
        title="Weak Evidence Article",
        slug="weak-evidence-article",
        kind=ArticleKind.ARTICLE,
        status=ArticleStatus.DRAFT,
    )
    db.add(article)
    await db.flush()

    fragment = SourceFragment(
        source_id=source.id,
        content="This weakly related block should not be enough evidence.",
        element_type=ElementType.PARAGRAPH,
        position_index=1,
        embedding=_vector(0.25, 0.0),
    )
    db.add(fragment)
    await db.flush()
    db.add(
        ArticleBlock(
            article_id=article.id,
            fragment_id=fragment.id,
            content=fragment.content,
            element_type=ElementType.PARAGRAPH,
            position_index=0,
            source_position_index=fragment.position_index,
        )
    )
    await db.commit()

    response = await client.post(
        f"/projects/{project['id']}/ask",
        json={
            "question": "Can this weak block answer confidently?",
            "min_score": 0.0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["insufficient_context"] is True
    assert body["confidence"] == 0.0
    assert body["citations"] == []


async def test_ask_stream_endpoint_emits_status_and_answer(
    client, project, db, monkeypatch
):
    project_id = uuid.UUID(project["id"])

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [_vector(1.0, 0.0) for _ in texts]

    monkeypatch.setattr("app.domain.qa.retrieval.embed_texts", fake_embed_texts)

    source = Source(
        project_id=project_id,
        filename="stream.docx",
        source_type=SourceType.DOCX,
        storage_path="/tmp/stream.docx",
        status=SourceStatus.DONE,
    )
    db.add(source)
    await db.flush()

    article = Article(
        project_id=project_id,
        title="Streaming Article",
        slug="streaming-article",
        kind=ArticleKind.ARTICLE,
        status=ArticleStatus.DRAFT,
    )
    db.add(article)
    await db.flush()

    fragment = SourceFragment(
        source_id=source.id,
        content="Streaming answers retrieve evidence before responding.",
        element_type=ElementType.PARAGRAPH,
        position_index=1,
        embedding=_vector(1.0, 0.0),
    )
    db.add(fragment)
    await db.flush()
    db.add(
        ArticleBlock(
            article_id=article.id,
            fragment_id=fragment.id,
            content=fragment.content,
            element_type=ElementType.PARAGRAPH,
            position_index=0,
            source_position_index=fragment.position_index,
        )
    )
    await db.commit()

    response = await client.post(
        f"/projects/{project['id']}/ask/stream",
        json={"question": "How does streaming answer?"},
    )

    assert response.status_code == 200, response.text
    assert "text/event-stream" in response.headers["content-type"]
    text = response.text
    assert "event: status" in text
    assert "event: answer" in text
    assert "event: done" in text
    assert "Streaming Article" in text


async def test_ask_endpoint_continues_existing_conversation(
    client, project, db, monkeypatch
):
    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [_vector(1.0, 0.0) for _ in texts]

    monkeypatch.setattr("app.domain.qa.retrieval.embed_texts", fake_embed_texts)

    first = await client.post(
        f"/projects/{project['id']}/ask",
        json={"question": "First question?"},
    )
    assert first.status_code == 200, first.text
    conversation_id = first.json()["conversation_id"]

    second = await client.post(
        f"/projects/{project['id']}/ask",
        json={
            "question": "Second question?",
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["conversation_id"] == conversation_id

    conversations = (
        await db.execute(select(Conversation))
    ).scalars().all()
    assert len(conversations) == 1

    messages = (
        await db.execute(
            select(Message).order_by(Message.position_index)
        )
    ).scalars().all()
    assert [message.position_index for message in messages] == [0, 1, 2, 3]
    assert [message.role for message in messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]

    list_response = await client.get(f"/projects/{project['id']}/conversations")
    assert list_response.status_code == 200, list_response.text
    assert list_response.json()[0]["message_count"] == 4

    messages_response = await client.get(
        f"/projects/{project['id']}/conversations/{conversation_id}/messages"
    )
    assert messages_response.status_code == 200, messages_response.text
    assert [message["content"] for message in messages_response.json()][::2] == [
        "First question?",
        "Second question?",
    ]


async def test_conversations_are_sorted_by_last_activity(
    client, project, monkeypatch
):
    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [_vector(1.0, 0.0) for _ in texts]

    monkeypatch.setattr("app.domain.qa.retrieval.embed_texts", fake_embed_texts)

    first = await client.post(
        f"/projects/{project['id']}/ask",
        json={"question": "First thread?"},
    )
    assert first.status_code == 200, first.text
    first_conversation_id = first.json()["conversation_id"]

    second = await client.post(
        f"/projects/{project['id']}/ask",
        json={"question": "Second thread?"},
    )
    assert second.status_code == 200, second.text
    second_conversation_id = second.json()["conversation_id"]

    bump_first = await client.post(
        f"/projects/{project['id']}/ask",
        json={
            "question": "Continue first thread?",
            "conversation_id": first_conversation_id,
        },
    )
    assert bump_first.status_code == 200, bump_first.text

    list_response = await client.get(f"/projects/{project['id']}/conversations")
    assert list_response.status_code == 200, list_response.text
    payload = list_response.json()

    assert [item["id"] for item in payload] == [
        first_conversation_id,
        second_conversation_id,
    ]


async def test_delete_conversation_removes_messages(client, project, db, monkeypatch):
    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [_vector(1.0, 0.0) for _ in texts]

    monkeypatch.setattr("app.domain.qa.retrieval.embed_texts", fake_embed_texts)

    first = await client.post(
        f"/projects/{project['id']}/ask",
        json={"question": "Delete this conversation?"},
    )
    assert first.status_code == 200, first.text
    conversation_id = first.json()["conversation_id"]

    delete_response = await client.delete(
        f"/projects/{project['id']}/conversations/{conversation_id}"
    )
    assert delete_response.status_code == 204, delete_response.text

    messages = (await db.execute(select(Message))).scalars().all()
    conversations = (await db.execute(select(Conversation))).scalars().all()
    assert messages == []
    assert conversations == []

    get_response = await client.get(
        f"/projects/{project['id']}/conversations/{conversation_id}/messages"
    )
    assert get_response.status_code == 404
