import * as fs from 'fs';
import * as path from 'path';
import {
  ConfigError, agentStackName, dashboardName, foundationModelId, judgeProfileName, loadConfig, parseConfig,
  platformStackName, profileName, runtimeName,
} from '../lib/config';

const ROOT = path.resolve(__dirname, '..', '..');
const FIXTURES = path.join(ROOT, 'config', 'fixtures');
const files = fs.readdirSync(FIXTURES);

describe('shared fixtures (same files as tests/template_core/test_config.py)', () => {
  test.each(files.filter((f) => f.startsWith('valid-')))('%s loads', (f) => {
    expect(() => loadConfig(path.join(FIXTURES, f))).not.toThrow();
  });

  test.each(files.filter((f) => f.startsWith('invalid-')))('%s fails with the expected message', (f) => {
    const text = fs.readFileSync(path.join(FIXTURES, f), 'utf8');
    const expected = text.split(/\r?\n/)[0].replace('# expect:', '').trim();
    expect(() => parseConfig(text)).toThrow(ConfigError);
    expect(() => parseConfig(text)).toThrow(expected);
  });
});

test('repo config is valid', () => {
  const cfg = loadConfig(path.join(ROOT, 'config', 'deploy.yaml'));
  expect(cfg.prefix).toBe('tmpl');
  expect(cfg.models[cfg.judge]).toBeDefined();
});

test('overrides win and empty values are ignored', () => {
  const text = fs.readFileSync(path.join(FIXTURES, 'valid-minimal.yaml'), 'utf8');
  const cfg = parseConfig(text, { prefix: 't9', region: undefined, removal: 'destroy' });
  expect(cfg.prefix).toBe('t9');
  expect(cfg.region).toBe('eu-west-1');
  expect(cfg.removal).toBe('destroy');
});

test('unquoted account is a string and billing_tag defaults to true', () => {
  const cfg = loadConfig(path.join(FIXTURES, 'valid-no-billing-tag.yaml'));
  expect(cfg.account).toBe('123456789012');
  expect(cfg.models.m1.billing_tag).toBe(true);
  expect(cfg.models.k3.billing_tag).toBe(false);
});

test('names follow the spec', () => {
  expect(runtimeName('t1', 'tools-agent')).toBe('t1_tools_agent');
  expect(agentStackName('t1', 'tools-agent')).toBe('t1-agent-tools-agent');
  expect(platformStackName('t1')).toBe('t1-platform');
  expect(profileName('t1', 'tools-agent', 'm1')).toBe('t1-tools-agent-m1');
  expect(judgeProfileName('t1')).toBe('t1-judge');
  expect(dashboardName('t1')).toBe('t1-agent-costs');
});

test.each([
  ['eu.anthropic.claude-haiku-4-5-20251001-v1:0', 'anthropic.claude-haiku-4-5-20251001-v1:0'],
  ['global.amazon.nova-2-lite-v1:0', 'amazon.nova-2-lite-v1:0'],
  ['anthropic.claude-3-haiku-20240307-v1:0', 'anthropic.claude-3-haiku-20240307-v1:0'],
])('foundationModelId(%s)', (profile, fm) => {
  expect(foundationModelId(profile)).toBe(fm);
});
