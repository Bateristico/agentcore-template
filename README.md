# AgentCore + Strands Agent Template

Deploy [Strands](https://strandsagents.com) agents to **Amazon Bedrock AgentCore Runtime** with AWS CDK, run them on different Bedrock models, and see what each agent × model combination costs: per call, per benchmark, and on your AWS bill.

> **Status:** design approved, implementation pending. This guide describes the target interface from the [design spec](docs/superpowers/specs/2026-10-09-agentcore-strands-template-design.md). Commands will work once the implementation lands.

**What you get**

- One AgentCore runtime per agent; the model is chosen **per request** from an allow-list.
- A tagged Bedrock application inference profile per agent × model, so costs show up in Cost Explorer by `agent` and `model`.
- A benchmark runner with an LLM judge that produces an HTML report: cost, latency, and quality per model, plus projected cost per 1,000 conversations.
- A local web UI (Streamlit) with three pages: **Playground**, **Benchmarks**, and **Costs**.
- Validation gates (`uv run validate all`) that prove a deployment works end to end.

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Install](#2-install)
3. [Configure](#3-configure)
4. [Spin it up](#4-spin-it-up)
5. [Run it](#5-run-it)
6. [Destroy it](#6-destroy-it)
7. [Deploy to another AWS account](#7-deploy-to-another-aws-account)
8. [Add an agent or a model](#8-add-an-agent-or-a-model)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Prerequisites

| Tool | Version | Check |
|---|---|---|
| AWS CLI | v2 | `aws --version` |
| Node.js | 20+ | `node --version` |
| AWS CDK CLI | v2 | `npx cdk --version` |
| Docker Desktop | with `buildx` | `docker buildx ls` (the list must include `linux/arm64`) |
| uv | 0.9+ | `uv --version` (uv installs the right Python for you) |

You also need:

- **An AWS profile** for the target account with permission to deploy CloudFormation, IAM, ECR, S3, CloudWatch, Bedrock, and AgentCore resources. For the validation account, use `<profile>`.
- **Bedrock model access** in the target region for every model you list in `config/deploy.yaml`. The preflight check (step 4.2) verifies this.

> **Windows note:** AgentCore only runs `linux/arm64` containers. Docker Desktop builds them through emulation, so the first build takes a few minutes. If `linux/arm64` is missing from `docker buildx ls`, see [Troubleshooting](#9-troubleshooting).

## 2. Install

```powershell
git clone <repo-url> agentcore-strands-template
cd agentcore-strands-template
uv sync                    # Python deps: agents kit, bench, UI, scripts, validation
cd infra; npm ci; cd ..    # CDK (TypeScript) deps
```

## 3. Configure

There are two files, both in `config/`.

### `config/deploy.yaml`: what gets deployed where

```yaml
prefix: tmpl                    # goes into every resource name; change it for parallel copies
region: eu-central-1
# account: "123456789012"       # optional: pin it, and deploys refuse any other account
removal: retain                 # retain | destroy (what happens to the results bucket on teardown)

models:                         # alias -> Bedrock system inference profile
  haiku-4-5:  { profile: eu.anthropic.claude-haiku-4-5-20251001-v1:0 }
  sonnet-4-5: { profile: eu.anthropic.claude-sonnet-4-5-20250929-v1:0 }
  nova-pro:   { profile: eu.amazon.nova-pro-v1:0 }
  gpt-5-5:    { profile: global.openai.gpt-5.5, billing_tag: false }   # no Cost Explorer tag

judge: sonnet-4-5               # model that scores benchmark answers

agents:                         # folder name under agents/ -> allowed models + default
  hello-agent: { models: [haiku-4-5, nova-2-lite, nova-pro], default: haiku-4-5 }
  tools-agent: { models: [haiku-4-5, haiku-4-5-global, sonnet-4-6, nova-pro], default: haiku-4-5 }
```

To see which model profiles your account offers:

```powershell
aws bedrock list-inference-profiles --profile <profile> --region eu-central-1 --query "inferenceProfileSummaries[].inferenceProfileId" --output text
```

### `config/pricing.yaml`: what tokens cost

This file holds USD per 1M tokens for every model alias, plus AgentCore compute rates. Every alias in `deploy.yaml` must have a price, or preflight fails. To propose updates from the AWS Price List API:

```powershell
uv run scripts/refresh_pricing.py --region eu-central-1   # prints a diff; review it, then accept
```

Prices are applied when results are **read**, never stored as dollars. You can change prices at any time without redeploying, and re-price old benchmark runs.

## 4. Spin it up

### 4.1 Log in

```powershell
aws sso login --profile <profile>
aws sts get-caller-identity --profile <profile>     # confirm the account
```

### 4.2 Preflight

This checks the account, the CDK bootstrap, Docker arm64 support, model access (about 1 token per model), and pricing coverage. Nothing is deployed.

```powershell
uv run validate g0 --profile <profile>
```

If the account isn't CDK-bootstrapped yet, preflight says so. Bootstrap it once:

```powershell
cd infra
npx cdk bootstrap aws://<ACCOUNT_ID>/eu-central-1 --profile <profile>
```

### 4.3 Deploy

```powershell
cd infra
npx cdk diff   --all --profile <profile>     # review what will be created
npx cdk deploy --all --profile <profile>
```

This creates:

| Stack | Contents |
|---|---|
| `tmpl-platform` | Results S3 bucket, judge inference profile, CloudWatch dashboard `tmpl-agent-costs` |
| `tmpl-agent-<name>` (one per agent) | AgentCore runtime `tmpl_<name>`, execution role, one tagged inference profile per allowed model |

To deploy or update a single agent:

```powershell
npx cdk deploy tmpl-agent-tools-agent --profile <profile>
```

### 4.4 Verify the deployment

```powershell
uv run validate g4 --profile <profile>    # one cheap call per agent x model
```

### 4.5 One-time: turn on billing tags

These tags let Cost Explorer show real billed cost per agent and model. You only need them for the **Costs** view and billing reconciliation; everything else works without them.

1. Open the AWS Billing console, go to **Cost allocation tags**, and activate `project`, `agent`, and `model`. If the account belongs to an AWS Organization, this has to happen in the **management (payer) account**.
2. Wait about 24 hours for data to appear.

## 5. Run it

### 5.1 Web UI (recommended)

```powershell
uv run streamlit run ui/app.py -- --profile <profile>
```

This opens http://localhost:8501 with three pages:

- **Playground:** pick an agent and model, then chat. Every answer shows its tokens, latency, and cost.
- **Benchmarks:** pick an agent, test suite, models, and number of repeats. You see a cost estimate first and confirm before anything runs. Results update live as calls finish. You can also browse past runs and open their reports.
- **Costs:** live cost per agent × model from CloudWatch, shown next to the billed cost from Cost Explorer.

### 5.2 Single call from the CLI

```powershell
uv run invoke --agent tools-agent --model haiku-4-5 --prompt "What is 17% of 2340?" --profile <profile>
```

This prints the answer plus a usage block (tokens, latency, cycles, tool calls) and the cost. If you leave out `--model`, the agent's default model is used.

### 5.3 Benchmark from the CLI

Test suites live in `bench/suites/<agent>/<suite>.yaml`:

```yaml
suite: basics
cases:
  - id: c01
    prompt: "What is 17% of 2,340 and is that above the 400 threshold?"
    expected: "397.8, which is below 400"
    rubric: "Correct number; correct comparison; uses calculator tool"
```

Run a suite:

```powershell
uv run bench/run.py --agent tools-agent --suite basics --models haiku-4-5,sonnet-4-6,nova-pro --repeats 3 --profile <profile>
```

- The runner shows a **cost estimate** and asks you to confirm. Add `--yes` to skip the prompt in scripts.
- Results go to `bench/out/<run_id>/` and `s3://<results-bucket>/runs/<run_id>/`:
  - `report.html`: open it in a browser. It shows per-model cost, cost per 1,000 conversations, latency, judge score, pass rate, and a cost-vs-quality chart.
  - `results.jsonl`: one row per call.
  - `run.json`: the config and price snapshot for reproducibility.
- To rebuild an old report with today's prices:

  ```powershell
  uv run bench/run.py report <run_id> --reprice
  ```

### 5.4 Costs

- **Live estimate:** CloudWatch dashboard `tmpl-agent-costs` in the console, or the **Costs** page in the UI.
- **Billed cost vs. estimate** (needs step 4.5):

  ```powershell
  uv run scripts/reconcile_costs.py --days 7 --profile <profile>
  ```

### 5.5 Full validation

To run all the gates in order (preflight, offline tests, local smoke, deploy, matrix, metering accuracy, judge calibration, benchmark, UI, portability, teardown):

```powershell
uv run validate all --profile <profile>
```

Evidence is written to `validation/<timestamp>/`. Two things to know:

- `validate all` **ends by destroying the stacks** (gate G10). To validate without tearing down, run up to G9: `uv run validate all --until g9`.
- Billing reconciliation (G11) only works 24–48 hours later: `uv run validate g11 --profile <profile>`.

## 6. Destroy it

### 6.1 Note the results bucket name (only if you want to delete it later)

```powershell
aws cloudformation describe-stacks --stack-name tmpl-platform --profile <profile> --region eu-central-1 --query "Stacks[0].Outputs[?OutputKey=='ResultsBucketName'].OutputValue" --output text
```

### 6.2 Destroy the stacks

```powershell
cd infra
npx cdk destroy --all --profile <profile>
```

This removes every runtime, role, inference profile, and the dashboard that this template created. It **never touches resources without the `prefix`**, such as other AgentCore runtimes already in the account.

### 6.3 Results bucket

- With `removal: retain` (the default), the bucket and your benchmark history are kept. To delete them too:

  ```powershell
  aws s3 rb s3://<results-bucket> --force --profile <profile>
  ```

- With `removal: destroy`, the bucket is emptied and deleted along with the stacks.

### 6.4 Confirm nothing is left behind

```powershell
aws resourcegroupstaggingapi get-resources --tag-filters Key=project,Values=tmpl --profile <profile> --region eu-central-1 --query "ResourceTagMappingList[].ResourceARN"
```

The list should be empty, or contain only the retained bucket.

## 7. Deploy to another AWS account

1. Create or choose an AWS CLI profile for that account and log in.
2. In `config/deploy.yaml`, set `region`. Optionally pin `account`, and change `prefix` if another copy already runs there.
3. Check that every model alias exists in that region:

   ```powershell
   aws bedrock list-inference-profiles --profile <profile> --region <region>
   ```

4. Run steps [4.2](#42-preflight) to [4.5](#45-one-time-turn-on-billing-tags) with `--profile <profile>`.

Nothing in the code is tied to a specific account. Validation gate G9 proves this.

## 8. Add an agent or a model

**New agent**

1. Copy `agents/hello-agent/` to `agents/<new-agent>/`.
2. Edit `prompt.md` (the system prompt), add tools in `tools.py`, and add any extra dependencies to `requirements.txt`. `agent.py` only has to export:

   ```python
   def build_agent(model) -> Agent:
       return Agent(model=model, system_prompt=open("prompt.md").read(), tools=[...])
   ```

3. Add the agent to `config/deploy.yaml` under `agents:` with its allowed models and default.
4. Optionally add a test suite in `bench/suites/<new-agent>/`.
5. Deploy it:

   ```powershell
   npx cdk deploy tmpl-agent-<new-agent> --profile <profile>
   ```

**New model**

1. Add an alias under `models:` in `deploy.yaml` and a price in `pricing.yaml`.
2. Add the alias to the agents that should use it.
3. Redeploy those agent stacks. This creates the new tagged inference profile and updates the allow-list.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `ExpiredToken` / `The SSO session has expired` | `aws sso login --profile <profile>` |
| Docker build fails with `exec format error`, or there's no `linux/arm64` builder | `docker run --privileged --rm tonistiigi/binfmt --install arm64`, then `docker buildx create --use` |
| Preflight: `AccessDeniedException` for a model | Enable access to that model in the Bedrock console (**Model access**) for this region, or remove the alias from `deploy.yaml` |
| Preflight: `alias has no price` | Add the alias to `config/pricing.yaml` |
| Invocation error `model 'x' not allowed` | Add the alias to that agent's `models:` list and redeploy the agent stack |
| `ThrottlingException` during benchmarks | Lower concurrency with `--concurrency 2`, or request a Bedrock quota increase |
| Costs page says "cost allocation tags not active" | Do step [4.5](#45-one-time-turn-on-billing-tags) and wait about 24 hours |
| A model shows "no billing tag" | That model can't use an application inference profile. Its cost is token-based only, which is expected. Check a new model with `uv run scripts/probe_models.py --profile <p> --models <profile-id>` before adding it. |
| `cdk deploy` fails with an invalid runtime name | Agent folder and prefix names may only use letters, digits, and hyphens. Hyphens become underscores in runtime names, which have a maximum of 48 characters |

---

**Cost note:** a full validation run in `<profile>` is expected to cost under $5. The preflight and benchmark commands always print an estimate before making paid calls.
