import unittest

from app.rag import _read_corpus, chunk_text, rank_chunks, source_recall_at_k


class RAGTests(unittest.TestCase):
    def test_corpus_includes_official_exam_schedule_page(self):
        documents = _read_corpus()
        exam_page = next(doc for doc in documents if doc["source"] == "exam_schedules.txt")

        self.assertEqual(
            exam_page["url"],
            "https://kpfu.ru/biology-medicine/uchebnyj-process/raspisanie-zachjotov-i-ekzamenov",
        )
        self.assertIn("не содержит календаря экзаменов", exam_page["text"])

    def test_chunks_overlap_and_keep_source_metadata(self):
        text = "a" * 1800

        chunks = chunk_text(
            text,
            source="source.txt",
            title="Source title",
            url="https://example.test/source",
        )

        self.assertEqual([len(chunk["text"]) for chunk in chunks], [800, 800, 500])
        self.assertEqual(chunks[0]["text"][-150:], chunks[1]["text"][:150])
        self.assertEqual(chunks[1]["text"][-150:], chunks[2]["text"][:150])
        self.assertEqual(chunks[1]["source"], "source.txt")
        self.assertEqual(chunks[1]["title"], "Source title")
        self.assertEqual(chunks[1]["url"], "https://example.test/source")

    def test_rank_chunks_returns_best_cosine_matches(self):
        chunks = [
            {"chunk_id": "c1", "embedding": [1.0, 0.0]},
            {"chunk_id": "c2", "embedding": [0.0, 1.0]},
            {"chunk_id": "c3", "embedding": [0.8, 0.2]},
        ]

        results = rank_chunks([1.0, 0.0], chunks, top_k=2)

        self.assertEqual([chunk["chunk_id"] for chunk, _ in results], ["c1", "c3"])
        self.assertAlmostEqual(results[0][1], 1.0)

    def test_source_recall_at_k_counts_expected_sources_in_retrieved_chunks(self):
        expected = ["structure.txt", "programs.txt"]
        retrieved = [
            {"source": "structure.txt"},
            {"source": "research.txt"},
            {"source": "programs.txt"},
        ]

        self.assertEqual(source_recall_at_k(expected, retrieved, k=2), 0.5)
        self.assertEqual(source_recall_at_k(expected, retrieved, k=3), 1.0)


if __name__ == "__main__":
    unittest.main()
