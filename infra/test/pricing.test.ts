import * as path from 'path';
import { loadConfig } from '../lib/config';
import { loadPricing, missingPrices, parsePricing } from '../lib/pricing';

const ROOT = path.resolve(__dirname, '..', '..');

test('repo pricing covers every repo model', () => {
  const cfg = loadConfig(path.join(ROOT, 'config', 'deploy.yaml'));
  expect(missingPrices(cfg, loadPricing(path.join(ROOT, 'config', 'pricing.yaml')))).toEqual([]);
});

test('cache prices default to zero', () => {
  const p = parsePricing('as_of: 2026-10-09\nagentcore: {vcpu_hour: 0.1, gb_hour: 0.01}\nmodels:\n  m: {input: 1, output: 2}\n');
  expect(p.models.m).toEqual({ input: 1, output: 2, cache_read: 0, cache_write: 0 });
});

test('negative or missing prices are rejected', () => {
  expect(() => parsePricing('as_of: 2026-10-09\nagentcore: {vcpu_hour: 1, gb_hour: 1}\nmodels:\n  m: {input: -1, output: 2}\n')).toThrow('pricing');
  expect(() => parsePricing('as_of: 2026-10-09\nagentcore: {vcpu_hour: 1, gb_hour: 1}\nmodels:\n  m: {output: 2}\n')).toThrow('pricing');
});
