import { CfnOutput, Stack, StackProps, Tags } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { DeployConfig } from './config';
import { addAgentNagSuppressions } from './nag';
import { StrandsAgentRuntime } from './strands-agent-runtime';

export interface AgentStackProps extends StackProps {
  readonly config: DeployConfig;
  readonly agentName: string;
  readonly agentsDir: string;
}

export class AgentStack extends Stack {
  constructor(scope: Construct, id: string, props: AgentStackProps) {
    super(scope, id, props);
    Tags.of(this).add('project', props.config.prefix);
    Tags.of(this).add('agent', props.agentName);

    const agent = new StrandsAgentRuntime(this, 'Agent', {
      config: props.config,
      agentName: props.agentName,
      agentsDir: props.agentsDir,
    });

    new CfnOutput(this, 'RuntimeArn', { value: agent.runtime.agentRuntimeArn });
    new CfnOutput(this, 'RuntimeId', { value: agent.runtime.agentRuntimeId });
    new CfnOutput(this, 'ModelProfiles', { value: this.toJsonString(agent.modelProfiles) });
    new CfnOutput(this, 'DefaultModel', { value: props.config.agents[props.agentName].default });
    addAgentNagSuppressions(this);
  }
}
