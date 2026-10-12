"""Sample agent: plain chat, no tools."""

from pathlib import Path

from strands import Agent

PROMPT = (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")


def build_agent(model) -> Agent:
    return Agent(model=model, system_prompt=PROMPT, callback_handler=None)
