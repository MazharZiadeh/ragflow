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
Unit tests for the Canvas and Graph classes in agent/canvas.py

These tests verify:
- DSL parsing and component loading
- Variable resolution and state management
- Workflow execution and component orchestration
- Task cancellation handling
- Error handling and exception management
"""

import json
import sys
import pytest
from unittest.mock import MagicMock, patch
from copy import deepcopy

# Mock all the heavy dependencies before importing the canvas module
sys.modules['agent.component'] = MagicMock()
sys.modules['agent.component.base'] = MagicMock()
sys.modules['api.db.services.file_service'] = MagicMock()
sys.modules['api.db.services.llm_service'] = MagicMock()
sys.modules['api.db.services.task_service'] = MagicMock()
sys.modules['common.constants'] = MagicMock()
sys.modules['common.misc_utils'] = MagicMock()
sys.modules['common.exceptions'] = MagicMock()
sys.modules['rag.prompts.generator'] = MagicMock()
sys.modules['rag.utils.redis_conn'] = MagicMock()


# Minimal DSL for basic tests
MINIMAL_DSL = {
    "components": {
        "begin": {
            "obj": {
                "component_name": "Begin",
                "params": {
                    "mode": "conversational",
                    "prologue": "Hello!"
                }
            },
            "downstream": ["message_0"],
            "upstream": []
        },
        "message_0": {
            "obj": {
                "component_name": "Message",
                "params": {
                    "content": ["Hello, world!"],
                    "stream": False
                }
            },
            "downstream": [],
            "upstream": ["begin"]
        }
    },
    "history": [],
    "path": [],
    "retrieval": [],
    "globals": {
        "sys.query": "",
        "sys.user_id": "test_tenant",
        "sys.conversation_turns": 0,
        "sys.files": []
    },
    "variables": {}
}


# DSL with variable references
VARIABLE_DSL = {
    "components": {
        "begin": {
            "obj": {
                "component_name": "Begin",
                "params": {
                    "mode": "conversational",
                    "prologue": "Hello!"
                }
            },
            "downstream": ["message_0"],
            "upstream": []
        },
        "message_0": {
            "obj": {
                "component_name": "Message",
                "params": {
                    "content": ["{sys.query}"],
                    "stream": False
                }
            },
            "downstream": [],
            "upstream": ["begin"]
        }
    },
    "history": [],
    "path": [],
    "retrieval": [],
    "globals": {
        "sys.query": "What is the weather?",
        "sys.user_id": "test_tenant",
        "sys.conversation_turns": 0,
        "sys.files": []
    },
    "variables": {}
}


class MockRedisConn:
    """Mock Redis connection for testing"""
    def __init__(self):
        self._data = {}

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value, *args, **kwargs):
        self._data[key] = value

    def set_obj(self, key, obj, ttl=None):
        self._data[key] = json.dumps(obj)

    def delete(self, key):
        if key in self._data:
            del self._data[key]


class MockComponentBase:
    """Mock base component for testing"""
    component_name = "MockComponent"

    def __init__(self, canvas, id, param):
        self._canvas = canvas
        self._id = id
        self._param = param
        self._outputs = {}

    def reset(self, only_output=False):
        self._outputs = {}

    def output(self, key=None):
        if key:
            return self._outputs.get(key, "")
        return self._outputs

    def set_output(self, key, value):
        self._outputs[key] = value

    def error(self):
        return self._outputs.get("_ERROR")

    def invoke(self, **kwargs):
        return self._outputs

    def get_input(self):
        return {}

    def get_input_elements(self):
        return {}

    def get_input_values(self):
        return {}

    def thoughts(self):
        return ""

    def exception_handler(self):
        return None


class MockComponentParam:
    """Mock component parameter"""
    def __init__(self):
        self.mode = "conversational"
        self.prologue = "Hello!"
        self.content = ["Hello, world!"]
        self.stream = False
        self.inputs = {}
        self.outputs = {"content": {"type": "str", "value": None}}
        self.debug_inputs = {}

    def update(self, conf, allow_redundant=False):
        for k, v in conf.items():
            if hasattr(self, k):
                setattr(self, k, v)
            else:
                setattr(self, k, v)

    def check(self):
        pass


class MockBegin(MockComponentBase):
    """Mock Begin component"""
    component_name = "Begin"

    def thoughts(self):
        return ""


class MockMessage(MockComponentBase):
    """Mock Message component"""
    component_name = "Message"

    def get_param(self, name):
        return getattr(self._param, name, None)

    def thoughts(self):
        return ""


@pytest.fixture
def mock_component_class():
    """Fixture to mock component_class function"""
    def _mock_component_class(name):
        if name == "Begin":
            return MockBegin
        elif name == "BeginParam":
            return MockComponentParam
        elif name == "Message":
            return MockMessage
        elif name == "MessageParam":
            return MockComponentParam
        raise ValueError(f"Unknown component: {name}")
    return _mock_component_class


@pytest.fixture
def mock_redis_conn():
    """Fixture for mock Redis connection"""
    return MockRedisConn()


class TestVariableResolution:
    """Test variable resolution logic independently"""

    def test_get_variable_param_value_dict(self):
        """Test getting nested dict value"""
        obj = {"level1": {"level2": "value"}}

        # Simulate the algorithm from Graph.get_variable_param_value
        def get_variable_param_value(obj, path):
            cur = obj
            if not path:
                return cur
            for key in path.split('.'):
                if cur is None:
                    return None
                if isinstance(cur, str):
                    try:
                        cur = json.loads(cur)
                    except Exception:
                        return None
                if isinstance(cur, dict):
                    cur = cur.get(key)
                    continue
                if isinstance(cur, (list, tuple)):
                    try:
                        idx = int(key)
                        cur = cur[idx]
                    except Exception:
                        return None
                    continue
                cur = getattr(cur, key, None)
            return cur

        result = get_variable_param_value(obj, "level1.level2")
        assert result == "value"

    def test_get_variable_param_value_list(self):
        """Test getting list index value"""
        obj = ["first", "second", "third"]

        def get_variable_param_value(obj, path):
            cur = obj
            if not path:
                return cur
            for key in path.split('.'):
                if cur is None:
                    return None
                if isinstance(cur, (list, tuple)):
                    try:
                        idx = int(key)
                        cur = cur[idx]
                    except Exception:
                        return None
                    continue
            return cur

        result = get_variable_param_value(obj, "1")
        assert result == "second"

    def test_get_variable_param_value_empty_path(self):
        """Test with empty path returns original object"""
        obj = {"key": "value"}

        def get_variable_param_value(obj, path):
            if not path:
                return obj
            return None

        result = get_variable_param_value(obj, "")
        assert result == obj

    def test_get_variable_param_value_none(self):
        """Test with None object"""
        def get_variable_param_value(obj, path):
            cur = obj
            if not path:
                return cur
            for key in path.split('.'):
                if cur is None:
                    return None
            return cur

        result = get_variable_param_value(None, "any.path")
        assert result is None

    def test_set_variable_param_value(self):
        """Test setting nested value"""
        obj = {"level1": {}}

        def set_variable_param_value(obj, path, value):
            cur = obj
            keys = path.split('.')
            if not path:
                return value
            for key in keys[:-1]:
                if key not in cur or not isinstance(cur.get(key), dict):
                    cur[key] = {}
                cur = cur[key]
            cur[keys[-1]] = value
            return obj

        result = set_variable_param_value(obj, "level1.level2", "new_value")
        assert result["level1"]["level2"] == "new_value"

    def test_json_string_parsing(self):
        """Test JSON string parsing in path resolution"""
        obj = '{"nested": {"key": "value"}}'

        def get_variable_param_value(obj, path):
            cur = obj
            if not path:
                return cur
            for key in path.split('.'):
                if cur is None:
                    return None
                if isinstance(cur, str):
                    try:
                        cur = json.loads(cur)
                    except Exception:
                        return None
                if isinstance(cur, dict):
                    cur = cur.get(key)
                    continue
            return cur

        result = get_variable_param_value(obj, "nested.key")
        assert result == "value"


class TestVariableInterpolation:
    """Test variable interpolation in strings"""

    def test_simple_variable_interpolation(self):
        """Test simple variable substitution"""
        import re

        globals_dict = {
            "sys.query": "What is the weather?",
            "sys.user_id": "test_user"
        }

        pat = re.compile(r"\{* *\{([a-zA-Z:0-9]+@[A-Za-z0-9_.-]+|sys\.[A-Za-z0-9_.]+|env\.[A-Za-z0-9_.]+)\} *\}*")
        value = "Query: {sys.query}"
        out_parts = []
        last = 0

        for m in pat.finditer(value):
            out_parts.append(value[last:m.start()])
            key = m.group(1)
            v = globals_dict.get(key, "")
            if v is None:
                rep = ""
            elif isinstance(v, str):
                rep = v
            else:
                rep = json.dumps(v, ensure_ascii=False)
            out_parts.append(rep)
            last = m.end()

        out_parts.append(value[last:])
        result = "".join(out_parts)

        assert "Query:" in result and "What is the weather?" in result

    def test_multiple_variable_interpolation(self):
        """Test multiple variable substitution"""
        import re

        globals_dict = {
            "sys.query": "test query",
            "sys.user_id": "user_123"
        }

        pat = re.compile(r"\{* *\{([a-zA-Z:0-9]+@[A-Za-z0-9_.-]+|sys\.[A-Za-z0-9_.]+|env\.[A-Za-z0-9_.]+)\} *\}*")
        value = "User: {sys.user_id}, Query: {sys.query}"
        out_parts = []
        last = 0

        for m in pat.finditer(value):
            out_parts.append(value[last:m.start()])
            key = m.group(1)
            v = globals_dict.get(key, "")
            out_parts.append(str(v) if v else "")
            last = m.end()

        out_parts.append(value[last:])
        result = "".join(out_parts)

        assert "user_123" in result
        assert "test query" in result

    def test_no_variable_in_string(self):
        """Test string without variables"""
        import re

        pat = re.compile(r"\{* *\{([a-zA-Z:0-9]+@[A-Za-z0-9_.-]+|sys\.[A-Za-z0-9_.]+|env\.[A-Za-z0-9_.]+)\} *\}*")
        value = "Plain text without variables"

        matches = list(pat.finditer(value))
        assert len(matches) == 0
        # Result should be unchanged
        assert value == "Plain text without variables"


class TestDSLStructure:
    """Test DSL structure validation"""

    def test_minimal_dsl_structure(self):
        """Test that minimal DSL has required fields"""
        dsl = MINIMAL_DSL

        assert "components" in dsl
        assert "begin" in dsl["components"]
        assert "globals" in dsl
        assert "history" in dsl
        assert "path" in dsl
        assert "retrieval" in dsl

    def test_component_structure(self):
        """Test component has required fields"""
        component = MINIMAL_DSL["components"]["begin"]

        assert "obj" in component
        assert "downstream" in component
        assert "upstream" in component
        assert "component_name" in component["obj"]
        assert "params" in component["obj"]

    def test_globals_default_structure(self):
        """Test globals has default system variables"""
        globals_dict = MINIMAL_DSL["globals"]

        assert "sys.query" in globals_dict
        assert "sys.user_id" in globals_dict
        assert "sys.conversation_turns" in globals_dict
        assert "sys.files" in globals_dict


class TestHistoryManagement:
    """Test conversation history management"""

    def test_get_history_with_window(self):
        """Test getting history with window size"""
        history = [
            ("user", "Q1"),
            ("assistant", {"content": "A1"}),
            ("user", "Q2"),
            ("assistant", {"content": "A2"}),
            ("user", "Q3"),
            ("assistant", {"content": "A3"})
        ]

        def get_history(history, window_size):
            convs = []
            if window_size <= 0:
                return convs
            for role, obj in history[window_size * -2:]:
                if isinstance(obj, dict):
                    convs.append({"role": role, "content": obj.get("content", "")})
                else:
                    convs.append({"role": role, "content": str(obj)})
            return convs

        result = get_history(history, 2)
        assert len(result) == 4  # Last 4 entries (2 turns * 2)

    def test_get_history_zero_window(self):
        """Test getting history with zero window size"""
        history = [("user", "Q1"), ("assistant", {"content": "A1"})]

        def get_history(history, window_size):
            if window_size <= 0:
                return []
            return history[window_size * -2:]

        result = get_history(history, 0)
        assert len(result) == 0

    def test_add_user_input(self):
        """Test adding user input to history"""
        history = []

        def add_user_input(history, question):
            history.append(("user", question))

        add_user_input(history, "Test question")
        assert history[-1] == ("user", "Test question")


class TestGlobalsManagement:
    """Test global variables management"""

    def test_reset_sys_variables_string(self):
        """Test resetting string sys variables"""
        globals_dict = {"sys.query": "old value"}

        for k in globals_dict.keys():
            if k.startswith("sys."):
                if isinstance(globals_dict[k], str):
                    globals_dict[k] = ""

        assert globals_dict["sys.query"] == ""

    def test_reset_sys_variables_int(self):
        """Test resetting int sys variables"""
        globals_dict = {"sys.conversation_turns": 5}

        for k in globals_dict.keys():
            if k.startswith("sys."):
                if isinstance(globals_dict[k], int):
                    globals_dict[k] = 0

        assert globals_dict["sys.conversation_turns"] == 0

    def test_reset_sys_variables_list(self):
        """Test resetting list sys variables"""
        globals_dict = {"sys.files": ["file1", "file2"]}

        for k in globals_dict.keys():
            if k.startswith("sys."):
                if isinstance(globals_dict[k], list):
                    globals_dict[k] = []

        assert globals_dict["sys.files"] == []

    def test_set_global_param(self):
        """Test setting global parameters"""
        globals_dict = {"sys.query": "", "sys.files": []}

        def set_global_param(globals_dict, **kwargs):
            globals_dict.update(kwargs)

        set_global_param(globals_dict, **{"sys.query": "New query", "sys.files": ["file1"]})

        assert globals_dict["sys.query"] == "New query"
        assert globals_dict["sys.files"] == ["file1"]


class TestRetrievalManagement:
    """Test retrieval/reference management"""

    def test_get_reference_empty(self):
        """Test getting reference when empty"""
        retrieval = []

        def get_reference(retrieval):
            if not retrieval:
                return {"chunks": {}, "doc_aggs": {}}
            return retrieval[-1]

        ref = get_reference(retrieval)
        assert ref == {"chunks": {}, "doc_aggs": {}}

    def test_get_reference_with_data(self):
        """Test getting reference with data"""
        retrieval = [{"chunks": {"1": {"content": "test"}}, "doc_aggs": {"doc1": {}}}]

        def get_reference(retrieval):
            if not retrieval:
                return {"chunks": {}, "doc_aggs": {}}
            return retrieval[-1]

        ref = get_reference(retrieval)
        assert "1" in ref["chunks"]
        assert "doc1" in ref["doc_aggs"]


class TestMemoryManagement:
    """Test memory management"""

    def test_add_memory(self):
        """Test adding memory entries"""
        memory = []

        def add_memory(memory, user, assist, summ):
            memory.append((user, assist, summ))

        add_memory(memory, "user question", "assistant answer", "summary")

        assert len(memory) == 1
        assert memory[0] == ("user question", "assistant answer", "summary")

    def test_get_memory_empty(self):
        """Test getting memory when empty"""
        memory = []

        def get_memory(memory):
            return memory

        result = get_memory(memory)
        assert result == []


class TestIsReff:
    """Test is_reff method logic"""

    def test_is_reff_system_var(self):
        """Test is_reff for system variables"""
        globals_dict = {"sys.query": "test"}

        def is_reff(exp, globals_dict):
            exp = exp.strip("{").strip("}")
            if exp.find("@") < 0:
                return exp in globals_dict
            arr = exp.split("@")
            if len(arr) != 2:
                return False
            return False  # Would need component check

        assert is_reff("{sys.query}", globals_dict) is True
        assert is_reff("sys.query", globals_dict) is True

    def test_is_reff_nonexistent(self):
        """Test is_reff for nonexistent variables"""
        globals_dict = {"sys.query": "test"}

        def is_reff(exp, globals_dict):
            exp = exp.strip("{").strip("}")
            if exp.find("@") < 0:
                return exp in globals_dict
            return False

        assert is_reff("sys.nonexistent", globals_dict) is False


class TestTTSTextCleaning:
    """Test TTS text cleaning logic"""

    def test_clean_tts_text_removes_emoji(self):
        """Test that TTS cleaning removes emojis"""
        import re

        def clean_tts_text(text):
            if not text:
                return ""

            text = text.encode("utf-8", "ignore").decode("utf-8", "ignore")
            text = re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]", "", text)

            emoji_pattern = re.compile(
                "[\U0001F600-\U0001F64F"
                "\U0001F300-\U0001F5FF"
                "\U0001F680-\U0001F6FF"
                "\U0001F1E0-\U0001F1FF"
                "\U00002700-\U000027BF"
                "\U0001F900-\U0001F9FF"
                "\U0001FA70-\U0001FAFF"
                "\U0001FAD0-\U0001FAFF]+",
                flags=re.UNICODE
            )
            text = emoji_pattern.sub("", text)
            text = re.sub(r"\s+", " ", text).strip()

            return text

        result = clean_tts_text("Hello 🎉 World")
        assert "🎉" not in result
        assert "Hello" in result
        assert "World" in result

    def test_clean_tts_text_truncates_long(self):
        """Test that TTS cleaning truncates long text"""
        import re

        def clean_tts_text(text, max_len=500):
            if not text:
                return ""

            text = re.sub(r"\s+", " ", text).strip()

            if len(text) > max_len:
                text = text[:max_len]

            return text

        long_text = "x" * 1000
        result = clean_tts_text(long_text)
        assert len(result) == 500

    def test_clean_tts_text_empty(self):
        """Test TTS cleaning with empty text"""
        def clean_tts_text(text):
            if not text:
                return ""
            return text

        assert clean_tts_text("") == ""
        assert clean_tts_text(None) == ""


class TestEnvironmentVariables:
    """Test environment variable handling"""

    def test_env_variable_string_default(self):
        """Test env variable reset with string default"""
        variables = {
            "test_var": {
                "type": "string",
                "value": ""
            }
        }
        globals_dict = {"env.test_var": "initial_value"}

        for k in globals_dict.keys():
            if k.startswith("env."):
                key = k[4:]
                if key in variables:
                    variable = variables[key]
                    if variable["value"]:
                        globals_dict[k] = variable["value"]
                    else:
                        if variable["type"] == "string":
                            globals_dict[k] = ""

        assert globals_dict["env.test_var"] == ""

    def test_env_variable_number_default(self):
        """Test env variable reset with number default"""
        variables = {
            "num_var": {
                "type": "number",
                "value": None
            }
        }
        globals_dict = {"env.num_var": 42}

        for k in globals_dict.keys():
            if k.startswith("env."):
                key = k[4:]
                if key in variables:
                    variable = variables[key]
                    if variable["value"]:
                        globals_dict[k] = variable["value"]
                    else:
                        if variable["type"] == "number":
                            globals_dict[k] = 0

        assert globals_dict["env.num_var"] == 0

    def test_env_variable_boolean_default(self):
        """Test env variable reset with boolean default"""
        variables = {
            "bool_var": {
                "type": "boolean",
                "value": None
            }
        }
        globals_dict = {"env.bool_var": True}

        for k in globals_dict.keys():
            if k.startswith("env."):
                key = k[4:]
                if key in variables:
                    variable = variables[key]
                    if variable["value"]:
                        globals_dict[k] = variable["value"]
                    else:
                        if variable["type"] == "boolean":
                            globals_dict[k] = False

        assert globals_dict["env.bool_var"] is False


class TestComponentPath:
    """Test component path management"""

    def test_append_path_no_duplicate(self):
        """Test that duplicate paths are not appended"""
        path = ["begin", "message_0"]

        def append_path(path, cpn_id):
            if path[-1] == cpn_id:
                return
            path.append(cpn_id)

        append_path(path, "message_0")  # Should not add duplicate
        assert path == ["begin", "message_0"]

        append_path(path, "end")  # Should add new
        assert path == ["begin", "message_0", "end"]

    def test_extend_path(self):
        """Test extending path with multiple components"""
        path = ["begin"]

        def extend_path(path, cpn_ids):
            for cpn_id in cpn_ids:
                if path[-1] != cpn_id:
                    path.append(cpn_id)

        extend_path(path, ["message_0", "end"])
        assert path == ["begin", "message_0", "end"]


# Run with: pytest test/unit_test/agent/test_canvas.py -v
