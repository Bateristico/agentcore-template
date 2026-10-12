"""Sample agent: order support with tools, to exercise multi-step tool use."""

from pathlib import Path

from strands import Agent
from tools import calculator, lookup_order

PROMPT = (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")


def build_agent(model) -> Agent:
    return Agent(model=model, system_prompt=PROMPT, tools=[calculator, lookup_order], callback_handler=None)
