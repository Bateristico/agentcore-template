from validation.gates_deploy import other_runtimes, profile_dimension_values, wait_for_profile_metrics, wait_runtime_ready


class FakeControl:
    def __init__(self, pages, statuses=None):
        self.pages = pages
        self.statuses = list(statuses or [])

    def list_agent_runtimes(self, **kwargs):
        idx = int(kwargs.get("nextToken", 0))
        page = {"agentRuntimes": self.pages[idx]}
        if idx + 1 < len(self.pages):
            page["nextToken"] = str(idx + 1)
        return page

    def get_agent_runtime(self, agentRuntimeId):
        return {"status": self.statuses.pop(0)}


def test_other_runtimes_excludes_prefix_and_paginates():
    control = FakeControl([
        [{"agentRuntimeName": "OtherTeamRuntime", "agentRuntimeVersion": "18"}, {"agentRuntimeName": "tmpl_tools_agent", "agentRuntimeVersion": "1"}],
        [{"agentRuntimeName": "tmpl2_tools_agent", "agentRuntimeVersion": "1"}],
    ])
    assert other_runtimes(control, "tmpl") == {"OtherTeamRuntime": "18", "tmpl2_tools_agent": "1"}


def test_wait_runtime_ready():
    assert wait_runtime_ready(FakeControl([], ["CREATING", "READY"]), "id", sleep=lambda s: None) == "READY"
    assert wait_runtime_ready(FakeControl([], ["CREATE_FAILED"]), "id", sleep=lambda s: None) == "CREATE_FAILED"


def test_profile_dimension_values():
    arn = "arn:aws:bedrock:eu-central-1:1:application-inference-profile/abc123"
    assert profile_dimension_values(arn) == ["abc123", arn]


class FakeCW:
    def __init__(self, present):
        self.present = present

    def list_metrics(self, Namespace, MetricName, Dimensions):
        return {"Metrics": [{}] if Dimensions[0]["Value"] in self.present else []}


def test_wait_for_profile_metrics_detects_dimension_form():
    a = "arn:aws:bedrock:eu-central-1:1:application-inference-profile/aaa"
    b = "arn:aws:bedrock:eu-central-1:1:application-inference-profile/bbb"
    found, form = wait_for_profile_metrics(FakeCW({"aaa"}), [a, b], timeout=0.05, sleep=lambda s: None)
    assert found == {a: "aaa"} and form == "profile-id"
