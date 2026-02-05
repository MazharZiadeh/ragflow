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
from abc import ABC
import os
from agent.component.base import ComponentBase, ComponentParamBase
from api.utils.api_utils import timeout


class MemoryParam(ComponentParamBase):
    """
    Define the Memory component parameters.
    """
    def __init__(self):
        super().__init__()
        self.operation = "store"  # "store" | "retrieve" | "clear"
        self.key = ""  # Variable reference for storage key
        self.value = ""  # Variable reference for storage value (for store operation)

    def check(self):
        self.check_valid_value(self.operation, "[Memory] Operation", ["store", "retrieve", "clear"])
        if self.operation in ["store", "retrieve"]:
            self.check_empty(self.key, "[Memory] Key")
        if self.operation == "store":
            self.check_empty(self.value, "[Memory] Value")
        return True


class Memory(ComponentBase, ABC):
    component_name = "Memory"

    def get_input_form(self) -> dict[str, dict]:
        return {
            "key": {
                "type": "line",
                "name": "Key"
            },
            "value": {
                "type": "line",
                "name": "Value"
            }
        }

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10*60)))
    def _invoke(self, **kwargs):
        operation = self._param.operation

        # Resolve key from variable reference if provided
        key = self._param.key
        if key:
            resolved_key = self._canvas.get_variable_value(key) if self._canvas.is_reff(key) else key
            # Convert to string if not already
            if not isinstance(resolved_key, str):
                resolved_key = str(resolved_key)
        else:
            resolved_key = ""

        if operation == "store":
            # Resolve value from variable reference
            value_ref = self._param.value
            if value_ref:
                resolved_value = self._canvas.get_variable_value(value_ref) if self._canvas.is_reff(value_ref) else value_ref
            else:
                resolved_value = ""

            success = self._canvas.set_kv_memory(resolved_key, resolved_value)
            self.set_output("success", success)
            self.set_output("value", resolved_value)
            self.set_output("all_keys", self._canvas.get_all_kv_keys())

        elif operation == "retrieve":
            value = self._canvas.get_kv_memory(resolved_key)
            self.set_output("value", value)
            self.set_output("success", value is not None)
            self.set_output("all_keys", self._canvas.get_all_kv_keys())

        elif operation == "clear":
            if resolved_key:
                success = self._canvas.clear_kv_memory(resolved_key)
            else:
                success = self._canvas.clear_kv_memory()
            self.set_output("success", success)
            self.set_output("value", None)
            self.set_output("all_keys", self._canvas.get_all_kv_keys())

    def thoughts(self) -> str:
        op = self._param.operation
        if op == "store":
            return f"Storing value to memory with key: {self._param.key}"
        elif op == "retrieve":
            return f"Retrieving value from memory with key: {self._param.key}"
        elif op == "clear":
            if self._param.key:
                return f"Clearing memory key: {self._param.key}"
            return "Clearing all memory"
        return "Memory operation"
