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
import json
import logging
import os
import re
from copy import deepcopy
from functools import partial
from typing import Any

import json_repair
import xxhash
from timeit import default_timer as timer
from agent.tools.base import LLMToolPluginCallSession, ToolParamBase, ToolBase, ToolMeta
from api.db.services.llm_service import LLMBundle
from api.db.services.tenant_llm_service import TenantLLMService
from api.db.services.mcp_server_service import MCPServerService
from common.connection_utils import timeout
from rag.prompts.generator import next_step_async, COMPLETE_TASK, \
    citation_prompt, kb_prompt, citation_plus, full_question, message_fit_in, structured_output_prompt, format_sources_section
from common.mcp_tool_call_conn import MCPToolCallSession, mcp_tool_metadata_to_openai_tool
from agent.component.llm import LLMParam, LLM


def strip_inline_citations(text: str) -> str:
    """Remove inline citations that LLMs add despite instructions not to."""
    if not text:
        return text
    # Remove [page N], [p. N], [pg N] style references
    text = re.sub(r'\s*\[page\s*\d+\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*\[p\.?\s*\d+\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*\[pg\.?\s*\d+\]', '', text, flags=re.IGNORECASE)
    # Remove [1], [2], [Document 1], [Source], [N], etc.
    text = re.sub(r'\s*\[[^\]]*\d+[^\]]*\]', '', text)
    # Remove [Document Name] style references
    text = re.sub(r'\s*\[Document[^\]]*\]', '', text, flags=re.IGNORECASE)
    # Remove [Source: ...] or [Reference: ...]
    text = re.sub(r'\s*\[(Source|Reference|Ref)[^\]]*\]', '', text, flags=re.IGNORECASE)
    # Remove LLM-invented bracketed references like [OH&SMS Manual], [KAUST Policy], [PPE Standard]
    # Matches [...] containing uppercase words, &, or common doc-name patterns
    text = re.sub(r'\s*\[[A-Z][A-Za-z&\s\-\.\']{2,}(?:Manual|Policy|Standard|Guide|Procedure|Document|Report|Code|Plan|Handbook|Regulation|Act)\]', '', text)
    # Remove standalone citation lines like "[1] Author, Title, Year"
    text = re.sub(r'\n\s*\[\d+\][^\n]+', '', text)
    # Remove internal chunk ID references like "document ID 489", "ID 327", "chunk 3"
    text = re.sub(r',?\s*(?:according to |from |per |in |see )?(?:document |chunk |record )?ID\s*\d+', '', text, flags=re.IGNORECASE)
    # Remove table annotation markers that leak into answers (all variants)
    text = re.sub(r'\[/?(?:TABLE DATA|END TABLE|TABLE)[^\]]*\]\n?', '', text)
    # Remove pipe-table rows (lines that start and end with |)
    text = re.sub(r'^\|.+\|[  ]*$\n?', '', text, flags=re.MULTILINE)
    # Remove separator rows (| --- | --- |)
    text = re.sub(r'^\|[\s\-:|]+\|[  ]*$\n?', '', text, flags=re.MULTILINE)
    # Clean up extra whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class AgentParam(LLMParam, ToolParamBase):
    """
    Define the Agent component parameters.
    """

    def __init__(self):
        self.meta:ToolMeta = {
                "name": "agent",
                "description": "This is an agent for a specific task.",
                "parameters": {
                    "user_prompt": {
                        "type": "string",
                        "description": "This is the order you need to send to the agent.",
                        "default": "",
                        "required": True
                    },
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "Supervisor's reasoning for choosing the this agent. "
                            "Explain why this agent is being invoked and what is expected of it."
                        ),
                        "required": True
                    },
                    "context": {
                        "type": "string",
                        "description": (
                                "All relevant background information, prior facts, decisions, "
                                "and state needed by the agent to solve the current query. "
                                "Should be as detailed and self-contained as possible."
                            ),
                        "required": True
                    },
                }
            }
        super().__init__()
        self.function_name = "agent"
        self.tools = []
        self.mcp = []
        self.max_rounds = 5
        self.description = ""


class Agent(LLM, ToolBase):
    component_name = "Agent"

    def __init__(self, canvas, id, param: LLMParam):
        LLM.__init__(self, canvas, id, param)
        self.tools = {}
        for idx, cpn in enumerate(self._param.tools):
            cpn = self._load_tool_obj(cpn)
            original_name = cpn.get_meta()["function"]["name"]
            indexed_name = f"{original_name}_{idx}"
            self.tools[indexed_name] = cpn

        self.chat_mdl = LLMBundle(self._canvas.get_tenant_id(), TenantLLMService.llm_id2llm_type(self._param.llm_id), self._param.llm_id,
                                  max_retries=self._param.max_retries,
                                  retry_interval=self._param.delay_after_error,
                                  max_rounds=self._param.max_rounds,
                                  verbose_tool_use=True
                                  )
        self.tool_meta = []
        for indexed_name, tool_obj in self.tools.items():
            original_meta = tool_obj.get_meta()
            indexed_meta = deepcopy(original_meta)
            indexed_meta["function"]["name"] = indexed_name
            self.tool_meta.append(indexed_meta)

        for mcp in self._param.mcp:
            _, mcp_server = MCPServerService.get_by_id(mcp["mcp_id"])
            tool_call_session = MCPToolCallSession(mcp_server, mcp_server.variables)
            for tnm, meta in mcp["tools"].items():
                self.tool_meta.append(mcp_tool_metadata_to_openai_tool(meta))
                self.tools[tnm] = tool_call_session
        self.callback = partial(self._canvas.tool_use_callback, id)
        self.toolcall_session = LLMToolPluginCallSession(self.tools, self.callback)
        #self.chat_mdl.bind_tools(self.toolcall_session, self.tool_metas)

    def _load_tool_obj(self, cpn: dict) -> object:
        from agent.component import component_class
        tool_name = cpn["component_name"]
        param = component_class(tool_name + "Param")()
        param.update(cpn["params"])
        try:
            param.check()
        except Exception as e:
            self.set_output("_ERROR", cpn["component_name"] + f" configuration error: {e}")
            raise
        cpn_id = f"{self._id}-->" + cpn.get("name", "").replace(" ", "_")
        return component_class(cpn["component_name"])(self._canvas, cpn_id, param)

    def get_meta(self) -> dict[str, Any]:
        self._param.function_name= self._id.split("-->")[-1]
        m = super().get_meta()
        if hasattr(self._param, "user_prompt") and self._param.user_prompt:
            m["function"]["parameters"]["properties"]["user_prompt"] = self._param.user_prompt
        return m

    def get_input_form(self) -> dict[str, dict]:
        res = {}
        for k, v in self.get_input_elements().items():
            res[k] = {
                "type": "line",
                "name": v["name"]
            }
        for cpn in self._param.tools:
            if not isinstance(cpn, LLM):
                continue
            res.update(cpn.get_input_form())
        return res

    def _get_output_schema(self):
        try:
            cand = self._param.outputs.get("structured")
        except Exception:
            return None

        if isinstance(cand, dict):
            if isinstance(cand.get("properties"), dict) and len(cand["properties"]) > 0:
                return cand
            for k in ("schema", "structured"):
                if isinstance(cand.get(k), dict) and isinstance(cand[k].get("properties"), dict) and len(cand[k]["properties"]) > 0:
                    return cand[k]

        return None

    async def _force_format_to_schema_async(self, text: str, schema_prompt: str) -> str:
        fmt_msgs = [
            {"role": "system", "content": schema_prompt + "\nIMPORTANT: Output ONLY valid JSON. No markdown, no extra text."},
            {"role": "user", "content": text},
        ]
        _, fmt_msgs = message_fit_in(fmt_msgs, int(self.chat_mdl.max_length * 0.97))
        return await self._generate_async(fmt_msgs)

    def _invoke(self, **kwargs):
        return asyncio.run(self._invoke_async(**kwargs))

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 20*60)))
    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("Agent processing"):
            return

        if kwargs.get("user_prompt"):
            usr_pmt = ""
            if kwargs.get("reasoning"):
                usr_pmt += "\nREASONING:\n{}\n".format(kwargs["reasoning"])
            if kwargs.get("context"):
                usr_pmt += "\nCONTEXT:\n{}\n".format(kwargs["context"])
            if usr_pmt:
                usr_pmt += "\nQUERY:\n{}\n".format(str(kwargs["user_prompt"]))
            else:
                usr_pmt = str(kwargs["user_prompt"])
            self._param.prompts = [{"role": "user", "content": usr_pmt}]

        if not self.tools:
            if self.check_if_canceled("Agent processing"):
                return
            return await LLM._invoke_async(self, **kwargs)

        prompt, msg, user_defined_prompt = self._prepare_prompt_variables()
        output_schema = self._get_output_schema()
        schema_prompt = ""
        if output_schema:
            schema = json.dumps(output_schema, ensure_ascii=False, indent=2)
            schema_prompt = structured_output_prompt(schema)

        downstreams = self._canvas.get_component(self._id)["downstream"] if self._canvas.get_component(self._id) else []
        ex = self.exception_handler()
        if any([self._canvas.get_component_obj(cid).component_name.lower()=="message" for cid in downstreams]) and not (ex and ex["goto"]) and not output_schema:
            self.set_output("content", partial(self.stream_output_with_tools_async, prompt, deepcopy(msg), user_defined_prompt))
            return

        _, msg = message_fit_in([{"role": "system", "content": prompt}, *msg], int(self.chat_mdl.max_length * 0.97))
        use_tools = []
        ans = ""
        async for delta_ans, _tk in self._react_with_tools_streamly_async_simple(prompt, msg, use_tools, user_defined_prompt,schema_prompt=schema_prompt):
            if self.check_if_canceled("Agent processing"):
                return
            ans += delta_ans

        if ans.find("**ERROR**") >= 0:
            logging.error(f"Agent._chat got error. response: {ans}")
            if self.get_exception_default_value():
                self.set_output("content", self.get_exception_default_value())
            else:
                self.set_output("_ERROR", ans)
            return

        if output_schema:
            error = ""
            for _ in range(self._param.max_retries + 1):
                try:
                    def clean_formated_answer(ans: str) -> str:
                        ans = re.sub(r"^.*</think>", "", ans, flags=re.DOTALL)
                        ans = re.sub(r"^.*```json", "", ans, flags=re.DOTALL)
                        return re.sub(r"```\n*$", "", ans, flags=re.DOTALL)
                    obj = json_repair.loads(clean_formated_answer(ans))
                    self.set_output("structured", obj)
                    if use_tools:
                        self.set_output("use_tools", use_tools)
                    return obj
                except Exception:
                    error = "The answer cannot be parsed as JSON"
                    ans = await self._force_format_to_schema_async(ans, schema_prompt)
                    if ans.find("**ERROR**") >= 0:
                        continue

            self.set_output("_ERROR", error)
            return

        self.set_output("content", ans)
        if use_tools:
            self.set_output("use_tools", use_tools)
        return ans

    async def stream_output_with_tools_async(self, prompt, msg, user_defined_prompt={}):
        _, msg = message_fit_in([{"role": "system", "content": prompt}, *msg], int(self.chat_mdl.max_length * 0.97))
        answer_without_toolcall = ""
        use_tools = []
        async for delta_ans, _ in self._react_with_tools_streamly_async_simple(prompt, msg, use_tools, user_defined_prompt):
            if self.check_if_canceled("Agent streaming"):
                return

            if delta_ans.find("**ERROR**") >= 0:
                if self.get_exception_default_value():
                    self.set_output("content", self.get_exception_default_value())
                    yield self.get_exception_default_value()
                else:
                    self.set_output("_ERROR", delta_ans)
                    return
            answer_without_toolcall += delta_ans
            yield delta_ans

        self.set_output("content", answer_without_toolcall)
        if use_tools:
            self.set_output("use_tools", use_tools)

    @staticmethod
    def _cache_key(question: str, kb_ids: list[str]) -> str | None:
        """Build a deterministic cache key from question + kb_ids. Returns None if uncacheable."""
        if not kb_ids:
            return None
        # Normalize: lowercase, strip punctuation, collapse whitespace
        norm = re.sub(r'[^\w\s]', '', question.lower()).strip()
        norm = re.sub(r'\s+', ' ', norm)
        if not norm:
            return None
        key_input = norm + "|" + ",".join(sorted(kb_ids))
        return f"cag:{xxhash.xxh64(key_input.encode('utf-8')).hexdigest()}"

    async def _react_with_tools_streamly_async_simple(self, prompt, history: list[dict], use_tools, user_defined_prompt={}, schema_prompt: str = ""):
        token_count = 0
        tool_metas = self.tool_meta
        hist = deepcopy(history)
        last_calling = ""
        user_request = history[-1]["content"]

        # --- Redis response cache (CAG) ---
        # Only cache first-turn queries with kb_ids (not multi-turn follow-ups)
        cache_key = None
        is_first_turn = len(history) <= 2  # system + user
        if is_first_turn and tool_metas:
            # Extract kb_ids from tool params
            kb_ids = []
            for tm in tool_metas:
                props = tm.get("function", {}).get("parameters", {}).get("properties", {})
                if "kb_ids" in props:
                    kb_ids = props["kb_ids"].get("default", [])
                    break
            if not kb_ids:
                # Try getting from tool objects
                for tool_obj in self.tools.values():
                    if hasattr(tool_obj, '_param') and hasattr(tool_obj._param, 'kb_ids'):
                        kb_ids = tool_obj._param.kb_ids or []
                        if kb_ids:
                            break
            cache_key = self._cache_key(user_request, kb_ids)

        if cache_key:
            try:
                from rag.utils.redis_conn import REDIS_CONN
                cached = REDIS_CONN.get(cache_key)
                if cached:
                    logging.info(f"[CAG] Cache HIT: {cache_key} for '{user_request[:60]}'")
                    cached_data = json.loads(cached)
                    answer = cached_data.get("answer", "")
                    sources = cached_data.get("sources", "")
                    if answer:
                        yield answer, 0
                        if sources:
                            yield sources, 0
                        return
            except Exception as e:
                logging.warning(f"[CAG] Cache read error: {e}")

        def _store_cache(answer: str, sources: str = ""):
            """Store answer in Redis cache if cache_key is set and answer is valid."""
            if not cache_key or not answer or answer.startswith("I am sorry") or answer.startswith("I'm sorry"):
                return
            try:
                from rag.utils.redis_conn import REDIS_CONN
                ttl = int(os.environ.get("CAG_ANSWER_TTL", 14400))
                payload = json.dumps({"answer": answer, "sources": sources}, ensure_ascii=False)
                REDIS_CONN.set(cache_key, payload, exp=ttl)
                logging.info(f"[CAG] Cached: {cache_key} (ttl={ttl}s, {len(answer)} chars)")
            except Exception as e:
                logging.warning(f"[CAG] Cache write error: {e}")

        def build_task_desc(prompt: str, user_request: str, user_defined_prompt: dict | None = None) -> str:
            """Build a minimal task_desc with just the user request (sys_prompt is already in system message)."""
            user_defined_prompt = user_defined_prompt or {}

            task_desc = f"### User Request\n{user_request}\n"

            if user_defined_prompt:
                udp_json = json.dumps(user_defined_prompt, ensure_ascii=False, indent=2)
                task_desc += "\n### User Defined Prompts\n" + udp_json + "\n"

            return task_desc


        async def use_tool_async(name, args):
            nonlocal hist, use_tools, last_calling
            logging.info(f"{last_calling=} == {name=}")
            last_calling = name
            tool_response = await self.toolcall_session.tool_call_async(name, args)
            use_tools.append({
                "name": name,
                "arguments": args,
                "results": tool_response
            })
            return name, tool_response

        async def complete():
            nonlocal hist
            need2cite = self._param.cite and self._canvas.get_reference()["chunks"] and self._id.find("-->") < 0
            if schema_prompt:
                need2cite = False
            cited = False

            # Build a CLEAN history for answer generation.
            # The ReAct hist contains raw JSON tool calls as assistant messages
            # (e.g. [{"name":"search_kb_0",...}]) which confuse the model into
            # asking for context instead of answering.  We keep ONLY:
            #   1. A minimal system prompt (no rules to acknowledge)
            #   2. Observation messages (contain retrieved chunks)
            #   3. A clear answering directive with the user's question
            sys_content = (
                "You are an expert assistant. Answer based ONLY on the provided data.\n"
                "Rules:\n"
                "- Lead directly with the answer — no preamble like 'Based on...' or 'The X are as follows:'\n"
                "- Be concise: 1-3 sentences for simple questions\n"
                "- For multiple items: use markdown bullet list with `- ` prefix, one item per line\n"
                "- Use **bold** for document numbers, codes, and key values (e.g., **HSE-PR-01**)\n"
                "- Use exact values, numbers, and codes from the data\n"
                "- No citations, references, or source attributions\n"
                "- When listing numbered items, sort by number ascending but ONLY include items explicitly present in the data — do NOT infer missing numbers"
            )
            if schema_prompt:
                sys_content += "\n" + schema_prompt
            if need2cite and len(hist) < 25:
                sys_content += citation_prompt()
                cited = True

            # Collect observation data from hist (user messages with "Observation:")
            observations = [
                m for m in hist
                if m.get("role") == "user" and "Observation:" in m.get("content", "")
            ]

            # Build the clean _hist: system + observations (token-budgeted)
            _hist = [{"role": "system", "content": sys_content}]
            # Use up to 60% of model context for observations, keeping room for answer
            # Cap budget for simple queries (max_tokens <= 768) to reduce inference time
            obs_token_budget = int(self.chat_mdl.max_length * 0.6)
            if self._param.max_tokens <= 768:
                obs_token_budget = min(obs_token_budget, 6500)
            obs_tokens_used = 0
            selected_obs = []
            for obs in reversed(observations):  # most recent first
                obs_len = len(obs.get("content", "")) // 3  # rough token estimate
                if obs_tokens_used + obs_len > obs_token_budget and selected_obs:
                    break
                selected_obs.append(obs)
                obs_tokens_used += obs_len
            for obs in reversed(selected_obs):  # restore chronological order
                _hist.append(obs)
            # Add a clear answering directive with the user's question
            # Use the raw last user message when available — full_question()
            # can contaminate the query in multi-turn sessions by mixing in
            # context from prior turns (e.g., "procedures" leaking into a
            # "points" question).
            raw_question = history[-1]["content"] if history else user_request
            question_text = raw_question if raw_question == user_request else f"{user_request}\n\nOriginal question: {raw_question}"
            _hist.append({"role": "user", "content": f"Based on the data above, answer this question: {question_text}"})
            logging.info(f"[COMPLETE] Built clean hist: {len(_hist)} messages, {len(observations)} observations, question='{user_request[:80]}'")

            yield "", token_count

            entire_txt = ""
            async for delta_ans in self._generate_streamly(_hist):
                entire_txt += delta_ans

            logging.info(f"[COMPLETE] answer ({len(entire_txt)} chars): {entire_txt[:200]}")

            # Strip citations and yield clean text
            if not need2cite or cited:
                yield strip_inline_citations(entire_txt), 0
                return

            st = timer()
            txt = ""
            async for delta_ans in self._gen_citations_async(entire_txt):
                if self.check_if_canceled("Agent streaming"):
                    return
                yield delta_ans, 0
                txt += delta_ans

            self.callback("gen_citations", {}, txt, elapsed_time=timer()-st)

        def build_observation(tool_call_res: list[tuple]) -> str:
            """
            Build a Observation from tool call results.
            No LLM involved.
            """
            if not tool_call_res:
                return ""

            lines = ["Observation:"]
            chunk_count = 0
            for name, result in tool_call_res:
                lines.append(f"[{name} result]")
                result_str = str(result)
                lines.append(result_str)
                chunk_count += result_str.count("\nID:")

            lines.append("")
            lines.append(f"=== END OF DATA ({chunk_count} chunks) ===")
            lines.append("Answer from this data ONLY. Use exact values. Use **bold** for codes/numbers. Use `- ` bullet lists for multiple items. Do NOT add items beyond what's shown.")

            return "\n".join(lines)

        def append_user_content(hist, content):
            if hist[-1]["role"] == "user":
                hist[-1]["content"] += content
            else:
                hist.append({"role": "user", "content": content})

        st = timer()
        task_desc = build_task_desc(prompt, user_request, user_defined_prompt)
        self.callback("analyze_task", {}, task_desc, elapsed_time=timer()-st)

        # --- Direct retrieval mode for SimpleRetrieval ---
        # When max_tokens <= 768 (SimpleRetrieval), skip the ReAct planning
        # LLM call and go straight to search → complete(). Saves 10-15s by
        # eliminating one LLM round-trip (next_step_async).
        if self._param.max_tokens <= 768 and tool_metas:
            logging.info(f"[DIRECT] SimpleRetrieval fast path for: {user_request[:80]}")
            # Find the first retrieval tool and call it directly
            retrieval_tool_name = None
            for tm in tool_metas:
                fname = tm.get("function", {}).get("name", "")
                if fname.startswith("search_kb") or fname.startswith("retrieval"):
                    retrieval_tool_name = fname
                    break
            if retrieval_tool_name:
                try:
                    tool_result = await self.toolcall_session.tool_call_async(
                        retrieval_tool_name, {"query": user_request}
                    )
                    use_tools.append({
                        "name": retrieval_tool_name,
                        "arguments": {"query": user_request},
                        "results": tool_result,
                    })
                    observation = build_observation([(retrieval_tool_name, tool_result)])
                    append_user_content(hist, observation)
                    self.callback("reflection", {}, observation, elapsed_time=timer() - st)
                    logging.info(f"[DIRECT] retrieval done, falling through to complete()")
                    # Fall through to complete() at end of method
                except Exception as e:
                    logging.warning(f"[DIRECT] retrieval failed ({e}), falling back to ReAct loop")
                    retrieval_tool_name = None  # reset so we enter the loop

            if retrieval_tool_name:
                # Skip the ReAct loop entirely — go to max-rounds complete() path
                direct_answer = ""
                async for txt, tkcnt in complete():
                    direct_answer += txt
                    yield txt, tkcnt
                sources_text = ""
                retrievals = self._canvas.get_reference()
                if retrievals.get("chunks"):
                    sources_text = format_sources_section(list(retrievals["chunks"].values()))
                    if sources_text:
                        yield sources_text, 0
                _store_cache(direct_answer, sources_text)
                return

        for _ in range(self._param.max_rounds + 1):
            if self.check_if_canceled("Agent streaming"):
                return
            response, tk = await next_step_async(self.chat_mdl, hist, tool_metas, task_desc, user_defined_prompt)
            token_count += tk or 0
            logging.info(f"[REACT round {_}] response ({len(response)} chars): {response[:200]}")
            hist.append({"role": "assistant", "content": response})
            try:
                functions = json_repair.loads(re.sub(r"```.*", "", response))
                if isinstance(functions, dict):
                    # Normalize a single dict response into a list
                    # Handle alternate keys: tool_code/tool_name -> name, reasoning -> answer
                    name = functions.get("name") or functions.get("tool_code") or functions.get("tool_name") or functions.get("tool_to_call") or functions.get("tool", "")
                    args = functions.get("arguments", {})
                    if not args and name == COMPLETE_TASK:
                        # LLM may put the answer in 'reasoning' or other fields
                        answer = functions.get("reasoning") or functions.get("answer") or functions.get("response") or functions.get("result", "")
                        args = {"answer": answer} if answer else {}
                    if not name and ("answer" in functions or "response" in functions or "reasoning" in functions or "result" in functions):
                        # Model returned bare {"answer": "..."} without tool name wrapper — treat as complete_task
                        name = COMPLETE_TASK
                        answer = functions.get("answer") or functions.get("response") or functions.get("reasoning") or functions.get("result", "")
                        args = {"answer": answer} if answer else {}
                    if name:
                        functions = [{"name": name, "arguments": args}]
                    else:
                        raise TypeError(f"List should be returned, but `{functions}`")
                if not isinstance(functions, list):
                    raise TypeError(f"List should be returned, but `{functions}`")
                for i, f in enumerate(functions):
                    if not isinstance(f, dict):
                        raise TypeError(f"An object type should be returned, but `{f}`")
                    # Normalize alternate key names within list items
                    if "name" not in f:
                        f["name"] = f.pop("tool_code", None) or f.pop("tool_name", f.get("name", ""))
                    if "arguments" not in f:
                        f["arguments"] = f.pop("params", f.pop("parameters", {}))

                tool_tasks = []
                has_searched = any(
                    m.get("role") == "user" and "Observation:" in m.get("content", "")
                    for m in hist
                )
                # Check if conversation history has recent context (for follow-ups)
                # Prior assistant answers contain relevant data the model can reuse
                has_recent_context = any(
                    m.get("role") == "assistant" and len(m.get("content", "")) > 20
                    for m in history[-6:]
                ) if len(history) > 3 else False
                for func in functions:
                    name = func["name"]
                    args = func["arguments"]
                    if name == COMPLETE_TASK:
                        # Search-first guard: reject complete_task if no search
                        # was done this turn and tools are available. Forces the
                        # model to retrieve fresh data instead of answering from
                        # stale conversation history.
                        # Exception: allow for follow-ups where recent observations
                        # exist in conversation history (within last 2 turns).
                        if not has_searched and tool_metas and not has_recent_context:
                            logging.info("[GUARD] complete_task rejected: no search this turn — forcing retrieval")
                            append_user_content(hist, "You MUST search the knowledge base before answering. Call search_kb_0 with relevant keywords first.")
                            break  # back to next round of the ReAct loop
                        answer = args.get("answer", "")
                        if answer:
                            # Strip any inline citations the LLM added despite instructions
                            answer = strip_inline_citations(answer)
                            yield answer, 0
                            # Append sources from current retrieval or conversation history
                            retrievals = self._canvas.get_reference()
                            chunk_count = len(retrievals.get("chunks", {}))
                            logging.info(f"[SOURCES] complete_task: chunks={chunk_count} has_searched={has_searched}")
                            ct_sources = ""
                            if retrievals.get("chunks"):
                                ct_sources = format_sources_section(list(retrievals["chunks"].values()))
                                if ct_sources:
                                    yield ct_sources, 0
                            elif not has_searched:
                                # No fresh retrieval — carry forward sources from
                                # conversation history so follow-up answers still
                                # show document attribution.
                                for m in reversed(history):
                                    content = m.get("content", "")
                                    if "\nSources: " in content or content.startswith("Sources: "):
                                        match = re.search(r'(?:\n|^)(Sources: .+?)$', content, re.MULTILINE)
                                        if match:
                                            logging.info(f"[SOURCES] Carried forward from history: {match.group(1)}")
                                            ct_sources = "\n\n" + match.group(1)
                                            yield ct_sources, 0
                                            break
                            _store_cache(answer, ct_sources)
                            return
                        # Fallback: no answer text, re-generate via complete()
                        async for txt, tkcnt in complete():
                            yield txt, tkcnt
                        # Append sources after fallback generation
                        retrievals = self._canvas.get_reference()
                        if retrievals.get("chunks"):
                            sources_text = format_sources_section(list(retrievals["chunks"].values()))
                            if sources_text:
                                yield sources_text, 0
                        return

                    tool_tasks.append(asyncio.create_task(use_tool_async(name, args)))

                results = await asyncio.gather(*tool_tasks) if tool_tasks else []
                st = timer()
                reflection = build_observation(results)
                append_user_content(hist, reflection)
                self.callback("reflection", {}, str(reflection), elapsed_time=timer()-st)

            except Exception as e:
                logging.exception(msg=f"Wrong JSON argument format in LLM ReAct response: {e}")
                # If the LLM returned substantial non-JSON text and we already have
                # observation data, skip to complete() instead of wasting rounds.
                has_observation = any(
                    m.get("role") == "user" and "Observation:" in m.get("content", "")
                    for m in hist
                )
                if has_observation and response and len(response.strip()) > 50:
                    logging.warning("Non-JSON response after successful retrieval — skipping to complete()")
                    break
                e = f"\nTool call error, please correct the input parameter of response format and call it again.\n *** Exception ***\n{e}"
                append_user_content(hist, str(e))

        logging.warning( f"Exceed max rounds: {self._param.max_rounds}")
        final_instruction = f"""ANSWER THIS QUESTION NOW: {user_request}

Look at the Observation data above. Extract the answer and respond concisely. Use **bold** for codes/numbers. Use `- ` bullet lists for multiple items. No preamble — lead directly with the answer."""
        if self.check_if_canceled("Agent final instruction"):
            return
        append_user_content(hist, final_instruction)

        mr_answer = ""
        async for txt, tkcnt in complete():
            mr_answer += txt
            yield txt, tkcnt
        # Append sources after max rounds generation
        mr_sources = ""
        retrievals = self._canvas.get_reference()
        if retrievals.get("chunks"):
            mr_sources = format_sources_section(list(retrievals["chunks"].values()))
            if mr_sources:
                yield mr_sources, 0
        _store_cache(mr_answer, mr_sources)

#     async def _react_with_tools_streamly_async(self, prompt, history: list[dict], use_tools, user_defined_prompt={}, schema_prompt: str = ""):
#         token_count = 0
#         tool_metas = self.tool_meta
#         hist = deepcopy(history)
#         last_calling = ""
#         if len(hist) > 3:
#             st = timer()
#             user_request = await full_question(messages=history, chat_mdl=self.chat_mdl)
#             self.callback("Multi-turn conversation optimization", {}, user_request, elapsed_time=timer()-st)
#         else:
#             user_request = history[-1]["content"]

#         async def use_tool_async(name, args):
#             nonlocal hist, use_tools, last_calling
#             logging.info(f"{last_calling=} == {name=}")
#             last_calling = name
#             tool_response = await self.toolcall_session.tool_call_async(name, args)
#             use_tools.append({
#                 "name": name,
#                 "arguments": args,
#                 "results": tool_response
#             })
#             # self.callback("add_memory", {}, "...")
#             #self.add_memory(hist[-2]["content"], hist[-1]["content"], name, args, str(tool_response), user_defined_prompt)

#             return name, tool_response

#         async def complete():
#             nonlocal hist
#             need2cite = self._param.cite and self._canvas.get_reference()["chunks"] and self._id.find("-->") < 0
#             if schema_prompt:
#                 need2cite = False
#             cited = False
#             if hist and hist[0]["role"] == "system":
#                 if schema_prompt:
#                     hist[0]["content"] += "\n" + schema_prompt
#                 if need2cite and len(hist) < 7:
#                     hist[0]["content"] += citation_prompt()
#                     cited = True
#             yield "", token_count

#             _hist = hist
#             if len(hist) > 12:
#                 _hist = [hist[0], hist[1], *hist[-10:]]
#             entire_txt = ""
#             async for delta_ans in self._generate_streamly(_hist):
#                 if not need2cite or cited:
#                     yield delta_ans, 0
#                 entire_txt += delta_ans
#             if not need2cite or cited:
#                 return

#             st = timer()
#             txt = ""
#             async for delta_ans in self._gen_citations_async(entire_txt):
#                 if self.check_if_canceled("Agent streaming"):
#                     return
#                 yield delta_ans, 0
#                 txt += delta_ans

#             self.callback("gen_citations", {}, txt, elapsed_time=timer()-st)

#         def append_user_content(hist, content):
#             if hist[-1]["role"] == "user":
#                 hist[-1]["content"] += content
#             else:
#                 hist.append({"role": "user", "content": content})

#         st = timer()
#         task_desc = await analyze_task_async(self.chat_mdl, prompt, user_request, tool_metas, user_defined_prompt)
#         self.callback("analyze_task", {}, task_desc, elapsed_time=timer()-st)
#         for _ in range(self._param.max_rounds + 1):
#             if self.check_if_canceled("Agent streaming"):
#                 return
#             response, tk = await next_step_async(self.chat_mdl, hist, tool_metas, task_desc, user_defined_prompt)
#             # self.callback("next_step", {}, str(response)[:256]+"...")
#             token_count += tk or 0
#             hist.append({"role": "assistant", "content": response})
#             try:
#                 functions = json_repair.loads(re.sub(r"```.*", "", response))
#                 if not isinstance(functions, list):
#                     raise TypeError(f"List should be returned, but `{functions}`")
#                 for f in functions:
#                     if not isinstance(f, dict):
#                         raise TypeError(f"An object type should be returned, but `{f}`")

#                 tool_tasks = []
#                 for func in functions:
#                     name = func["name"]
#                     args = func["arguments"]
#                     if name == COMPLETE_TASK:
#                         append_user_content(hist, f"Respond with a formal answer. FORGET(DO NOT mention) about `{COMPLETE_TASK}`. The language for the response MUST be as the same as the first user request.\n")
#                         async for txt, tkcnt in complete():
#                             yield txt, tkcnt
#                         return

#                     tool_tasks.append(asyncio.create_task(use_tool_async(name, args)))

#                 results = await asyncio.gather(*tool_tasks) if tool_tasks else []
#                 st = timer()
#                 reflection = await reflect_async(self.chat_mdl, hist, results, user_defined_prompt)
#                 append_user_content(hist, reflection)
#                 self.callback("reflection", {}, str(reflection), elapsed_time=timer()-st)

#             except Exception as e:
#                 logging.exception(msg=f"Wrong JSON argument format in LLM ReAct response: {e}")
#                 e = f"\nTool call error, please correct the input parameter of response format and call it again.\n *** Exception ***\n{e}"
#                 append_user_content(hist, str(e))

#         logging.warning( f"Exceed max rounds: {self._param.max_rounds}")
#         final_instruction = f"""
# {user_request}
# IMPORTANT: You have reached the conversation limit. Based on ALL the information and research you have gathered so far, please provide a DIRECT and COMPREHENSIVE final answer to the original request.
# Instructions:
# 1. SYNTHESIZE all information collected during this conversation
# 2. Provide a COMPLETE response using existing data - do not suggest additional research
# 3. Structure your response as a FINAL DELIVERABLE, not a plan
# 4. If information is incomplete, state what you found and provide the best analysis possible with available data
# 5. DO NOT mention conversation limits or suggest further steps
# 6. Focus on delivering VALUE with the information already gathered
# Respond immediately with your final comprehensive answer.
#         """
#         if self.check_if_canceled("Agent final instruction"):
#             return
#         append_user_content(hist, final_instruction)

#         async for txt, tkcnt in complete():
#             yield txt, tkcnt

    async def _gen_citations_async(self, text):
        retrievals = self._canvas.get_reference()
        retrievals = {"chunks": list(retrievals["chunks"].values()), "doc_aggs": list(retrievals["doc_aggs"].values())}
        formated_refer = kb_prompt(retrievals, self.chat_mdl.max_length, True)
        # Collect full response then strip citations
        full_response = ""
        async for delta_ans in self._generate_streamly([{"role": "system", "content": citation_plus("\n\n".join(formated_refer))},
                                                  {"role": "user", "content": text}
                                                  ]):
            full_response += delta_ans
        # Strip any citations the LLM added and yield clean text
        yield strip_inline_citations(full_response)

    def reset(self, only_output=False):
        """
        Reset all tools if they have a reset method. This avoids errors for tools like MCPToolCallSession.
        """
        for k in self._param.outputs.keys():
            self._param.outputs[k]["value"] = None

        for k, cpn in self.tools.items():
            if hasattr(cpn, "reset") and callable(cpn.reset):
                cpn.reset()
        if only_output:
            return
        for k in self._param.inputs.keys():
            self._param.inputs[k]["value"] = None
        self._param.debug_inputs = {}
