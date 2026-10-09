/** Reads config/pricing.yaml for the dashboard's $ estimates (spec §4). USD per 1M tokens. */
import * as fs from 'fs';
import * as YAML from 'yaml';
import { DeployConfig } from './config';

export interface ModelPrice { input: number; output: number; cache_read: number; cache_write: number }
export interface Pricing { models: Record<string, ModelPrice>; agentcore: { vcpu_hour: number; gb_hour: number } }

function price(value: unknown, where: string, required: boolean): number {
  if (value === undefined && !required) return 0;
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    throw new Error(`invalid pricing: ${where} must be a non-negative number`);
  }
  return value;
}

export function parsePricing(text: string): Pricing {
  const raw = YAML.parse(text) ?? {};
  const models: Record<string, ModelPrice> = {};
  for (const [alias, m] of Object.entries<any>(raw.models ?? {})) {
    models[alias] = {
      input: price(m?.input, `${alias}.input`, true),
      output: price(m?.output, `${alias}.output`, true),
      cache_read: price(m?.cache_read, `${alias}.cache_read`, false),
      cache_write: price(m?.cache_write, `${alias}.cache_write`, false),
    };
  }
  return {
    models,
    agentcore: {
      vcpu_hour: price(raw.agentcore?.vcpu_hour, 'agentcore.vcpu_hour', true),
      gb_hour: price(raw.agentcore?.gb_hour, 'agentcore.gb_hour', true),
    },
  };
}

export function loadPricing(file: string): Pricing {
  return parsePricing(fs.readFileSync(file, 'utf8'));
}

export function missingPrices(config: DeployConfig, pricing: Pricing): string[] {
  return Object.keys(config.models).filter((alias) => !(alias in pricing.models));
}
