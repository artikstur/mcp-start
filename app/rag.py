from __future__ import annotations

import argparse
import asyncio
import html
import json
import math
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI


BASE_DIR = Path(__file__).resolve().parent.parent
CORPUS_DIR = BASE_DIR / "docs" / "corpus"
INDEX_PATH = Path(os.getenv("MCP_START_INDEX_PATH", str(BASE_DIR / "data" / "rag_index.json")))
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150


def chunk_text(
    text: str,
    source: str,
    title: str = "",
    url: str = "",
    size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[dict]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk size must be positive and overlap smaller than size")
    if not text:
        return []

    chunks = []
    step = size - overlap
    for start in range(0, len(text), step):
        part = text[start : start + size]
        if not part:
            break
        chunks.append({
            "chunk_id": f"{source}#{len(chunks) + 1}",
            "source": source,
            "title": title,
            "url": url,
            "text": part,
        })
        if start + size >= len(text):
            break
    return chunks


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have the same dimensions")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def rank_chunks(
    query_embedding: list[float], chunks: list[dict], top_k: int = 3
) -> list[tuple[dict, float]]:
    if top_k < 0:
        raise ValueError("top_k must not be negative")
    ranked = [
        (chunk, cosine_similarity(query_embedding, chunk["embedding"]))
        for chunk in chunks
    ]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:top_k]


def source_recall_at_k(
    expected_sources: list[str], retrieved_chunks: list[dict], k: int = 3
) -> float:
    expected = set(expected_sources)
    if not expected:
        return 1.0
    retrieved = {chunk["source"] for chunk in retrieved_chunks[:k]}
    return len(expected & retrieved) / len(expected)


def _read_corpus(corpus_dir: Path = CORPUS_DIR) -> list[dict]:
    documents = []
    for path in sorted(corpus_dir.glob("*.txt")):
        lines = path.read_text(encoding="utf-8").splitlines()
        title = lines[0].removeprefix("TITLE: ") if lines and lines[0].startswith("TITLE: ") else path.stem
        url = lines[1].removeprefix("SOURCE: ") if len(lines) > 1 and lines[1].startswith("SOURCE: ") else ""
        body_start = next((i + 1 for i, line in enumerate(lines) if not line.strip()), len(lines))
        body = "\n".join(lines[body_start:]).strip()
        if body:
            documents.append({"source": path.name, "title": title, "url": url, "text": body})
    return documents


def _embedding_client() -> tuple[AsyncOpenAI, str]:
    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Не найден OPENROUTER_API_KEY в .env")
    model = os.getenv("OPENROUTER_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
    client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    return client, model


async def _embed(texts: list[str]) -> list[list[float]]:
    vectors, _ = await _embed_with_usage(texts)
    return vectors


async def _embed_with_usage(texts: list[str]) -> tuple[list[list[float]], dict]:
    if not texts:
        return [], {"embedding_tokens": 0, "embedding_cost_usd": 0.0, "cost_is_estimated": False}
    client, model = _embedding_client()
    response = await client.embeddings.create(model=model, input=texts)
    vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    usage = response.usage
    tokens = getattr(usage, "total_tokens", 0) if usage else 0
    price_per_million = float(os.getenv("OPENROUTER_EMBEDDING_PRICE_PER_MILLION", "0.02"))
    reported_cost = None
    if usage is not None:
        extra = getattr(usage, "model_extra", None) or {}
        reported_cost = extra.get("cost")
        if reported_cost is None and hasattr(usage, "model_dump"):
            reported_cost = usage.model_dump().get("cost")
    estimated = reported_cost is None
    cost = float(reported_cost) if reported_cost is not None else tokens * price_per_million / 1_000_000
    return vectors, {"embedding_tokens": tokens, "embedding_cost_usd": cost,
                     "cost_is_estimated": estimated}


async def build_index(
    corpus_dir: Path = CORPUS_DIR,
    index_path: Path = INDEX_PATH,
) -> list[dict]:
    documents = _read_corpus(corpus_dir)
    if not documents:
        raise ValueError(f"no .txt documents found in {corpus_dir}")

    chunks = [
        chunk
        for document in documents
        for chunk in chunk_text(
            document["text"], document["source"], document["title"], document["url"]
        )
    ]
    vectors = await _embed([chunk["text"] for chunk in chunks])
    for chunk, vector in zip(chunks, vectors):
        chunk["embedding"] = vector

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps({"model": os.getenv("OPENROUTER_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL), "chunks": chunks}, ensure_ascii=False), encoding="utf-8")
    return chunks


async def search_documents(
    query: str,
    index_path: Path = INDEX_PATH,
    top_k: int = 3,
    threshold: float = 0.2,
) -> dict:
    if not index_path.exists():
        await build_index(index_path=index_path)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    query_vectors, embedding_usage = await _embed_with_usage([query])
    query_vector = query_vectors[0]
    ranked = rank_chunks(query_vector, index["chunks"], top_k=top_k)
    sources = [
        {
            "id": i,
            "chunk_id": chunk["chunk_id"],
            "source": chunk["source"],
            "title": chunk["title"],
            "url": chunk["url"],
            "score": round(score, 4),
            "text": f'<doc id="{i}">\n{html.escape(chunk["text"])}\n</doc>',
        }
        for i, (chunk, score) in enumerate(ranked, start=1)
    ]
    return {
        "found": bool(ranked and ranked[0][1] >= threshold),
        "sources": sources if ranked and ranked[0][1] >= threshold else [],
        "usage": embedding_usage,
    }


async def evaluate_recall(
    golden_path: Path,
    index_path: Path = INDEX_PATH,
    k: int = 3,
) -> dict:
    rows = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    index = json.loads(index_path.read_text(encoding="utf-8"))
    query_vectors = await _embed([row["q"] for row in rows])
    hits = 0
    details = []
    for row, vector in zip(rows, query_vectors):
        ranked = rank_chunks(vector, index["chunks"], top_k=k)
        retrieved = [chunk for chunk, _ in ranked]
        recall = source_recall_at_k(row["expected_sources"], retrieved, k)
        hits += int(recall == 1.0)
        details.append({"id": row["id"], "recall_at_k": recall,
                        "expected_sources": row["expected_sources"],
                        "retrieved_sources": [chunk["source"] for chunk in retrieved]})
    return {"questions": len(rows), "k": k,
            "recall_at_k": hits / len(rows) if rows else 1.0,
            "hits": hits, "details": details}


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Build the local MCP document embedding index")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--build", action="store_true", help="rebuild and save the embedding index")
    actions.add_argument("--evaluate", action="store_true", help="measure recall@k using a JSONL gold set")
    parser.add_argument("--golden", type=Path, default=BASE_DIR / "eval" / "rag_golden.jsonl")
    parser.add_argument("--k", type=int, default=3)
    args = parser.parse_args()
    if args.build:
        chunks = await build_index()
        result = {"indexed_chunks": len(chunks), "index": str(INDEX_PATH)}
    else:
        result = await evaluate_recall(args.golden, k=args.k)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
