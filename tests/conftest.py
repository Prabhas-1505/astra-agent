import os

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")


class FakeStructuredLLM:
    """Stands in for `llm.with_structured_output(Model).invoke(prompt)`.

    Returns canned responses in order, one per call, regardless of prompt —
    the point of these unit tests is the graph's branching logic, not the
    model's reasoning (that's what the eval harness measures).
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def invoke(self, prompt):
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


class FakeLLM:
    def __init__(self, responses):
        self._structured = FakeStructuredLLM(responses)

    def with_structured_output(self, _model):
        return self._structured


class FakeEmbeddings:
    """Deterministic fake embedding: hashes text into a fixed vector so tests
    don't depend on real OpenAI calls. `_embeddings` is a pydantic model
    instance, which forbids ad-hoc attribute assignment, so tests must
    replace the whole object rather than patch a method on it.
    """

    def __init__(self, vectors=None):
        self._vectors = vectors or {}

    def embed_query(self, text):
        if text in self._vectors:
            return self._vectors[text]
        import hashlib

        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 for b in digest[:16]]


@pytest.fixture
def fake_embeddings(monkeypatch):
    from app.kb import store as kb_store

    fake = FakeEmbeddings()
    monkeypatch.setattr(kb_store, "_embeddings", fake)
    return fake
