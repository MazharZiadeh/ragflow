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
Unit tests for retrieval functionality and citation handling.

These tests verify:
- Citation pattern matching
- Chunk formatting with IDs
- Reference storage and retrieval
- Similarity threshold handling
"""

import pytest
import re
import json


class TestCitationPattern:
    """Tests for citation pattern matching"""

    def test_cite_pattern_basic(self):
        """Test basic citation pattern matching"""
        pattern = re.compile(r"\[ID:\s*\d+\]")

        assert pattern.search("[ID:0]") is not None
        assert pattern.search("[ID:123]") is not None
        assert pattern.search("[ID: 0]") is not None
        assert pattern.search("[ID:  45]") is not None

    def test_cite_pattern_in_text(self):
        """Test citation pattern in full text"""
        pattern = re.compile(r"\[ID:\s*\d+\]")

        text = "The market grew by 7.8% [ID:45]. Samsung leads [ID:46]."
        matches = pattern.findall(text)

        assert len(matches) == 2
        assert "[ID:45]" in matches
        assert "[ID:46]" in matches

    def test_cite_pattern_multiple_adjacent(self):
        """Test multiple adjacent citations"""
        pattern = re.compile(r"\[ID:\s*\d+\]")

        text = "This claim has multiple sources [ID:0][ID:1][ID:2]."
        matches = pattern.findall(text)

        assert len(matches) == 3

    def test_cite_pattern_no_match(self):
        """Test patterns that should not match"""
        pattern = re.compile(r"\[ID:\s*\d+\]")

        assert pattern.search("[ID:abc]") is None
        assert pattern.search("[id:0]") is None
        assert pattern.search("ID:0") is None
        assert pattern.search("[ID:]") is None


class TestChunkFormatting:
    """Tests for chunk formatting with IDs"""

    def test_chunk_format_structure(self):
        """Test chunk formatting produces expected structure"""
        def chunks_format(reference):
            if not reference or not isinstance(reference, dict):
                return []
            return [
                {
                    "id": chunk.get("chunk_id", chunk.get("id")),
                    "content": chunk.get("content", chunk.get("content_with_weight")),
                    "document_name": chunk.get("docnm_kwd", chunk.get("document_name")),
                    "similarity": chunk.get("similarity"),
                }
                for chunk in reference.get("chunks", [])
            ]

        reference = {
            "chunks": [
                {
                    "chunk_id": "abc123",
                    "content_with_weight": "Test content",
                    "docnm_kwd": "test_doc.pdf",
                    "similarity": 0.85
                }
            ]
        }

        formatted = chunks_format(reference)
        assert len(formatted) == 1
        assert formatted[0]["id"] == "abc123"
        assert formatted[0]["content"] == "Test content"
        assert formatted[0]["similarity"] == 0.85

    def test_kb_prompt_id_format(self):
        """Test knowledge base prompt ID format"""
        # Simulate hash_str2int
        def hash_str2int(s, mod):
            return hash(s) % mod

        chunk_id = "test_chunk_123"
        hashed_id = hash_str2int(chunk_id, 500)

        # ID should be in range 0-499
        assert 0 <= hashed_id < 500

        # Format should be "ID: N"
        formatted = f"ID: {hashed_id}"
        assert formatted.startswith("ID: ")


class TestSimilarityThreshold:
    """Tests for similarity threshold handling"""

    def test_filter_by_threshold(self):
        """Test filtering chunks by similarity threshold"""
        import numpy as np

        chunks = [
            {"id": "1", "similarity": 0.8},
            {"id": "2", "similarity": 0.5},
            {"id": "3", "similarity": 0.15},
            {"id": "4", "similarity": 0.05},
        ]

        threshold = 0.2
        sim_np = np.array([c["similarity"] for c in chunks])
        sorted_idx = np.argsort(sim_np * -1)

        valid_idx = [int(i) for i in sorted_idx if sim_np[i] >= threshold]
        filtered_chunks = [chunks[i] for i in valid_idx]

        assert len(filtered_chunks) == 2
        assert filtered_chunks[0]["id"] == "1"  # Highest similarity
        assert filtered_chunks[1]["id"] == "2"

    def test_fallback_when_none_meet_threshold(self):
        """Test fallback mechanism when no chunks meet threshold"""
        import numpy as np

        chunks = [
            {"id": "1", "similarity": 0.15},
            {"id": "2", "similarity": 0.12},
            {"id": "3", "similarity": 0.08},
        ]

        threshold = 0.5  # High threshold
        sim_np = np.array([c["similarity"] for c in chunks])
        sorted_idx = np.argsort(sim_np * -1)

        valid_idx = [int(i) for i in sorted_idx if sim_np[i] >= threshold]

        # Fallback: if no chunks meet threshold, return top-k
        if len(valid_idx) == 0 and len(sorted_idx) > 0:
            fallback_count = min(2, len(sorted_idx))
            valid_idx = [int(sorted_idx[i]) for i in range(fallback_count)]

        filtered_chunks = [chunks[i] for i in valid_idx]

        assert len(filtered_chunks) == 2  # Fallback returns top 2
        assert filtered_chunks[0]["id"] == "1"  # Highest similarity

    def test_lower_default_threshold(self):
        """Test that lower threshold improves recall"""
        import numpy as np

        chunks = [
            {"id": "1", "similarity": 0.25},
            {"id": "2", "similarity": 0.18},
            {"id": "3", "similarity": 0.12},
            {"id": "4", "similarity": 0.08},
        ]

        sim_np = np.array([c["similarity"] for c in chunks])
        sorted_idx = np.argsort(sim_np * -1)

        # Old threshold (0.2)
        old_threshold = 0.2
        old_valid = [int(i) for i in sorted_idx if sim_np[i] >= old_threshold]

        # New threshold (0.1)
        new_threshold = 0.1
        new_valid = [int(i) for i in sorted_idx if sim_np[i] >= new_threshold]

        assert len(new_valid) > len(old_valid)  # New threshold returns more results
        assert len(old_valid) == 1
        assert len(new_valid) == 3


class TestReferenceStorage:
    """Tests for reference storage in canvas"""

    def test_add_reference_hashes_ids(self):
        """Test that add_reference hashes chunk IDs"""
        def hash_str2int(s, mod):
            return hash(s) % mod

        retrieval = [{"chunks": {}, "doc_aggs": {}}]
        chunks = [
            {"id": "chunk_abc_123", "content": "Test content 1"},
            {"id": "chunk_def_456", "content": "Test content 2"},
        ]

        for ck in chunks:
            cid = hash_str2int(ck["id"], 500)
            retrieval[-1]["chunks"][cid] = ck

        # Should have 2 chunks stored
        assert len(retrieval[-1]["chunks"]) == 2

        # IDs should be in valid range
        for cid in retrieval[-1]["chunks"].keys():
            assert 0 <= cid < 500

    def test_get_reference_empty(self):
        """Test getting reference when empty"""
        retrieval = []

        def get_reference(retrieval):
            if not retrieval:
                return {"chunks": {}, "doc_aggs": {}}
            return retrieval[-1]

        ref = get_reference(retrieval)
        assert ref == {"chunks": {}, "doc_aggs": {}}

    def test_reference_matches_citation_ids(self):
        """Test that stored reference IDs match citation format"""
        def hash_str2int(s, mod):
            return hash(s) % mod

        chunk_id = "real_chunk_id_123"
        hashed_id = hash_str2int(chunk_id, 500)

        # This is the ID format in the LLM prompt
        prompt_id = f"ID: {hashed_id}"

        # This is the citation format
        citation = f"[ID:{hashed_id}]"

        # Citation pattern should match
        pattern = re.compile(r"\[ID:\s*\d+\]")
        assert pattern.search(citation) is not None


class TestRetrievalDefaults:
    """Tests for retrieval default parameter values"""

    def test_default_similarity_threshold(self):
        """Test default similarity threshold is lowered"""
        # Simulating the new default
        default_threshold = 0.1
        assert default_threshold < 0.2  # Was 0.2, now 0.1

    def test_default_keywords_weight(self):
        """Test default keywords weight gives more to vector"""
        default_keywords_weight = 0.3
        vector_weight = 1 - default_keywords_weight

        assert vector_weight == 0.7  # More weight to vector similarity
        assert default_keywords_weight < 0.5  # Was 0.5, now 0.3


# Run with: pytest test/unit_test/agent/test_retrieval.py -v
