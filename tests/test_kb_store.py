from app.kb import store as kb_store
from tests.conftest import FakeEmbeddings


def _reload_with_tmp_path(monkeypatch, tmp_path):
    kb_path = tmp_path / "cases.json"
    monkeypatch.setenv("ASTRA_KB_PATH", str(kb_path))
    return kb_path


def test_retrieve_on_empty_store_returns_nothing(monkeypatch, tmp_path, fake_embeddings):
    _reload_with_tmp_path(monkeypatch, tmp_path)
    assert kb_store.retrieve_similar("any incident context") == []


def test_write_then_retrieve_round_trip(monkeypatch, tmp_path, fake_embeddings):
    kb_path = _reload_with_tmp_path(monkeypatch, tmp_path)

    kb_store.write_case(
        "OSPF adjacency down on R1 due to shutdown interface",
        {"statement": "Gi0/0 shut down", "confidence": 0.95, "recommended_fix": "no shutdown"},
        "approved",
    )

    assert kb_path.exists()
    results = kb_store.retrieve_similar("OSPF adjacency down on R1 due to shutdown interface", k=2)
    assert len(results) == 1
    assert results[0]["outcome"] == "approved"
    assert results[0]["root_cause"]["statement"] == "Gi0/0 shut down"


def test_retrieve_respects_k_and_ranks_by_similarity(monkeypatch, tmp_path):
    _reload_with_tmp_path(monkeypatch, tmp_path)

    # Explicit, test-controlled embeddings so similarity ranking is unambiguous.
    vectors = {
        "query": [1.0, 0.0, 0.0],
        "closest match": [0.99, 0.01, 0.0],
        "distant match": [0.0, 1.0, 0.0],
        "middle match": [0.6, 0.4, 0.0],
    }
    monkeypatch.setattr(kb_store, "_embeddings", FakeEmbeddings(vectors))

    for text, outcome in [
        ("distant match", "approved"),
        ("closest match", "approved"),
        ("middle match", "rejected"),
    ]:
        kb_store.write_case(text, {"statement": text, "confidence": 0.5, "recommended_fix": "n/a"}, outcome)

    results = kb_store.retrieve_similar("query", k=2)

    assert [r["context_summary"] for r in results] == ["closest match", "middle match"]
