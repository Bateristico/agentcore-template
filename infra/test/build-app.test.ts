import * as path from 'path';
import { App, Aspects } from 'aws-cdk-lib';
import { Annotations, Match } from 'aws-cdk-lib/assertions';
import { AwsSolutionsChecks } from 'cdk-nag';
import { buildApp } from '../lib/build-app';
import { loadConfig } from '../lib/config';
import { loadPricing, parsePricing } from '../lib/pricing';

const ROOT = path.resolve(__dirname, '..', '..');
const AGENTS_DIR = path.join(ROOT, 'agents');
const config = loadConfig(path.join(ROOT, 'config', 'deploy.yaml'));
const pricing = loadPricing(path.join(ROOT, 'config', 'pricing.yaml'));

test('creates the platform stack and one stack per agent, with no cdk-nag errors', () => {
  const app = new App();
  const { platform, agents } = buildApp(app, config, pricing, AGENTS_DIR);
  Aspects.of(app).add(new AwsSolutionsChecks());
  expect(platform.stackName).toBe('tmpl-platform');
  expect(Object.keys(agents).sort()).toEqual(Object.keys(config.agents).sort());
  for (const stack of [platform, ...Object.values(agents)]) {
    expect(Annotations.fromStack(stack).findError('*', Match.stringLikeRegexp('AwsSolutions-.*'))).toEqual([]);
  }
});

test('a model without a price fails with a clear error', () => {
  const partial = parsePricing('as_of: 2026-10-09\nagentcore: {vcpu_hour: 1, gb_hour: 1}\nmodels:\n  haiku-4-5: {input: 1, output: 1}\n');
  expect(() => buildApp(new App(), config, partial, AGENTS_DIR)).toThrow('pricing.yaml has no price for');
});
