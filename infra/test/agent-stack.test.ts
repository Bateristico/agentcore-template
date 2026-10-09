import * as path from 'path';
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { AgentStack } from '../lib/agent-stack';
import { parseConfig } from '../lib/config';

const AGENTS_DIR = path.resolve(__dirname, '..', '..', 'agents');
const CONFIG = parseConfig(`
prefix: t1
region: eu-central-1
models:
  m1: { profile: eu.anthropic.claude-haiku-4-5-20251001-v1:0 }
  g1: { profile: global.anthropic.claude-haiku-4-5-20251001-v1:0 }
  k3: { profile: global.moonshotai.kimi-k3, billing_tag: false }
judge: m1
agents:
  tools-agent: { models: [m1, g1, k3], default: m1 }
`);

function synth(): Template {
  const app = new App();
  const stack = new AgentStack(app, 't1-agent-tools-agent', {
    env: { account: '111111111111', region: 'eu-central-1' },
    config: CONFIG,
    agentName: 'tools-agent',
    agentsDir: AGENTS_DIR,
  });
  return Template.fromStack(stack);
}

const template = synth();

test('one runtime with the spec name, env vars and tags', () => {
  template.resourceCountIs('AWS::BedrockAgentCore::Runtime', 1);
  template.hasResourceProperties('AWS::BedrockAgentCore::Runtime', {
    AgentRuntimeName: 't1_tools_agent',
    EnvironmentVariables: Match.objectLike({ AGENT_NAME: 'tools-agent', DEFAULT_MODEL: 'm1', PROJECT: 't1' }),
    Tags: Match.objectLike({ project: 't1', agent: 'tools-agent' }),
  });
});

test('one application profile per taggable model, copied from its system profile', () => {
  template.resourceCountIs('AWS::Bedrock::ApplicationInferenceProfile', 2);
  template.hasResourceProperties('AWS::Bedrock::ApplicationInferenceProfile', {
    InferenceProfileName: 't1-tools-agent-m1',
    Tags: Match.arrayWith([{ Key: 'model', Value: 'm1' }]),
  });
  const profiles = JSON.stringify(template.findResources('AWS::Bedrock::ApplicationInferenceProfile'));
  expect(profiles).toContain('inference-profile/eu.anthropic.claude-haiku-4-5-20251001-v1:0');
  expect(profiles).toContain('inference-profile/global.anthropic.claude-haiku-4-5-20251001-v1:0');
});

test('MODEL_PROFILES maps an untagged model to its system profile id', () => {
  const runtime = Object.values(template.findResources('AWS::BedrockAgentCore::Runtime'))[0];
  const env = JSON.stringify(runtime.Properties.EnvironmentVariables.MODEL_PROFILES);
  expect(env).toContain('global.moonshotai.kimi-k3');
  expect(env).toContain('"m1');
});

test('bedrock invoke permissions are scoped to the allowed models (never "*")', () => {
  const policies = Object.values(template.findResources('AWS::IAM::Policy'));
  const statements = policies.flatMap((p: any) => p.Properties.PolicyDocument.Statement);
  const invoke = statements.filter((s: any) => ([] as any[]).concat(s.Action).includes('bedrock:InvokeModel'));
  expect(invoke.length).toBeGreaterThan(0);
  for (const s of invoke) {
    for (const r of ([] as any[]).concat(s.Resource)) expect(r).not.toEqual('*');
  }
  const text = JSON.stringify(invoke);
  expect(text).toContain(':bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0');
  expect(text).toContain(':bedrock:::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0');
  expect(text).toContain('foundation-model/moonshotai.kimi-k3');
});

test('outputs consumed by the Python tooling exist', () => {
  for (const key of ['RuntimeArn', 'RuntimeId', 'ModelProfiles', 'DefaultModel']) template.hasOutput(key, {});
});

test('image is built in CodeBuild on an ARM host into a self-emptying ECR repository', () => {
  template.hasResource('AWS::ECR::Repository', { DeletionPolicy: 'Delete' });
  template.hasResourceProperties('AWS::ECR::Repository', { EmptyOnDelete: true });
  template.hasResourceProperties('AWS::CodeBuild::Project', {
    Environment: Match.objectLike({ Type: 'ARM_CONTAINER' }),
  });
});
