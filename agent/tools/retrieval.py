#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
import asyncio
import inspect
from functools import partial
import json
import logging
import os
import re
from abc import ABC
from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from common.constants import LLMType
from api.db.services.document_service import DocumentService
from common.metadata_utils import apply_meta_data_filter
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.llm_service import LLMBundle
from api.db.services.memory_service import MemoryService
from api.db.joint_services import memory_message_service
from common import settings
from common.connection_utils import timeout
from rag.app.tag import label_question
from rag.nlp.search import index_name
from rag.prompts.generator import cross_languages, kb_prompt, memory_prompt


class RetrievalParam(ToolParamBase):
    """
    Define the Retrieval component parameters.
    """

    def __init__(self):
        self.meta:ToolMeta = {
            "name": "search_kb",
            "description": "Search the knowledge base. Use this tool before answering any factual question.",
            "parameters": {
                "query": {
                    "type": "string",
                    "description": "The search query. Use the key terms from the user's question.",
                    "default": "",
                    "required": True
                }
            }
        }
        super().__init__()
        self.function_name = "search_kb"
        self.description = "Search the knowledge base. Use this tool before answering any factual question."
        self.similarity_threshold = 0.1  # Low threshold to prevent over-filtering
        self.keywords_similarity_weight = 0.3  # 30% keywords, 70% vector (semantic-heavy for factual Q&A)
        self.top_n = 10  # More context for the LLM
        self.top_k = 128
        self.kb_ids = []
        self.memory_ids = []
        self.kb_vars = []
        self.rerank_id = ""
        self.empty_response = ""
        self.use_kg = False
        self.cross_languages = []
        self.toc_enhance = False
        self.meta_data_filter={}

    def check(self):
        self.check_decimal_float(self.similarity_threshold, "[Retrieval] Similarity threshold")
        self.check_decimal_float(self.keywords_similarity_weight, "[Retrieval] Keyword similarity weight")
        self.check_positive_number(self.top_n, "[Retrieval] Top N")

    def get_input_form(self) -> dict[str, dict]:
        return {
            "query": {
                "name": "Query",
                "type": "line"
            }
        }

class Retrieval(ToolBase, ABC):
    component_name = "Retrieval"

    async def _retrieve_kb(self, query_text: str):
        kb_ids: list[str] = []
        for id in self._param.kb_ids:
            if id.find("@") < 0:
                kb_ids.append(id)
                continue
            kb_nm = self._canvas.get_variable_value(id)
            # if kb_nm is a list
            kb_nm_list = kb_nm if isinstance(kb_nm, list) else [kb_nm]
            for nm_or_id in kb_nm_list:
                e, kb = KnowledgebaseService.get_by_name(nm_or_id,
                                                         self._canvas._tenant_id)
                if not e:
                    e, kb = KnowledgebaseService.get_by_id(nm_or_id)
                    if not e:
                        raise Exception(f"Dataset({nm_or_id}) does not exist.")
                kb_ids.append(kb.id)

        filtered_kb_ids: list[str] = list(set([kb_id for kb_id in kb_ids if kb_id]))

        kbs = KnowledgebaseService.get_by_ids(filtered_kb_ids)
        if not kbs:
            raise Exception("No dataset is selected.")

        embd_nms = list(set([kb.embd_id for kb in kbs]))
        assert len(embd_nms) == 1, "Knowledge bases use different embedding models."

        embd_mdl = None
        if embd_nms:
            embd_mdl = LLMBundle(self._canvas.get_tenant_id(), LLMType.EMBEDDING, embd_nms[0])

        rerank_mdl = None
        if self._param.rerank_id:
            rerank_mdl = LLMBundle(kbs[0].tenant_id, LLMType.RERANK, self._param.rerank_id)

        vars = self.get_input_elements_from_text(query_text)
        vars = {k: o["value"] for k, o in vars.items()}
        query = self.string_format(query_text, vars)

        doc_ids = []
        if self._param.meta_data_filter != {}:
            metas = DocumentService.get_meta_by_kbs(kb_ids)

            def _resolve_manual_filter(flt: dict) -> dict:
                pat = re.compile(self.variable_ref_patt)
                s = flt.get("value", "")
                out_parts = []
                last = 0

                for m in pat.finditer(s):
                    out_parts.append(s[last:m.start()])
                    key = m.group(1)
                    v = self._canvas.get_variable_value(key)
                    if v is None:
                        rep = ""
                    elif isinstance(v, partial):
                        buf = []
                        for chunk in v():
                            buf.append(chunk)
                        rep = "".join(buf)
                    elif isinstance(v, str):
                        rep = v
                    else:
                        rep = json.dumps(v, ensure_ascii=False)

                    out_parts.append(rep)
                    last = m.end()

                out_parts.append(s[last:])
                flt["value"] = "".join(out_parts)
                return flt

            chat_mdl = None
            if self._param.meta_data_filter.get("method") in ["auto", "semi_auto"]:
                chat_mdl = LLMBundle(self._canvas.get_tenant_id(), LLMType.CHAT)

            doc_ids = await apply_meta_data_filter(
                self._param.meta_data_filter,
                metas,
                query,
                chat_mdl,
                doc_ids,
                _resolve_manual_filter if self._param.meta_data_filter.get("method") == "manual" else None,
            )

        if self._param.cross_languages:
            query = await cross_languages(kbs[0].tenant_id, None, query, self._param.cross_languages)

        if kbs:
            query = re.sub(r"^user[:：\s]*", "", query, flags=re.IGNORECASE)
            logging.info(f"[SEARCH_QUERY] '{query[:120]}' (top_n={self._param.top_n})")
            tenant_ids = [kb.tenant_id for kb in kbs]
            kbinfos = settings.retriever.retrieval(
                query,
                embd_mdl,
                tenant_ids,
                filtered_kb_ids,
                1,
                self._param.top_n,
                self._param.similarity_threshold,
                1 - self._param.keywords_similarity_weight,
                doc_ids=doc_ids,
                aggs=False,
                rerank_mdl=rerank_mdl,
                rank_feature=label_question(query, kbs),
            )
            if self.check_if_canceled("Retrieval processing"):
                return

            # Log per-chunk scores from primary retrieval (before dual-pass)
            for ci, ck in enumerate(kbinfos["chunks"]):
                logging.info(
                    f"[PRIMARY {ci}] sim={ck.get('similarity',0):.4f} "
                    f"vec={ck.get('vector_similarity',0):.4f} "
                    f"term={ck.get('term_similarity',0):.4f} "
                    f"doc={ck.get('docnm_kwd','')} "
                    f"content={str(ck.get('content_with_weight',''))[:80]}"
                )

            # Dual-pass: supplementary BM25-only search to catch chunks that
            # are keyword-relevant but have low vector similarity.  ES fusion
            # is 5%/95% keyword/vector, so keyword-heavy chunks get buried.
            # Uses search() directly (not retrieval()) to avoid rerank crash
            # with empty vectors when embd_mdl=None.
            try:
                kw_req = {
                    "kb_ids": filtered_kb_ids,
                    "doc_ids": doc_ids,
                    "page": 1,
                    "size": self._param.top_n,
                    "question": query,
                    "vector": True,
                    "topk": 128,
                    "similarity": 0.0,
                    "available_int": 1,
                }
                sres = settings.retriever.search(
                    kw_req,
                    [index_name(tid) for tid in tenant_ids],
                    filtered_kb_ids,
                    emb_mdl=None,
                    highlight=False,
                    rank_feature=label_question(query, kbs),
                )
                # Convert SearchResult to chunk dicts
                kw_chunks = []
                for chunk_id in sres.ids:
                    field = sres.field[chunk_id]
                    kw_chunks.append({
                        "chunk_id": chunk_id,
                        "content_with_weight": field.get("content_with_weight", ""),
                        "content_ltks": field.get("content_ltks", ""),
                        "doc_id": field.get("doc_id", ""),
                        "docnm_kwd": field.get("docnm_kwd", ""),
                        "kb_id": field.get("kb_id", ""),
                        "important_kwd": field.get("important_kwd", []),
                        "image_id": field.get("img_id", ""),
                        "similarity": float(field.get("_score", 0.0)),
                        "vector_similarity": 0.0,
                        "term_similarity": float(field.get("_score", 0.0)),
                        "positions": field.get("position_int", []),
                    })
                # Merge unique keyword chunks into primary results
                primary_ids = {ck.get("chunk_id") or ck.get("id", "") for ck in kbinfos["chunks"]}
                kw_unique = [ck for ck in kw_chunks
                             if ck["chunk_id"] not in primary_ids]
                max_supplement = max(3, self._param.top_n // 2)
                if kw_unique:
                    # Replace lowest-scoring primary chunks with keyword hits
                    kw_added = kw_unique[:max_supplement]
                    # Normalize BM25 scores: raw ES _score (e.g. 5.4) is not
                    # on the same [0,1] scale as primary cosine similarity.
                    # Set BM25 chunks slightly below the min primary score so
                    # they supplement but don't dominate sorting/filtering.
                    min_primary_sim = min((c.get("similarity", 0) for c in kbinfos["chunks"]), default=0.1)
                    bm25_sim = max(min_primary_sim * 0.95, 0.05)
                    for ck in kw_added:
                        ck["_raw_score"] = ck["similarity"]  # preserve raw ES score for logging
                        ck["similarity"] = bm25_sim
                        ck["term_similarity"] = bm25_sim
                        ck["vector_similarity"] = 0.0
                    # Sort primary by similarity ascending so we drop the worst
                    kbinfos["chunks"].sort(key=lambda c: c.get("similarity", 0))
                    slots = min(len(kw_added), len(kbinfos["chunks"]))
                    kbinfos["chunks"] = kbinfos["chunks"][slots:] + kw_added
                    # Re-sort descending for presentation
                    kbinfos["chunks"].sort(key=lambda c: c.get("similarity", 0), reverse=True)
                    logging.info(f"[DUAL-PASS] Added {len(kw_added)} keyword chunks (norm_sim={bm25_sim:.4f}), dropped {slots} lowest-scored primary chunks")
                    for ki, kc in enumerate(kw_added):
                        logging.info(
                            f"[BM25 {ki}] raw_es_score={kc.get('_raw_score', '?')} "
                            f"norm_sim={kc.get('similarity',0):.4f} "
                            f"doc={kc.get('docnm_kwd','')} "
                            f"content={str(kc.get('content_with_weight',''))[:80]}"
                        )
            except Exception as e:
                logging.warning(f"[DUAL-PASS] Keyword search failed: {e}")

            if self._param.toc_enhance:
                chat_mdl = LLMBundle(self._canvas._tenant_id, LLMType.CHAT)
                cks = await settings.retriever.retrieval_by_toc(query, kbinfos["chunks"], [kb.tenant_id for kb in kbs],
                                                          chat_mdl, self._param.top_n)
                if self.check_if_canceled("Retrieval processing"):
                    return
                if cks:
                    kbinfos["chunks"] = cks
            kbinfos["chunks"] = settings.retriever.retrieval_by_children(kbinfos["chunks"],
                                                                         [kb.tenant_id for kb in kbs])
            if self._param.use_kg:
                _kg_result = settings.kg_retriever.retrieval(query,
                                                     [kb.tenant_id for kb in kbs],
                                                     kb_ids,
                                                     embd_mdl,
                                                     LLMBundle(self._canvas.get_tenant_id(), LLMType.CHAT))
                ck = (await _kg_result) if inspect.isawaitable(_kg_result) else _kg_result
                if self.check_if_canceled("Retrieval processing"):
                    return
                if ck["content_with_weight"]:
                    kbinfos["chunks"].insert(0, ck)
        else:
            kbinfos = {"chunks": [], "doc_aggs": []}

        if self._param.use_kg and kbs:
            _kg_result = settings.kg_retriever.retrieval(query, [kb.tenant_id for kb in kbs], filtered_kb_ids, embd_mdl,
                                                 LLMBundle(kbs[0].tenant_id, LLMType.CHAT))
            ck = (await _kg_result) if inspect.isawaitable(_kg_result) else _kg_result
            if self.check_if_canceled("Retrieval processing"):
                return
            if ck["content_with_weight"]:
                ck["content"] = ck["content_with_weight"]
                del ck["content_with_weight"]
                kbinfos["chunks"].insert(0, ck)

        for ck in kbinfos["chunks"]:
            if "vector" in ck:
                del ck["vector"]
            if "content_ltks" in ck:
                del ck["content_ltks"]

        if not kbinfos["chunks"]:
            logging.warning(f"Retrieval returned no chunks for query: '{query}' with similarity_threshold={self._param.similarity_threshold}. Consider lowering the threshold or checking the knowledge base content.")
            self.set_output("formalized_content", self._param.empty_response if self._param.empty_response else "No relevant information found in the knowledge base for this query.")
            return

        # Format the chunks for JSON output (similar to how other tools do it)
        json_output = kbinfos["chunks"].copy()

        # Log retrieval statistics for debugging
        logging.info(
            f"Retrieval successful: {len(kbinfos['chunks'])} chunks found for query '{query[:50]}...' "
            f"with similarity scores ranging from "
            f"{min((c.get('similarity', 0) for c in kbinfos['chunks']), default=0):.4f} to "
            f"{max((c.get('similarity', 0) for c in kbinfos['chunks']), default=0):.4f}"
        )

        self._canvas.add_reference(kbinfos["chunks"], kbinfos["doc_aggs"])
        form_cnt = "\n".join(kb_prompt(kbinfos, 200000, True))

        # Set both formalized content and JSON output
        self.set_output("formalized_content", form_cnt)
        self.set_output("json", json_output)
        self.set_output("_references", kbinfos)  # Store references for downstream components

        return form_cnt

    async def _retrieve_memory(self, query_text: str):
        memory_ids: list[str] = [memory_id for memory_id in self._param.memory_ids]
        memory_list = MemoryService.get_by_ids(memory_ids)
        if not memory_list:
            raise Exception("No memory is selected.")

        embd_names = list({memory.embd_id for memory in memory_list})
        assert len(embd_names) == 1, "Memory use different embedding models."

        vars = self.get_input_elements_from_text(query_text)
        vars = {k: o["value"] for k, o in vars.items()}
        query = self.string_format(query_text, vars)
        # query message
        message_list = memory_message_service.query_message({"memory_id": memory_ids}, {
            "query": query,
            "similarity_threshold": self._param.similarity_threshold,
            "keywords_similarity_weight": self._param.keywords_similarity_weight,
            "top_n": self._param.top_n
        })
        if not message_list:
            self.set_output("formalized_content", self._param.empty_response)
            return ""
        formated_content = "\n".join(memory_prompt(message_list, 200000))
        # set formalized_content output
        self.set_output("formalized_content", formated_content)

        return formated_content

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 12)))
    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("Retrieval processing"):
            return
        if not kwargs.get("query"):
            self.set_output("formalized_content", self._param.empty_response)
            return

        if hasattr(self._param, "retrieval_from") and self._param.retrieval_from == "dataset":
            return await self._retrieve_kb(kwargs["query"])
        elif hasattr(self._param, "retrieval_from") and self._param.retrieval_from == "memory":
            return await self._retrieve_memory(kwargs["query"])
        elif self._param.kb_ids:
            return await self._retrieve_kb(kwargs["query"])
        elif hasattr(self._param, "memory_ids") and self._param.memory_ids:
            return await self._retrieve_memory(kwargs["query"])
        else:
            self.set_output("formalized_content", self._param.empty_response)
            return

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 12)))
    def _invoke(self, **kwargs):
        return asyncio.run(self._invoke_async(**kwargs))

    def thoughts(self) -> str:
        return """
Keywords: {}
Looking for the most relevant articles.
        """.format(self.get_input().get("query", "-_-!"))
