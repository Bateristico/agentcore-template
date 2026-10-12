import { App } from 'aws-cdk-lib';
import { AgentStack } from './agent-stack';
import { ConfigError, DeployConfig, agentStackName, platformStackName } from './config';
import { PlatformStack } from './platform-stack';
import { Pricing, missingPrices } from './pricing';

export function buildApp(app: App, config: DeployConfig, pricing: Pricing, agentsDir: string) {
  const missing = missingPrices(config, pricing);
  if (missing.length) throw new ConfigError(`pricing.yaml has no price for: ${missing.join(', ')}`);

  // A pinned account makes CDK refuse credentials for any other account.
  const env = { account: config.account ?? process.env.CDK_DEFAULT_ACCOUNT, region: config.region };
  const platform = new PlatformStack(app, platformStackName(config.prefix), { env, config, pricing });
  const agents: Record<string, AgentStack> = {};
  for (const agentName of Object.keys(config.agents)) {
    agents[agentName] = new AgentStack(app, agentStackName(config.prefix, agentName), { env, config, agentName, agentsDir });
  }
  return { platform, agents };
}
