/** CloudWatch dashboard built from deploy.yaml + pricing.yaml at synth time (spec §4). */
import { Duration } from 'aws-cdk-lib';
import * as cw from 'aws-cdk-lib/aws-cloudwatch';
import { DeployConfig } from './config';
import { ModelPrice, Pricing } from './pricing';

export const NAMESPACE = 'AgentTemplate';
const PERIOD = Duration.hours(1);

/** One kit EMF series; Project (= deploy prefix) keeps two copies of the template in one account apart. */
function metric(project: string, agent: string, model: string, name: string, statistic = 'Sum'): cw.Metric {
  return new cw.Metric({
    namespace: NAMESPACE,
    metricName: name,
    dimensionsMap: { Project: project, Agent: agent, Model: model },
    statistic,
    period: PERIOD,
  });
}

/** Estimated USD per period for one agent × model; metric ids are suffixed with `index` to stay unique per graph. */
export function costExpression(project: string, agent: string, model: string, price: ModelPrice, index: number): cw.MathExpression {
  const [i, o, r, w] = ['i', 'o', 'r', 'w'].map((p) => `${p}${index}`);
  return new cw.MathExpression({
    label: `${model} $`,
    period: PERIOD,
    expression: `(${i}*${price.input} + ${o}*${price.output} + ${r}*${price.cache_read} + ${w}*${price.cache_write}) / 1000000`,
    usingMetrics: {
      [i]: metric(project, agent, model, 'InputTokens'),
      [o]: metric(project, agent, model, 'OutputTokens'),
      [r]: metric(project, agent, model, 'CacheReadTokens'),
      [w]: metric(project, agent, model, 'CacheWriteTokens'),
    },
  });
}

export function buildDashboardWidgets(config: DeployConfig, pricing: Pricing): cw.IWidget[][] {
  const rows: cw.IWidget[][] = [[
    new cw.TextWidget({
      markdown:
        `# ${config.prefix} agent costs\n` +
        'Estimated USD = token metrics × prices from `config/pricing.yaml` **at deploy time**. ' +
        'The UI Costs page uses current prices and shows billed cost from Cost Explorer.',
      width: 24,
      height: 2,
    }),
  ]];
  for (const [agent, a] of Object.entries(config.agents)) {
    rows.push([
      new cw.GraphWidget({
        title: `${agent} — estimated USD per hour`,
        left: a.models.map((m, idx) => costExpression(config.prefix, agent, m, pricing.models[m], idx)),
        stacked: true,
        width: 8,
        height: 6,
      }),
      new cw.GraphWidget({
        title: `${agent} — tokens`,
        left: a.models.flatMap((m) => [
          metric(config.prefix, agent, m, 'InputTokens').with({ label: `${m} in` }),
          metric(config.prefix, agent, m, 'OutputTokens').with({ label: `${m} out` }),
        ]),
        width: 8,
        height: 6,
      }),
      new cw.GraphWidget({
        title: `${agent} — latency p50 (ms) / errors`,
        left: a.models.map((m) => metric(config.prefix, agent, m, 'LatencyMs', 'p50').with({ label: `${m} p50` })),
        right: a.models.map((m) => metric(config.prefix, agent, m, 'Errors').with({ label: `${m} errors` })),
        width: 8,
        height: 6,
      }),
    ]);
  }
  return rows;
}
