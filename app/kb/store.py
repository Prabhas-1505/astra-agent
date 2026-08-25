import json
import os
from pathlib import Path

import numpy as np
from langchain_openai import OpenAIEmbeddings

from app.kb.runbooks import RUNBOOKS

DEFAULT_STORE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "past_cases.json"

_embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
_runbook_embeddings_cache: list[list[float]] | None = None


def _cosine(a: list[float], b: list[float]) -> float:
    a_vec, b_vec = np.array(a), np.array(b)
    return float(np.dot(a_vec, b_vec) / (np.linalg.norm(a_vec) * np.linalg.norm(b_vec) + 1e-8))


def _store_path() -> Path:
    path = Path(os.environ.get("ASTRA_KB_PATH", str(DEFAULT_STORE_PATH)))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load() -> list[dict]:
    path = _store_path()
    if path.exists():
        return json.loads(path.read_text())
    return []


def _save(cases: list[dict]) -> None:
    _store_path().write_text(json.dumps(cases, indent=2))


def retrieve_similar(context_text: str, k: int = 2) -> list[dict]:
    cases = _load()
    if not cases:
        return []
    query_vec = _embeddings.embed_query(context_text)
    scored = [(_cosine(query_vec, case["embedding"]), case) for case in cases]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [case for _, case in scored[:k]]


def retrieve_runbooks(context_text: str, k: int = 1) -> list[dict]:
    global _runbook_embeddings_cache
    if not RUNBOOKS:
        return []
    if _runbook_embeddings_cache is None:
        _runbook_embeddings_cache = [_embeddings.embed_query(rb["body"]) for rb in RUNBOOKS]

    query_vec = _embeddings.embed_query(context_text)
    scored = [
        (_cosine(query_vec, vec), rb)
        for vec, rb in zip(_runbook_embeddings_cache, RUNBOOKS)
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [rb for _, rb in scored[:k]]


def write_case(context_text: str, root_cause: dict | None, outcome: str) -> None:
    cases = _load()
    cases.append(
        {
            "context_summary": context_text[:800],
            "root_cause": root_cause,
            "outcome": outcome,
            "embedding": _embeddings.embed_query(context_text),
        }
    )
    _save(cases)
