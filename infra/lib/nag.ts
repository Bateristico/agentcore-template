/** cdk-nag suppressions. Each one carries the reason it is acceptable (spec §6 G1). */
import { Stack } from 'aws-cdk-lib';
import { NagSuppressions } from 'cdk-nag';

export function addAgentNagSuppressions(stack: Stack): void {
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-IAM5',
      reason:
        'Execution role: CloudWatch Logs / X-Ray wildcards come from the AgentCore Runtime construct defaults; ' +
        'Bedrock foundation-model ARNs use a region wildcard because EU/global inference profiles route across regions, ' +
        'and each ARN is still scoped to one model id. Image build: deploy-time-build CodeBuild/Lambda roles use library-managed wildcards.',
    },
    { id: 'AwsSolutions-IAM4', reason: 'deploy-time-build custom resource Lambda uses the AWS-managed basic execution role (library-managed).' },
    { id: 'AwsSolutions-L1', reason: 'deploy-time-build and emptyOnDelete custom resource Lambda runtimes are managed by the libraries.' },
    { id: 'AwsSolutions-CB3', reason: 'CodeBuild privileged mode is required to run docker build for the agent image.' },
    { id: 'AwsSolutions-CB4', reason: 'Image build project has no secrets or artifacts; AWS-managed encryption is sufficient for this template.' },
  ]);
}

export function addPlatformNagSuppressions(stack: Stack): void {
  NagSuppressions.addStackSuppressions(stack, [
    { id: 'AwsSolutions-S1', reason: 'Results bucket holds benchmark outputs only; server access logs are not needed for this template.' },
    { id: 'AwsSolutions-IAM4', reason: 'autoDeleteObjects custom resource Lambda (removal: destroy only) uses the CDK-managed basic execution role.' },
    { id: 'AwsSolutions-L1', reason: 'autoDeleteObjects custom resource Lambda runtime is managed by CDK.' },
  ]);
}
