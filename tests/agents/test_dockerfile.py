from template_core.config import REPO_ROOT


def test_image_installs_the_kit_with_the_committed_lock_as_constraints():
    dockerfile = (REPO_ROOT / "agents" / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install -c /app/_kit/requirements.lock /app/_kit" in dockerfile
    lock = (REPO_ROOT / "agents" / "_kit" / "requirements.lock").read_text(encoding="utf-8").splitlines()
    pins = [line for line in lock if line and not line.startswith((" ", "#"))]
    assert pins and all("==" in line for line in pins)  # pinned third-party deps only
    assert not any(line.startswith(("agentkit", "-e", ".")) for line in pins)
