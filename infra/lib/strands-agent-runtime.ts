/** One AgentCore runtime for one agent folder + a tagged application inference profile per allowed model (spec §4). */
import { RemovalPolicy, Stack } from 'aws-cdk-lib';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { ContainerImageBuild } from '@cdklabs/deploy-time-build';
import { DeployConfig, foundationModelId, profileName, runtimeName } from './config';

export interface StrandsAgentRuntimeProps {
  readonly config: DeployConfig;
  readonly agentName: string;
  /** Absolute path of the repo's agents/ directory (Docker build context). */
  readonly agentsDir: string;
}

export class StrandsAgentRuntime extends Construct {
  public readonly runtime: agentcore.Runtime;
  /** alias -> model id the agent calls: application profile ARN, or the system profile id when billing_tag is false. */
  public readonly modelProfiles: Record<string, string> = {};

  constructor(scope: Construct, id: string, props: StrandsAgentRuntimeProps) {
    super(scope, id);
    const { config, agentName } = props;
    const stack = Stack.of(this);
    const agentCfg = config.agents[agentName];
    const invokeResources: string[] = [];

    for (const alias of agentCfg.models) {
      const model = config.models[alias];
      const systemProfileArn = stack.formatArn({ service: 'bedrock', resource: 'inference-profile', resourceName: model.profile });
      const fm = foundationModelId(model.profile);
      invokeResources.push(
        systemProfileArn,
        `arn:${stack.partition}:bedrock:*::foundation-model/${fm}`, // EU/global profiles route across regions
        `arn:${stack.partition}:bedrock:::foundation-model/${fm}`, // region-less form required for global profiles
      );
      if (model.billing_tag) {
        const profile = new bedrock.CfnApplicationInferenceProfile(this, `Profile-${alias}`, {
          inferenceProfileName: profileName(config.prefix, agentName, alias),
          description: `${agentName} on ${alias}`,
          modelSource: { copyFrom: systemProfileArn },
          tags: [{ key: 'model', value: alias }],
        });
        this.modelProfiles[alias] = profile.attrInferenceProfileArn;
        invokeResources.push(profile.attrInferenceProfileArn);
      } else {
        this.modelProfiles[alias] = model.profile;
      }
    }

    // arm64 image built in CodeBuild on an ARM host at deploy time (no local Docker needed).
    const repository = new ecr.Repository(this, 'Repository', {
      removalPolicy: RemovalPolicy.DESTROY,
      emptyOnDelete: true,
      imageScanOnPush: true,
    });
    const image = new ContainerImageBuild(this, 'Image', {
      directory: props.agentsDir,
      file: 'Dockerfile',
      platform: Platform.LINUX_ARM64,
      buildArgs: { AGENT: agentName },
      repository,
    });

    this.runtime = new agentcore.Runtime(this, 'Runtime', {
      runtimeName: runtimeName(config.prefix, agentName),
      description: `Strands agent ${agentName} (${config.prefix})`,
      agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromEcrRepository(repository, image.imageTag),
      environmentVariables: {
        AGENT_NAME: agentName,
        PROJECT: config.prefix, // EMF Project dimension: separates copies of the template in one account
        DEFAULT_MODEL: agentCfg.default,
        MODEL_PROFILES: stack.toJsonString(this.modelProfiles),
      },
      tags: { project: config.prefix, agent: agentName },
    });

    this.runtime.role.addToPrincipalPolicy(
      new iam.PolicyStatement({
        sid: 'InvokeAllowedModels',
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: invokeResources,
      }),
    );
  }
}
