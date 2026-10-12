#!/usr/bin/env node
import * as path from 'path';
import { App, Aspects } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { buildApp } from '../lib/build-app';
import { loadConfig } from '../lib/config';
import { loadPricing } from '../lib/pricing';

const repoRoot = path.resolve(__dirname, '..', '..');
const app = new App();
const ctx = (key: string): string | undefined => app.node.tryGetContext(key);

const config = loadConfig(path.join(repoRoot, 'config', 'deploy.yaml'), {
  prefix: ctx('prefix'),
  region: ctx('region'),
  account: ctx('account'),
  removal: ctx('removal'),
});
const pricing = loadPricing(path.join(repoRoot, 'config', 'pricing.yaml'));

buildApp(app, config, pricing, path.join(repoRoot, 'agents'));
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
