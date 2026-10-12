import { CfnOutput, RemovalPolicy, Stack, StackProps, Tags } from 'aws-cdk-lib';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as cw from 'aws-cdk-lib/aws-cloudwatch';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { DeployConfig, dashboardName, judgeProfileName } from './config';
import { buildDashboardWidgets } from './dashboard';
import { addPlatformNagSuppressions } from './nag';
import { Pricing } from './pricing';

export interface PlatformStackProps extends StackProps {
  readonly config: DeployConfig;
  readonly pricing: Pricing;
}

/** Shared per account: results bucket, judge profile, cost dashboard (spec §4). */
export class PlatformStack extends Stack {
  public readonly bucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: PlatformStackProps) {
    super(scope, id, props);
    const { config, pricing } = props;
    const destroy = config.removal === 'destroy';
    Tags.of(this).add('project', config.prefix);

    this.bucket = new s3.Bucket(this, 'Results', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: destroy ? RemovalPolicy.DESTROY : RemovalPolicy.RETAIN,
      autoDeleteObjects: destroy,
    });

    const judge = config.models[config.judge];
    const judgeSystemArn = this.formatArn({ service: 'bedrock', resource: 'inference-profile', resourceName: judge.profile });
    let judgeModelId = judge.profile;
    if (judge.billing_tag) {
      const profile = new bedrock.CfnApplicationInferenceProfile(this, 'JudgeProfile', {
        inferenceProfileName: judgeProfileName(config.prefix),
        description: `Benchmark judge ${config.judge}`,
        modelSource: { copyFrom: judgeSystemArn },
        tags: [{ key: 'role', value: 'judge' }, { key: 'model', value: config.judge }],
      });
      judgeModelId = profile.attrInferenceProfileArn;
    }

    new cw.Dashboard(this, 'CostDashboard', {
      dashboardName: dashboardName(config.prefix),
      widgets: buildDashboardWidgets(config, pricing),
    });

    new CfnOutput(this, 'ResultsBucketName', { value: this.bucket.bucketName });
    new CfnOutput(this, 'JudgeProfileArn', { value: judgeModelId });
    new CfnOutput(this, 'JudgeModel', { value: config.judge });
    addPlatformNagSuppressions(this);
  }
}
