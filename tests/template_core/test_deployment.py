import json

import pytest
from botocore.exceptions import ClientError

from template_core.config import parse_config
from template_core.deployment import DeploymentNotFound, load_deployment

CFG = parse_config(
    """
prefix: t1
region: eu-central-1
models:
  m1: { profile: eu.amazon.nova-pro-v1:0 }
judge: m1
agents:
  tools-agent: { models: [m1], default: m1 }
"""
)

OUTPUTS = {
    "t1-platform": {"ResultsBucketName": "bucket-1", "JudgeProfileArn": "arn:judge", "JudgeModel": "m1"},
    "t1-agent-tools-agent": {
        "RuntimeArn": "arn:aws:bedrock-agentcore:eu-central-1:1:runtime/t1_tools_agent-abc",
        "RuntimeId": "t1_tools_agent-abc",
        "ModelProfiles": json.dumps({"m1": "arn:profile"}),
        "DefaultModel": "m1",
    },
}


class FakeCfn:
    def __init__(self, outputs):
        self.outputs = outputs

    def describe_stacks(self, StackName):
        if StackName not in self.outputs:
            raise ClientError({"Error": {"Code": "ValidationError", "Message": f"Stack with id {StackName} does not exist"}}, "DescribeStacks")
        return {"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in self.outputs[StackName].items()]}]}


class FakeSession:
    def __init__(self, cfn):
        self.cfn = cfn

    def client(self, name, region_name=None):
        assert name == "cloudformation" and region_name == "eu-central-1"
        return self.cfn


def test_load_deployment_reads_outputs():
    d = load_deployment(CFG, FakeSession(FakeCfn(OUTPUTS)))
    assert d.results_bucket == "bucket-1" and d.judge_profile == "arn:judge" and d.judge_model == "m1"
    ep = d.agents["tools-agent"]
    assert ep.runtime_id == "t1_tools_agent-abc" and ep.model_profiles == {"m1": "arn:profile"} and ep.default_model == "m1"


def test_missing_stack_says_how_to_fix():
    with pytest.raises(DeploymentNotFound, match="cdk deploy"):
        load_deployment(CFG, FakeSession(FakeCfn({"t1-platform": OUTPUTS["t1-platform"]})))
