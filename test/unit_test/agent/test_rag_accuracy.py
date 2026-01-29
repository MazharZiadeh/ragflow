#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""
RAG accuracy integration test against the RAGTest knowledge base.

Tests that the retrieval pipeline returns correct chunks for known
questions with expected answers.

Usage:
    export RAGFLOW_API_KEY="your-api-key"
    export RAGFLOW_HOST="http://127.0.0.1:9380"  # optional
    pytest test/unit_test/agent/test_rag_accuracy.py -v -s
"""

import os
import sys
import json
import pytest
import requests

HOST = os.getenv("RAGFLOW_HOST", "http://127.0.0.1:9380")
API_KEY = os.getenv("RAGFLOW_API_KEY", "")
DATASET_NAME = "RAGTest"

# Known question-answer pairs for validation
# Format: (question, expected_keywords, expected_source_doc)
QA_PAIRS = [
    (
        "What are the three core components required for machines to exhibit intelligent behavior?",
        ["algorithms", "data", "computing power"],
        "AI.pdf",
    ),
]


def _headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }


def _list_datasets(name=None):
    """List datasets, optionally filtered by name."""
    params = {}
    if name:
        params["name"] = name
    resp = requests.get(f"{HOST}/api/v1/datasets", headers=_headers(), params=params)
    resp.raise_for_status()
    body = resp.json()
    assert body.get("code") == 0, f"list_datasets failed: {body}"
    return body["data"]


def _retrieve(dataset_ids, question, similarity_threshold=0.1, vector_similarity_weight=0.7, top_k=1024, page_size=10):
    """Call the retrieval API."""
    payload = {
        "question": question,
        "dataset_ids": dataset_ids,
        "page": 1,
        "page_size": page_size,
        "similarity_threshold": similarity_threshold,
        "vector_similarity_weight": vector_similarity_weight,
        "top_k": top_k,
    }
    resp = requests.post(f"{HOST}/api/v1/retrieval", headers=_headers(), json=payload)
    resp.raise_for_status()
    body = resp.json()
    assert body.get("code") == 0, f"retrieval failed: {body}"
    return body["data"]


@pytest.fixture(scope="module")
def ragtest_dataset_ids():
    """Resolve RAGTest dataset IDs."""
    if not API_KEY:
        pytest.skip("RAGFLOW_API_KEY not set")

    datasets = _list_datasets(DATASET_NAME)
    if not datasets:
        pytest.skip(f"Dataset '{DATASET_NAME}' not found. Create it and upload AI.pdf first.")

    ids = [ds["id"] for ds in datasets]
    print(f"\nFound RAGTest dataset(s): {ids}")
    return ids


class TestRAGRetrieval:
    """Test that the retrieval pipeline returns relevant chunks."""

    @pytest.mark.parametrize("question,expected_keywords,expected_doc", QA_PAIRS)
    def test_retrieval_returns_relevant_chunks(self, ragtest_dataset_ids, question, expected_keywords, expected_doc):
        """Verify retrieval returns chunks containing expected keywords from expected document."""
        data = _retrieve(ragtest_dataset_ids, question)
        chunks = data.get("chunks", [])

        print(f"\nQuery: {question}")
        print(f"Chunks returned: {len(chunks)}")

        assert len(chunks) > 0, f"No chunks returned for: {question}"

        # Print all chunks for debugging
        for i, chunk in enumerate(chunks):
            content = chunk.get("content_with_weight", chunk.get("content", ""))
            doc_name = chunk.get("document_name", chunk.get("docnm_kwd", "unknown"))
            similarity = chunk.get("similarity", 0)
            print(f"\n--- Chunk {i} (sim={similarity:.4f}, doc={doc_name}) ---")
            print(content[:300])

        # Check that at least one chunk contains expected keywords
        all_content = " ".join(
            chunk.get("content_with_weight", chunk.get("content", "")).lower()
            for chunk in chunks
        )

        found_keywords = [kw for kw in expected_keywords if kw.lower() in all_content]
        missing_keywords = [kw for kw in expected_keywords if kw.lower() not in all_content]

        print(f"\nExpected keywords found: {found_keywords}")
        print(f"Missing keywords: {missing_keywords}")

        assert len(found_keywords) >= len(expected_keywords) // 2 + 1, (
            f"Too few expected keywords found in retrieved chunks. "
            f"Found: {found_keywords}, Missing: {missing_keywords}"
        )

    @pytest.mark.parametrize("question,expected_keywords,expected_doc", QA_PAIRS)
    def test_retrieval_source_document(self, ragtest_dataset_ids, question, expected_keywords, expected_doc):
        """Verify that the expected source document appears in results."""
        data = _retrieve(ragtest_dataset_ids, question)
        chunks = data.get("chunks", [])

        doc_names = set()
        for chunk in chunks:
            doc_name = chunk.get("document_name", chunk.get("docnm_kwd", ""))
            if doc_name:
                doc_names.add(doc_name)

        print(f"\nDocuments in results: {doc_names}")

        assert any(expected_doc.lower() in dn.lower() for dn in doc_names), (
            f"Expected document '{expected_doc}' not found in results. Got: {doc_names}"
        )

    @pytest.mark.parametrize("question,expected_keywords,expected_doc", QA_PAIRS)
    def test_top_chunk_similarity(self, ragtest_dataset_ids, question, expected_keywords, expected_doc):
        """Verify top chunk has reasonable similarity score."""
        data = _retrieve(ragtest_dataset_ids, question)
        chunks = data.get("chunks", [])

        assert len(chunks) > 0

        top_sim = max(chunk.get("similarity", 0) for chunk in chunks)
        print(f"\nTop similarity score: {top_sim:.4f}")

        # Top chunk should have at least 0.1 similarity (our threshold)
        assert top_sim >= 0.1, f"Top similarity {top_sim:.4f} is too low"


class TestSearchWeightConfigurations:
    """Test different search weight configurations to compare accuracy."""

    def test_semantic_heavy_vs_keyword_heavy(self, ragtest_dataset_ids):
        """Compare semantic-heavy (70% vector) vs keyword-heavy (70% keyword) search."""
        question = QA_PAIRS[0][0]
        expected_keywords = QA_PAIRS[0][1]

        # Semantic-heavy: 70% vector (current config)
        semantic_data = _retrieve(ragtest_dataset_ids, question, vector_similarity_weight=0.7)
        semantic_chunks = semantic_data.get("chunks", [])

        # Keyword-heavy: 70% keyword
        keyword_data = _retrieve(ragtest_dataset_ids, question, vector_similarity_weight=0.3)
        keyword_chunks = keyword_data.get("chunks", [])

        def count_keyword_hits(chunks):
            content = " ".join(
                c.get("content_with_weight", c.get("content", "")).lower()
                for c in chunks
            )
            return sum(1 for kw in expected_keywords if kw.lower() in content)

        semantic_hits = count_keyword_hits(semantic_chunks)
        keyword_hits = count_keyword_hits(keyword_chunks)

        print(f"\nSemantic-heavy (70% vector): {semantic_hits}/{len(expected_keywords)} keywords found in {len(semantic_chunks)} chunks")
        print(f"Keyword-heavy (70% keyword): {keyword_hits}/{len(expected_keywords)} keywords found in {len(keyword_chunks)} chunks")

        # Log top similarities for comparison
        if semantic_chunks:
            print(f"Semantic top sim: {max(c.get('similarity', 0) for c in semantic_chunks):.4f}")
        if keyword_chunks:
            print(f"Keyword top sim: {max(c.get('similarity', 0) for c in keyword_chunks):.4f}")

        # At least one configuration should find the majority of keywords
        assert max(semantic_hits, keyword_hits) >= len(expected_keywords) // 2 + 1, (
            f"Neither search configuration found enough keywords. "
            f"Semantic: {semantic_hits}, Keyword: {keyword_hits}"
        )


# Standalone runner for quick manual testing
if __name__ == "__main__":
    if not API_KEY:
        print("Set RAGFLOW_API_KEY environment variable first.")
        print("  export RAGFLOW_API_KEY='your-key-here'")
        sys.exit(1)

    print(f"Host: {HOST}")
    print(f"Dataset: {DATASET_NAME}")
    print("=" * 60)

    # Find dataset
    datasets = _list_datasets(DATASET_NAME)
    if not datasets:
        print(f"ERROR: Dataset '{DATASET_NAME}' not found.")
        sys.exit(1)

    ds_ids = [ds["id"] for ds in datasets]
    print(f"Dataset IDs: {ds_ids}\n")

    # Run each QA pair
    for question, expected_kw, expected_doc in QA_PAIRS:
        print(f"Q: {question}")
        print(f"Expected: {expected_kw} from {expected_doc}")
        print("-" * 40)

        data = _retrieve(ds_ids, question)
        chunks = data.get("chunks", [])

        if not chunks:
            print("  NO CHUNKS RETURNED - retrieval failed\n")
            continue

        all_content = ""
        for i, chunk in enumerate(chunks[:5]):
            content = chunk.get("content_with_weight", chunk.get("content", ""))
            doc_name = chunk.get("document_name", chunk.get("docnm_kwd", "?"))
            sim = chunk.get("similarity", 0)
            print(f"  [{i}] sim={sim:.4f} doc={doc_name}")
            print(f"      {content[:200]}")
            all_content += " " + content.lower()

        found = [kw for kw in expected_kw if kw.lower() in all_content]
        missing = [kw for kw in expected_kw if kw.lower() not in all_content]
        status = "PASS" if len(found) >= len(expected_kw) // 2 + 1 else "FAIL"
        print(f"\n  Result: {status} | Found: {found} | Missing: {missing}")
        print("=" * 60)
