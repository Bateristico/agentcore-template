# AgentCore + Strands Agent Template

Deploy [Strands](https://strandsagents.com) agents to **Amazon Bedrock AgentCore Runtime** with AWS CDK, run them on different Bedrock models, and see what each agent × model combination costs: per call, per benchmark, and on your AWS bill.

> **Status:** implemented and validated end to end in eu-central-1: gates G0–G10 pass. Billing reconciliation (G11) needs cost allocation tags activated in the payer account first (see [§4.5](#45-one-time-turn-on-billing-tags)).

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
| uv | 0.9+ | `uv --version` (uv installs the right Python for you) |

You also need:

- **An AWS profile** for the target account with permission to deploy CloudFormation, IAM, ECR, S3, CloudWatch, Bedrock, and AgentCore resources. You set it once in `.env` (see [§3](#3-configure)).
- **Bedrock model access** in the target region for every model you list in `config/deploy.yaml`. The preflight check (step 4.2) verifies this.

> **No Docker needed:** AgentCore only runs `linux/arm64` containers. The images are built in AWS CodeBuild on an ARM build host during `cdk deploy`, so your machine needs no Docker and no emulation. Each image build takes a few minutes and costs a few cents.

## 2. Install

```powershell
git clone <repo-url> agentcore-strands-template
cd agentcore-strands-template
uv sync                    # Python deps: agents kit, bench, UI, scripts, validation
cd infra; npm ci; cd ..    # CDK (TypeScript) deps
```

## 3. Configure

There is one file for your local environment (`.env`) and two files in `config/`.

### `.env`: which AWS profile to use

Copy the example and set `AWS_PROFILE` to the AWS CLI profile of the target account:

```powershell
Copy-Item .env.example .env     # bash: cp .env.example .env
```

| Variable | Required | Meaning |
|---|---|---|
| `AWS_PROFILE` | yes | AWS CLI profile for the target account. Used by every `uv run` command, the UI, the `aws` CLI, and `cdk`. |
| `AWS_REGION` | no | Region for the `aws` CLI. The template itself deploys to the `region` in `config/deploy.yaml`, so keep the two the same. |
| `TMPL_G11_DAYS` | no | Window in whole days for billing reconciliation (gate G11). Default `3`. |

`.env` is git-ignored, so never commit it. Load it into your shell once per terminal session, before running any command in this README:

```powershell
# PowerShell
Get-Content .env | Where-Object { $_ -match '^\s*[^#\s][^=]*=' } | ForEach-Object { $k, $v = $_ -split '=', 2; Set-Item "env:$($k.Trim())" $v.Trim() }
```

```bash
# bash / zsh
set -a; source .env; set +a
```

The commands below rely on `AWS_PROFILE` and have no `--profile` flag. To target another profile for one command, add `--profile <name>`, which overrides `AWS_PROFILE`.

### `config/deploy.yaml`: what gets deployed where

```yaml
prefix: tmpl                    # goes into every resource name; change it for parallel copies
region: eu-central-1
# account: "123456789012"       # optional: pin it, and deploys refuse any other account
removal: retain                 # retain | destroy (what happens to the results bucket on teardown)

models:                         # alias -> Bedrock system inference profile
  haiku-4-5:        { profile: eu.anthropic.claude-haiku-4-5-20251001-v1:0 }
  haiku-4-5-global: { profile: global.anthropic.claude-haiku-4-5-20251001-v1:0 }   # same model, global routing (10% cheaper)
  sonnet-4-6:       { profile: eu.anthropic.claude-sonnet-4-6 }
  nova-2-lite:      { profile: eu.amazon.nova-2-lite-v1:0 }
  nova-pro:         { profile: eu.amazon.nova-pro-v1:0 }
  # kimi-k3:        { profile: global.moonshotai.kimi-k3, billing_tag: false }   # model without application-profile support: no Cost Explorer tag

judge: sonnet-4-6               # model that scores benchmark answers

agents:                         # folder name under agents/ -> allowed models + default
  hello-agent: { models: [haiku-4-5, nova-2-lite, nova-pro], default: haiku-4-5 }
  tools-agent: { models: [haiku-4-5, haiku-4-5-global, sonnet-4-6, nova-pro], default: haiku-4-5 }
```

To see which model profiles your account offers:

```powershell
aws bedrock list-inference-profiles --region eu-central-1 --query "inferenceProfileSummaries[].inferenceProfileId" --output text
```

### `config/pricing.yaml`: what tokens cost

This file holds USD per 1M tokens for every model alias, plus AgentCore compute rates. Every alias in `deploy.yaml` must have a price, or preflight fails. To compare it with the AWS Price List API:

```powershell
uv run scripts/refresh_pricing.py --region eu-central-1           # prints a diff; changes nothing
uv run scripts/refresh_pricing.py --region eu-central-1 --write   # writes the changed prices into pricing.yaml
```

> **Prices are region-specific.** The shipped `pricing.yaml` is for **eu-central-1**: both the prices and the Price List usage types each model is looked up by (`source.usage_types`) differ by region. Before deploying to another region, re-derive the usage types for that region and run `refresh_pricing.py --region <region>`. The script exits with code 1 and lists every price it cannot find, which means a usage type does not exist in that region.

Prices are applied when results are **read**, never stored as dollars. You can change prices at any time without redeploying, and re-price old benchmark runs.

## 4. Spin it up

### 4.1 Log in

```powershell
aws sso login
aws sts get-caller-identity     # confirm the account
```

### 4.2 Preflight

This checks the account, the CDK bootstrap, model access (a few tokens per model), and pricing coverage. Nothing is deployed.

```powershell
uv run validate g0
```

If the account isn't CDK-bootstrapped yet, preflight says so. Bootstrap it once:

```powershell
cd infra
npx cdk bootstrap aws://<ACCOUNT_ID>/eu-central-1
cd ..
```

### 4.3 Deploy

```powershell
cd infra
npx cdk diff   --all     # review what will be created
npx cdk deploy --all
cd ..                                           # back to the repo root for the uv commands below
```

This creates:

| Stack | Contents |
|---|---|
| `tmpl-platform` | Results S3 bucket, judge inference profile, CloudWatch dashboard `tmpl-agent-costs` |
| `tmpl-agent-<name>` (one per agent) | AgentCore runtime `tmpl_<name>`, execution role, one tagged inference profile per allowed model |

To deploy or update a single agent:

```powershell
cd infra
npx cdk deploy tmpl-agent-tools-agent
cd ..
```

### 4.4 Verify the deployment

```powershell
uv run validate g4    # one cheap call per agent x model
```

### 4.5 One-time: turn on billing tags

These tags let Cost Explorer show real billed cost per agent and model. You only need them for the **Costs** view and billing reconciliation; everything else works without them.

1. Open the AWS Billing console, go to **Cost allocation tags**, and activate `project`, `agent`, and `model`. If the account belongs to an AWS Organization, this has to happen in the **management (payer) account**.
2. Wait about 24 hours for data to appear.

Cost Explorer only attributes tags to usage that happens **after** activation. Usage from before activation stays untagged unless the payer account requests a **cost allocation tag backfill** (Billing > Cost allocation tags > Backfill tags). To check billing reconciliation (G11), follow [§5.5](#55-full-validation).

## 5. Run it

### 5.1 Web UI (recommended)

```powershell
uv run streamlit run ui/app.py
```

To use another profile than `AWS_PROFILE`, pass it after `--`: `uv run streamlit run ui/app.py -- --profile <name>`.

This opens http://localhost:8501 with three pages:

- **Playground:** pick an agent and model, then chat. Every answer shows its tokens, latency, and cost.
- **Benchmarks:** pick an agent, test suite, models, and number of repeats. You see a cost estimate first and confirm before anything runs. A progress bar updates live as calls finish. You can also browse past runs and open their reports.
- **Costs:** live cost per agent × model from CloudWatch, shown next to the billed cost from Cost Explorer.

### 5.2 Single call from the CLI

```powershell
uv run invoke --agent tools-agent --model haiku-4-5 --prompt "What is 17% of 2340?"
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
uv run bench/run.py --agent tools-agent --suite basics --models haiku-4-5,sonnet-4-6,nova-pro --repeats 3
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
  uv run scripts/reconcile_costs.py --days 7
  ```

### 5.5 Full validation

To run all the gates in order (preflight, offline tests, local smoke, deploy, matrix, metering accuracy, judge calibration, benchmark, UI, portability, teardown):

```powershell
uv run validate all
```

Evidence is written to `validation/<timestamp>/`. Two things to know:

- `validate all` **ends by destroying the stacks** (gate G10). To validate without tearing down, run up to G9: `uv run validate all --until g9`.
- Billing reconciliation (G11) is a separate step, because Cost Explorer needs tagged usage and 24–48 hours:

  1. Activate the cost allocation tags ([§4.5](#45-one-time-turn-on-billing-tags)). Optionally ask the payer account to request a tag backfill; without it, only usage **after** activation counts.
  2. Deploy: `cd infra; npx cdk deploy --all; cd ..`
  3. Make tagged calls: `uv run validate g4` and `uv run validate g7 --yes` (or run a benchmark).
  4. Wait 24–48 hours.
  5. Within 3 days of those calls: `uv run validate g11`. G11 compares the last 3 whole completed UTC days (ending yesterday). To widen the window, set `TMPL_G11_DAYS`, for example `$env:TMPL_G11_DAYS = 5` in PowerShell.
  6. Destroy the stacks ([§6](#6-destroy-it)).

## 6. Destroy it

### 6.1 Note the results bucket name (only if you want to delete it later)

```powershell
aws cloudformation describe-stacks --stack-name tmpl-platform --region eu-central-1 --query "Stacks[0].Outputs[?OutputKey=='ResultsBucketName'].OutputValue" --output text
```

### 6.2 Destroy the stacks

```powershell
cd infra
npx cdk destroy --all
```

This removes every runtime, role, inference profile, and the dashboard that this template created. It **never touches resources without the `prefix`**, such as other AgentCore runtimes already in the account.

### 6.3 Results bucket

- With `removal: retain` (the default), the bucket and your benchmark history are kept. To delete them too:

  ```powershell
  aws s3 rb s3://<results-bucket> --force
  ```

- With `removal: destroy`, the bucket is emptied and deleted along with the stacks.

### 6.4 Confirm nothing is left behind

```powershell
aws resourcegroupstaggingapi get-resources --tag-filters Key=project,Values=tmpl --region eu-central-1 --query "ResourceTagMappingList[].ResourceARN"
```

The list should be empty, or contain only the retained bucket.

## 7. Deploy to another AWS account

1. Create or choose an AWS CLI profile for that account, set it as `AWS_PROFILE` in `.env` (and `AWS_REGION` to the new region), reload `.env` ([§3](#env-which-aws-profile-to-use)), and log in.
2. In `config/deploy.yaml`, set `region`. Optionally pin `account`, and change `prefix` if another copy already runs there.
3. Check that every model alias exists in that region:

   ```powershell
   aws bedrock list-inference-profiles --region <region>
   ```

4. If the region is not eu-central-1, re-derive `config/pricing.yaml` for it: prices and Price List usage types differ by region (see [§3](#configpricingyaml-what-tokens-cost)). `uv run scripts/refresh_pricing.py --region <region>` must exit with code 0 before you trust the cost numbers.
5. Run steps [4.2](#42-preflight) to [4.5](#45-one-time-turn-on-billing-tags).

Nothing in the code is tied to a specific account. Validation gate G9 proves this.

## 8. Add an agent or a model

**New agent**

1. Copy `agents/hello-agent/` to `agents/<new-agent>/`.
2. Edit `prompt.md` (the system prompt), add tools in `tools.py`, and add any extra dependencies to `requirements.txt`. `agent.py` only has to export:

   ```python
   from pathlib import Path

   from strands import Agent

   # Read relative to this file, not the working directory (it is /app in the container).
   PROMPT = (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")

   def build_agent(model) -> Agent:
       return Agent(model=model, system_prompt=PROMPT, tools=[...])
   ```

3. Add the agent to `config/deploy.yaml` under `agents:` with its allowed models and default.
4. Optionally add a test suite in `bench/suites/<new-agent>/`.
5. Deploy it, and redeploy the platform stack so the `tmpl-agent-costs` dashboard includes the new agent:

   ```powershell
   cd infra
   npx cdk deploy tmpl-agent-<new-agent> tmpl-platform
   cd ..
   ```

**New model**

1. Add an alias under `models:` in `deploy.yaml` and a price in `pricing.yaml`.
2. Add the alias to the agents that should use it.
3. Redeploy those agent stacks. This creates the new tagged inference profile and updates the allow-list. Also redeploy `tmpl-platform` so the dashboard shows the new model (or simply run `npx cdk deploy --all` from `infra/`).

**Agent kit dependencies**

The image installs the agent kit (`agents/_kit`) with its third-party dependencies pinned to `uv.lock`, through `agents/_kit/requirements.lock`. After changing the kit's dependencies or running `uv lock`, regenerate it from the repo root and commit it (gate G1 fails while it is stale):

```powershell
uv export --frozen --package agentkit --no-hashes --no-emit-workspace --no-dev --no-header -o agents/_kit/requirements.lock
```

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `ExpiredToken` / `The SSO session has expired` | `aws sso login` |
| `cdk deploy` fails while building an agent image | Read the CodeBuild log: CloudWatch Logs group `/aws/codebuild/<project>` named in the error, or the CodeBuild console. Usually a dependency in `agents/<name>/requirements.txt` without an arm64 wheel |
| Preflight: `AccessDeniedException` for a model | Enable access to that model in the Bedrock console (**Model access**) for this region, or remove the alias from `deploy.yaml` |
| Preflight check `every model has a price` fails (it lists the aliases), or `cdk synth`/`deploy` stops with `pricing.yaml has no price for: <aliases>` | Add those aliases to `config/pricing.yaml` |
| Invocation error `model 'x' not allowed` | Add the alias to that agent's `models:` list and redeploy the agent stack |
| A `nova-pro` answer contains `<thinking>...</thinking>` text | That is the model's own output (Nova Pro sometimes writes its reasoning into the answer), not a template bug. Ask for the final answer only in `prompt.md`, or compare models in a benchmark |
| `ThrottlingException` during benchmarks | Lower concurrency with `--concurrency 2`, or request a Bedrock quota increase |
| Costs page says "cost allocation tags not active" | Do step [4.5](#45-one-time-turn-on-billing-tags) and wait about 24 hours |
| A model shows "no billing tag" | That model can't use an application inference profile. Its cost is token-based only, which is expected. Check a new model with `uv run scripts/probe_models.py --profile <p> --models <profile-id>` before adding it. |
| `cdk deploy` fails with an invalid runtime name | Agent folder and prefix names may only use letters, digits, and hyphens. Hyphens become underscores in runtime names, which have a maximum of 48 characters |

---

**Cost note:** a full validation run is expected to cost under $5. The preflight and benchmark commands always print an estimate before making paid calls.
