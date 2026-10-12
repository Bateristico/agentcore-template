import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { parseConfig } from '../lib/config';
import { PlatformStack } from '../lib/platform-stack';
import { parsePricing } from '../lib/pricing';

const PRICING = parsePricing(`
as_of: 2026-10-09
agentcore: { vcpu_hour: 0.0895, gb_hour: 0.00945 }
models:
  m1: { input: 1.1, output: 5.5, cache_read: 0.11, cache_write: 1.375 }
  n1: { input: 1.05, output: 4.2 }
`);
const YAML_CFG = `
prefix: t1
region: eu-central-1
models:
  m1: { profile: eu.anthropic.claude-haiku-4-5-20251001-v1:0 }
  n1: { profile: eu.amazon.nova-pro-v1:0 }
judge: m1
agents:
  tools-agent: { models: [m1, n1], default: m1 }
`;

function synth(removal: 'retain' | 'destroy'): Template {
  const app = new App();
  const config = parseConfig(YAML_CFG, { removal });
  const stack = new PlatformStack(app, 't1-platform', { env: { account: '111111111111', region: 'eu-central-1' }, config, pricing: PRICING });
  return Template.fromStack(stack);
}

test('results bucket is private, encrypted, SSL-only and retained by default', () => {
  const t = synth('retain');
  t.hasResource('AWS::S3::Bucket', { DeletionPolicy: 'Retain' });
  t.hasResourceProperties('AWS::S3::Bucket', {
    PublicAccessBlockConfiguration: { BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true },
    BucketEncryption: Match.anyValue(),
  });
  t.hasResourceProperties('AWS::S3::BucketPolicy', {
    PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({ Effect: 'Deny', Condition: { Bool: { 'aws:SecureTransport': 'false' } } })]) },
  });
  t.resourceCountIs('Custom::S3AutoDeleteObjects', 0);
});

test('removal: destroy deletes the bucket and its objects', () => {
  const t = synth('destroy');
  t.hasResource('AWS::S3::Bucket', { DeletionPolicy: 'Delete' });
  t.resourceCountIs('Custom::S3AutoDeleteObjects', 1);
});

test('judge profile is tagged role=judge', () => {
  synth('retain').hasResourceProperties('AWS::Bedrock::ApplicationInferenceProfile', {
    InferenceProfileName: 't1-judge',
    Tags: Match.arrayWith([{ Key: 'role', Value: 'judge' }]),
  });
});

test('dashboard has the spec name and prices baked into metric math', () => {
  const t = synth('retain');
  t.hasResourceProperties('AWS::CloudWatch::Dashboard', { DashboardName: 't1-agent-costs' });
  const body = JSON.stringify(t.findResources('AWS::CloudWatch::Dashboard'));
  expect(body).toContain('AgentTemplate');
  expect(body).toContain('i0*1.1');
  expect(body).toContain('o1*4.2');
  expect(body).toContain('tools-agent');
});

test('dashboard metrics are scoped to this deployment by the Project dimension', () => {
  const body = JSON.stringify(synth('retain').findResources('AWS::CloudWatch::Dashboard')).replace(/\\/g, '');
  expect(body).toContain('"Agent","tools-agent","Model","m1","Project","t1"');
});

test('outputs consumed by the Python tooling exist', () => {
  const t = synth('retain');
  for (const key of ['ResultsBucketName', 'JudgeProfileArn', 'JudgeModel']) t.hasOutput(key, {});
});
