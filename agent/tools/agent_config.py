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
"""Tool to update agent configuration parameters."""
import json
import logging
from abc import ABC

from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from api.db.services.canvas_service import UserCanvasService


class AgentConfigParam(ToolParamBase):
    """Define the AgentConfig tool parameters."""

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "update_agent_config",
            "description": "Update retrieval parameters for a target RAG agent. Use this to tune similarity_threshold, top_n, top_k, or keywords_similarity_weight based on evaluation results.",
            "parameters": {
                "agent_id": {
                    "type": "string",
                    "description": "ID of the agent to update. If not provided, uses the configured target_agent_id.",
                    "required": False
                },
                "similarity_threshold": {
                    "type": "number",
                    "description": "New similarity threshold (0.0-1.0). Lower values return more results.",
                    "required": False
                },
                "top_n": {
                    "type": "integer",
                    "description": "Number of chunks to return after reranking (1-50).",
                    "required": False
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of candidate chunks for reranking (100-2048).",
                    "required": False
                },
                "keywords_similarity_weight": {
                    "type": "number",
                    "description": "Weight for keyword vs vector search (0.0-1.0). Higher values favor keyword matching.",
                    "required": False
                }
            }
        }
        super().__init__()
        self.target_agent_id = ""

    def check(self):
        pass


class AgentConfig(ToolBase, ABC):
    """Tool to update retrieval parameters in a target agent's DSL."""

    component_name = "AgentConfig"

    def _invoke(self, **kwargs):
        agent_id = kwargs.get("agent_id") or self._param.target_agent_id
        if not agent_id:
            return "Error: No agent_id provided and no target_agent_id configured"

        try:
            exists, canvas = UserCanvasService.get_by_id(agent_id)
        except Exception as e:
            logging.exception(f"Failed to fetch agent {agent_id}")
            return f"Error: Failed to fetch agent - {e}"

        if not exists:
            return f"Error: Agent {agent_id} not found"

        dsl = canvas.dsl
        if isinstance(dsl, str):
            dsl = json.loads(dsl)

        updates = []
        updatable_params = ["similarity_threshold", "top_n", "top_k", "keywords_similarity_weight"]

        for comp_id, comp in dsl.get("components", {}).items():
            tools = comp.get("obj", {}).get("params", {}).get("tools", [])
            for tool in tools:
                if tool.get("component_name") == "Retrieval":
                    params = tool.get("params", {})
                    for key in updatable_params:
                        if key in kwargs and kwargs[key] is not None:
                            old_val = params.get(key)
                            new_val = kwargs[key]
                            if key == "similarity_threshold":
                                new_val = max(0.0, min(1.0, float(new_val)))
                            elif key == "keywords_similarity_weight":
                                new_val = max(0.0, min(1.0, float(new_val)))
                            elif key == "top_n":
                                new_val = max(1, min(50, int(new_val)))
                            elif key == "top_k":
                                new_val = max(100, min(2048, int(new_val)))

                            if old_val != new_val:
                                params[key] = new_val
                                updates.append(f"{comp_id}/{key}: {old_val} -> {new_val}")

        if not updates:
            return "No parameters were updated (values unchanged or no Retrieval tools found)"

        try:
            UserCanvasService.update_by_id(agent_id, {"dsl": json.dumps(dsl, ensure_ascii=False)})
        except Exception as e:
            logging.exception(f"Failed to save agent {agent_id}")
            return f"Error: Failed to save changes - {e}"

        result = f"Updated agent {agent_id}:\n" + "\n".join(updates)
        logging.info(result)
        return result
