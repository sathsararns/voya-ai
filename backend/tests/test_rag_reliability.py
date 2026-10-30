"""RAG failure-path and grounding regressions; no external API calls."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services import document_ingest, groq_service, kb_rag, pdf_text
from services.context_router import decide_context_strategy
from services.retrieval_policy import build_search_query, relevant_hits
from services.ingestion_lock import knowledge_write_lock


def hit(text, score=0.7, index=0):
    return {"id": str(index), "score": score,
            "fields": {"text": text, "document_name": "guide.pdf", "chunk_index": index}}


CASES_PATH = Path(__file__).with_name("eval_cases.json")


@pytest.mark.parametrize("case", json.loads(CASES_PATH.read_text(encoding="utf-8")))
def test_existing_travel_evaluation_queries_reach_the_kb(case):
    assert decide_context_strategy(case["query"], 0).use_knowledge_base


@pytest.mark.parametrize("query", ["Visa requirements", "Kandy attractions", "Train timetable",
    "What should I wear when visiting the Temple of the Tooth?", "Train times from Colombo to Kandy"])
def test_short_and_transport_questions_reach_kb(query):
    result = decide_context_strategy(query, 2)
    assert result.use_knowledge_base
    assert "get_weather" not in result.candidate_tools


def test_irrelevant_and_duplicate_chunks_are_not_sources():
    query = "What is the current entrance fee for Brief Garden today?"
    hits = [hit("Many historic sites require tickets and hotels charge service fees."),
            hit("Brief Garden entry prices must be confirmed with the venue.", 0.7, 1),
            hit("Brief Garden entry prices must be confirmed with the venue.", 0.65, 2),
            hit("Brief Garden is a garden.", 0.1, 3)]
    result = relevant_hits(query, hits, 4)
    assert [h["id"] for h in result] == ["1"]
    assert len(kb_rag.build_kb_sources(result)) == 1


@pytest.mark.parametrize("query", ["How do I replace a computer graphics card?",
    "What are the opening hours of the Atlantis Moon Museum?"])
def test_unanswerable_queries_cannot_use_generic_travel_paragraphs(query):
    assert relevant_hits(query, [hit("National Museum entrance hours and ticket prices in Sri Lanka.")], 4) == []


def test_followup_search_keeps_the_itinerary_destinations():
    history = [{"role": "assistant", "content": json.dumps({
        "destination": "Galle, Mirissa, Bentota", "itinerary": [{"day": 1}]})}]
    assert "Galle, Mirissa, Bentota" in build_search_query("Make the second day more relaxed", history)
    assert build_search_query("Visa requirements", history) == "Visa requirements"


def test_provider_outage_is_distinct_from_no_matches(monkeypatch):
    index = MagicMock()
    index.search.side_effect = RuntimeError("provider down")
    monkeypatch.setattr(kb_rag, "index", index)
    with pytest.raises(kb_rag.KnowledgeBaseUnavailable):
        kb_rag.search_knowledge_base("Kandy attractions")


@pytest.mark.parametrize("outage", [False, True])
def test_prompt_builder_explains_missing_evidence_without_sources(monkeypatch, outage):
    search = MagicMock(return_value=[])
    if outage:
        search.side_effect = kb_rag.KnowledgeBaseUnavailable()
    monkeypatch.setattr(groq_service, "search_knowledge_base", search)
    messages, sources, _ = groq_service._build_prompt_messages(
        "Brief Garden entrance fee today", None, [], False, True, "test", candidate_tools=[])
    assert sources == []
    assert ("unavailable" if outage else "no_supporting_evidence") in messages[0]["content"]
    assert "Do not invent citations" in messages[0]["content"]


def test_empty_reingestion_never_mutates_the_index(monkeypatch):
    replace = MagicMock()
    monkeypatch.setattr(document_ingest, "build_chunks", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(document_ingest, "replace_document_chunks", replace)
    with pytest.raises(ValueError, match="preserved"):
        document_ingest.ingest_file("scanned.pdf")
    replace.assert_not_called()


def test_failed_reingestion_only_rolls_back_its_new_generation(monkeypatch):
    index = MagicMock()
    index.upsert_records.side_effect = RuntimeError("upload failed")
    monkeypatch.setattr(kb_rag, "index", index)
    with pytest.raises(RuntimeError, match="upload failed"):
        kb_rag.replace_document_chunks("guide.pdf", [hit("Valid text")["fields"]])
    condition = index.delete.call_args.kwargs["filter"]
    assert set(condition) == {"ingestion_id"}  # never delete previous doc
    index.fetch.assert_not_called()


def test_new_records_must_be_visible_before_old_ones_are_removed(monkeypatch):
    index = MagicMock()
    calls = []
    index.upsert_records.side_effect = lambda **_: calls.append("upsert")
    def fetch(**kwargs):
        calls.append("fetch")
        return {"vectors": {key: {} for key in kwargs["ids"]}}
    index.fetch.side_effect = fetch
    index.delete.side_effect = lambda **_: calls.append("prune")
    monkeypatch.setattr(kb_rag, "index", index)
    assert kb_rag.replace_document_chunks("guide.pdf", [hit("Valid text")["fields"]]) == 1
    assert calls == ["upsert", "fetch", "prune"]
    condition = index.delete.call_args.kwargs["filter"]
    assert "$and" in condition
    assert condition["$and"][0]["document_name"]["$eq"] == "guide.pdf"


def test_visibility_timeout_keeps_old_records(monkeypatch):
    index = MagicMock()
    index.fetch.return_value = {"vectors": {}}
    monkeypatch.setattr(kb_rag, "index", index)
    monkeypatch.setattr(kb_rag.time, "monotonic", MagicMock(side_effect=[0, 31]))
    with pytest.raises(TimeoutError, match="preserved"):
        kb_rag.replace_document_chunks("guide.pdf", [hit("Valid text")["fields"]])
    assert set(index.delete.call_args.kwargs["filter"]) == {"ingestion_id"}


def test_colliding_filename_slugs_have_different_record_ids():
    assert kb_rag._record_id("A B.pdf", 0) != kb_rag._record_id("A-B.pdf", 0)


def test_overlap_never_breaks_the_declared_chunk_size():
    chunks = document_ingest.chunk_text("A" * 1000 + "\n\n" + "B" * 1150)
    assert all(len(chunk) <= document_ingest.CHUNK_SIZE for chunk in chunks)
    assert any("B" * 1150 in chunk for chunk in chunks)


def test_train_question_can_use_a_rail_paragraph_without_literal_train_word():
    evidence = hit("Rail journeys offer scenic landscapes across Sri Lanka.")
    assert relevant_hits("What is train travel like in Sri Lanka?", [evidence], 4) == [evidence]


def test_timetable_rejects_reverse_routes_and_generic_rail_advice():
    forward = hit("Maradana to Galle departure 17:25. Train timetable notice 2025/12/02.", 0.6)
    forward["fields"].update(route_origin="Maradana", route_destination="Galle",
                             extraction_method="reviewed_transcription")
    reverse = hit("Galle to Maradana departure 3:15. Train timetable.", 0.9, 1)
    reverse["fields"].update(route_origin="Galle", route_destination="Maradana",
                             extraction_method="reviewed_transcription")
    advice = hit("Train travel from Maradana to Galle offers beautiful landscapes.", 0.95, 2)
    assert relevant_hits("Which Maradana to Galle departures are in the timetable?",
                         [forward, reverse, advice], 4) == [forward]
    assert relevant_hits("What time is the train from Colombo to Kandy?", [forward, advice], 4) == []


def test_timetable_notice_date_is_always_attached_by_the_server():
    reply = {"summary": "A dated timetable lists a departure.", "itinerary": []}
    sources = [{"route_origin": "Maradana", "source_date": "2025-12-02"}]
    groq_service._attach_retrieval_details(reply, sources)
    assert "2025-12-02" in reply["summary"]
    assert "Services may have changed" in reply["summary"]
    assert reply["kb_sources"] == sources


def test_parallel_local_ingestion_cannot_prune_each_others_versions():
    with knowledge_write_lock():
        with pytest.raises(RuntimeError, match="Another knowledge ingestion"):
            with knowledge_write_lock():
                pytest.fail("Second job acquired the same lock")


def test_partial_batch_failure_preserves_all_old_chunks(monkeypatch):
    index = MagicMock()
    index.upsert_records.side_effect = [None, RuntimeError("second batch failed")]
    monkeypatch.setattr(kb_rag, "index", index)
    chunks = [{"document_name": "guide.pdf", "chunk_index": n, "text": f"chunk {n}"} for n in range(91)]
    with pytest.raises(RuntimeError, match="second batch failed"):
        kb_rag.replace_document_chunks("guide.pdf", chunks)
    assert index.upsert_records.call_count == 2
    assert set(index.delete.call_args.kwargs["filter"]) == {"ingestion_id"}


def test_mismatched_document_replacement_never_reaches_pinecone(monkeypatch):
    index = MagicMock()
    monkeypatch.setattr(kb_rag, "index", index)
    with pytest.raises(ValueError, match="preserved"):
        kb_rag.replace_document_chunks("other.pdf", [hit("Valid text")["fields"]])
    index.upsert_records.assert_not_called()


def test_bundled_scanned_timetable_has_fingerprinted_reviewed_text():
    path = Path(__file__).resolve().parents[1] / "docs" / "Train-Timetable.pdf"
    chunks = document_ingest.build_chunks(str(path))
    assert chunks
    assert all(c["page_number"] == 1 and c["source_date"] == "2025-12-02" for c in chunks)
    assert all(c["extraction_method"] == "reviewed_transcription" for c in chunks)
    assert "partial" in chunks[0]["text"].lower()


def test_sidecar_cannot_survive_a_changed_pdf(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"changed pdf")
    path.with_suffix(".pdf.text.json").write_text(json.dumps({"pdf_sha256": "old", "pages": []}))
    with pytest.raises(ValueError, match="no longer matches"):
        pdf_text.read_pdf_pages(str(path))


def test_mixed_pdf_uses_ocr_only_for_empty_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_text, "PdfReader", lambda _: SimpleNamespace(pages=[
        SimpleNamespace(extract_text=lambda: "Readable text"),
        SimpleNamespace(extract_text=lambda: ""),
    ]))
    ocr = MagicMock(return_value={2: "Scanned page text"})
    monkeypatch.setattr(pdf_text, "ocr_pdf_pages", ocr)
    pages = pdf_text.read_pdf_pages(str(tmp_path / "mixed.pdf"))
    ocr.assert_called_once_with(tmp_path / "mixed.pdf", [2])
    assert pages[0]["extraction_method"] == "pdf_text"
    assert pages[1]["extraction_method"] == "ocr_unverified"


@pytest.mark.parametrize("streaming", [False, True])
def test_short_question_reaches_retrieval_prompt_and_final_citations(monkeypatch, streaming):
    import asyncio
    from routes import chat as routes
    from schemas.chat import ChatRequest

    index = MagicMock()
    evidence = hit("Currency: Sri Lanka uses the Sri Lankan rupee, abbreviated LKR.")
    index.search.return_value = SimpleNamespace(result=SimpleNamespace(hits=[SimpleNamespace(**evidence)]))
    monkeypatch.setattr(kb_rag, "index", index)
    monkeypatch.setattr(routes, "get_or_create_conversation", lambda *_a, **_k: SimpleNamespace(conversation_id="test"))
    monkeypatch.setattr(routes, "count_conversation_messages", lambda *_: 0)
    monkeypatch.setattr(routes, "save_chat", MagicMock())
    monkeypatch.setattr(routes, "touch_conversation", MagicMock())
    monkeypatch.setattr(routes, "build_memory_text", lambda *_: "")
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-test-key")
    captured = []
    content = json.dumps({"summary": "Sri Lanka uses the Sri Lankan rupee (LKR).", "itinerary": []})
    def create(**kwargs):
        captured.extend(kwargs["messages"])
        if kwargs.get("stream"):
            return (chunk for chunk in [SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content=content), finish_reason="stop")])])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content), finish_reason="stop")])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(groq_service, "Groq", lambda **_: client)
    request = ChatRequest(message="Currency", session_id="browser")
    if streaming:
        class Connected:
            async def is_disconnected(self):
                return False
        response = routes.chat_stream(request, Connected(), db=object(), current_user=None)
        async def consume():
            return "".join([chunk async for chunk in response.body_iterator])
        wire = asyncio.run(consume())
        final = wire.split("event: done\ndata: ")[1].split("\n\n")[0]
        result = json.loads(final)
    else:
        result = routes.chat(request, db=object(), current_user=None)
    assert index.search.call_args.kwargs["query"]["inputs"]["text"] == "Currency"
    assert evidence["fields"]["text"] in captured[0]["content"]
    assert result["kb_sources"][0]["document_name"] == "guide.pdf"
