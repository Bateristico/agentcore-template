import importlib.util
import json
import sys

import pytest

from template_core.config import REPO_ROOT

TOOLS_PATH = REPO_ROOT / "agents" / "tools-agent" / "tools.py"


@pytest.fixture(scope="module")
def tools():
    spec = importlib.util.spec_from_file_location("tools_agent_tools", TOOLS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["tools_agent_tools"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("expr", "expected"),
    [("0.17 * 2340", "397.8"), ("(349 + 89) * 1", "438"), ("2 ** 10", "1024"), ("-3 + 5", "2"), ("7 % 4", "3"),
     ("12345.67 * 1", "12345.67"), ("1234567.89 + 0", "1234567.89")],
)
def test_calculate(tools, expr, expected):
    assert tools.calculate(expr) == expected


@pytest.mark.parametrize("expr", ["1 / 0", "__import__('os')", "a + 1", "2 ** 1000", "", "1 +"])
def test_calculate_rejects_unsafe_or_invalid(tools, expr):
    assert tools.calculate(expr).startswith("error:")


def test_order_status(tools):
    assert json.loads(tools.order_status(" a101 "))["status"] == "processing"
    assert tools.order_status("Z999") == "no order with id Z999"


def test_agent_files_exist():
    for name in ("hello-agent", "tools-agent"):
        folder = REPO_ROOT / "agents" / name
        assert (folder / "agent.py").exists() and (folder / "prompt.md").read_text(encoding="utf-8").strip()
        assert (folder / "requirements.txt").exists()


def test_build_agent_constructs_strands_agents():
    from agentkit.server import load_agent_module
    from strands.models import BedrockModel

    model = BedrockModel(model_id="eu.amazon.nova-pro-v1:0", region_name="eu-central-1")
    for name in ("hello-agent", "tools-agent"):
        agent = load_agent_module(REPO_ROOT / "agents" / name).build_agent(model)
        assert agent.system_prompt
    tools_agent = load_agent_module(REPO_ROOT / "agents" / "tools-agent").build_agent(model)
    assert set(tools_agent.tool_names) == {"calculator", "lookup_order"}
