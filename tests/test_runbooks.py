from app.kb import store as kb_store
from app.kb.runbooks import RUNBOOKS
from tests.conftest import FakeEmbeddings


def test_retrieve_runbooks_ranks_by_similarity(monkeypatch):
    assert len(RUNBOOKS) >= 2  # otherwise this test isn't exercising ranking

    vectors = {rb["body"]: [0.0, 0.0, 1.0] for rb in RUNBOOKS}
    target_title = RUNBOOKS[0]["title"]
    vectors[RUNBOOKS[0]["body"]] = [1.0, 0.0, 0.0]
    vectors["query text"] = [1.0, 0.0, 0.0]

    monkeypatch.setattr(kb_store, "_embeddings", FakeEmbeddings(vectors))
    monkeypatch.setattr(kb_store, "_runbook_embeddings_cache", None)

    results = kb_store.retrieve_runbooks("query text", k=1)

    assert results[0]["title"] == target_title


def test_retrieve_runbooks_caches_embeddings(monkeypatch):
    calls = []
    fake = FakeEmbeddings()
    original_embed = fake.embed_query

    def counting_embed(text):
        calls.append(text)
        return original_embed(text)

    fake.embed_query = counting_embed
    monkeypatch.setattr(kb_store, "_embeddings", fake)
    monkeypatch.setattr(kb_store, "_runbook_embeddings_cache", None)

    kb_store.retrieve_runbooks("first query")
    calls_after_first = len(calls)
    kb_store.retrieve_runbooks("second query")

    # second call should only embed the query itself, not re-embed every runbook
    assert len(calls) - calls_after_first == 1
