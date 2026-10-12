/**
 * Deployment configuration: loads and validates config/deploy.yaml (spec §4).
 * Twin of template_core/config.py; both are tested against config/fixtures/*.yaml.
 */
import * as fs from 'fs';
import * as YAML from 'yaml';

export class ConfigError extends Error {}

export interface ModelCfg { profile: string; billing_tag: boolean }
export interface AgentCfg { models: string[]; default: string }
export interface DeployConfig {
  prefix: string;
  region: string;
  account?: string;
  removal: 'retain' | 'destroy';
  models: Record<string, ModelCfg>;
  judge: string;
  agents: Record<string, AgentCfg>;
}
export interface ConfigOverrides { prefix?: string; region?: string; account?: string; removal?: string }

const PREFIX_RE = /^[a-z][a-z0-9]{1,11}$/;
const REGION_RE = /^[a-z]{2}(-[a-z]+)+-\d$/;
const ACCOUNT_RE = /^\d{12}$/;
const NAME_RE = /^[a-z][a-z0-9]*(-[a-z0-9]+)*$/;
const ALIAS_RE = /^[a-z0-9]+([.-][a-z0-9]+)*$/;
const RUNTIME_NAME_RE = /^[a-zA-Z][a-zA-Z0-9_]{0,47}$/;
const ALIAS_MAX = 24;
const PROFILE_NAME_MAX = 64;
const GEO_PREFIXES = new Set(['eu', 'us', 'apac', 'global', 'jp', 'au', 'ca', 'us-gov']);
const TOP_KEYS = new Set(['prefix', 'region', 'account', 'removal', 'models', 'judge', 'agents']);

export const runtimeName = (prefix: string, agent: string) => `${prefix}_${agent.replace(/-/g, '_')}`;
export const agentStackName = (prefix: string, agent: string) => `${prefix}-agent-${agent}`;
export const platformStackName = (prefix: string) => `${prefix}-platform`;
export const profileName = (prefix: string, agent: string, alias: string) => `${prefix}-${agent}-${alias}`;
export const judgeProfileName = (prefix: string) => `${prefix}-judge`;
export const dashboardName = (prefix: string) => `${prefix}-agent-costs`;

export function foundationModelId(profile: string): string {
  const [head, ...rest] = profile.split('.');
  return GEO_PREFIXES.has(head) && rest.length > 0 ? rest.join('.') : profile;
}

const isObject = (v: unknown): v is Record<string, any> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown) => (typeof v === 'string' ? v : '');

export function parseConfig(text: string, overrides: ConfigOverrides = {}): DeployConfig {
  let raw: unknown;
  try {
    raw = YAML.parse(text) ?? {};
  } catch (e) {
    throw new ConfigError(`deploy.yaml is not valid YAML: ${(e as Error).message}`);
  }
  if (!isObject(raw)) throw new ConfigError('deploy.yaml must be a mapping');
  for (const [k, v] of Object.entries(overrides)) if (v !== undefined && v !== null && v !== '') raw[k] = v;

  const problems: string[] = [];
  for (const k of Object.keys(raw)) if (!TOP_KEYS.has(k)) problems.push(`${k}: unknown key`);
  const prefix = str(raw.prefix);
  if (!PREFIX_RE.test(prefix)) problems.push(`prefix: must match ${PREFIX_RE}`);
  if (!REGION_RE.test(str(raw.region))) problems.push('region: must look like eu-central-1');
  const account = raw.account === undefined || raw.account === null ? undefined : String(raw.account);
  if (account !== undefined && !ACCOUNT_RE.test(account)) problems.push('account: must be 12 digits');
  const removal = raw.removal ?? 'retain';
  if (removal !== 'retain' && removal !== 'destroy') problems.push("removal: must be 'retain' or 'destroy'");

  const models: Record<string, ModelCfg> = {};
  if (!isObject(raw.models) || Object.keys(raw.models).length === 0) {
    problems.push('models: at least one model is required');
  } else {
    for (const [alias, m] of Object.entries(raw.models)) {
      if (!ALIAS_RE.test(alias) || alias.length > ALIAS_MAX) problems.push(`model alias '${alias}' must match ${ALIAS_RE} and be at most ${ALIAS_MAX} chars`);
      if (!isObject(m) || typeof m.profile !== 'string' || m.profile.length < 3) {
        problems.push(`models.${alias}.profile: required`);
        continue;
      }
      const extra = Object.keys(m).filter((k) => k !== 'profile' && k !== 'billing_tag');
      if (extra.length) problems.push(`models.${alias}: unknown key(s) ${extra.join(', ')}`);
      if (m.billing_tag !== undefined && typeof m.billing_tag !== 'boolean') {
        problems.push(`models.${alias}.billing_tag: must be true or false`);
      }
      models[alias] = { profile: m.profile, billing_tag: m.billing_tag ?? true };
    }
  }

  const judge = str(raw.judge);
  if (!(judge in models)) problems.push(`judge '${judge}' is not a defined model alias`);

  const agents: Record<string, AgentCfg> = {};
  if (!isObject(raw.agents) || Object.keys(raw.agents).length === 0) {
    problems.push('agents: at least one agent is required');
  } else {
    for (const [name, a] of Object.entries(raw.agents)) {
      if (!NAME_RE.test(name)) {
        problems.push(`agent name '${name}' must match ${NAME_RE}`);
        continue;
      }
      const rn = runtimeName(prefix, name);
      if (!RUNTIME_NAME_RE.test(rn)) problems.push(`runtime name '${rn}' for agent '${name}' is longer than 48 chars or invalid`);
      if (isObject(a)) {
        const extraAgent = Object.keys(a).filter((k) => k !== 'models' && k !== 'default');
        if (extraAgent.length) problems.push(`agents.${name}: unknown key(s) ${extraAgent.join(', ')}`);
      }
      const list: string[] = isObject(a) && Array.isArray(a.models) ? a.models.map(String) : [];
      if (list.length === 0) problems.push(`agents.${name}.models: at least one model is required`);
      for (const alias of list) {
        if (!(alias in models)) problems.push(`agent '${name}' uses unknown model alias '${alias}'`);
        else if (profileName(prefix, name, alias).length > PROFILE_NAME_MAX) problems.push(`profile name '${profileName(prefix, name, alias)}' exceeds ${PROFILE_NAME_MAX} chars`);
      }
      const def = isObject(a) ? str(a.default) : '';
      if (!list.includes(def)) problems.push(`agent '${name}' default '${def}' is not in its models list`);
      agents[name] = { models: list, default: def };
    }
  }

  if (problems.length) throw new ConfigError(`invalid deploy.yaml: ${problems.join('; ')}`);
  return { prefix, region: str(raw.region), account, removal: removal as 'retain' | 'destroy', models, judge, agents };
}

export function loadConfig(file: string, overrides?: ConfigOverrides): DeployConfig {
  if (!fs.existsSync(file)) throw new ConfigError(`config file not found: ${file}`);
  return parseConfig(fs.readFileSync(file, 'utf8'), overrides);
}
