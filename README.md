# Autonomous Short-Form Audio Generation Engine

An AWS-based multi-agent application that turns natural-language prompts into short-form audio content. The system is designed for creator workflows such as podcast-style clips, YouTube Shorts audio, educational explainers, and persona-based dialogue generation.

The project uses Amazon Bedrock AgentCore, Strands agents, Lambda, API Gateway, DynamoDB, S3, Amazon Polly, and an AgentCore MCP Gateway to coordinate research, script generation, and MP3 delivery.

## Demo
```text
demo.webm
```

After adding the file, viewers can open `demo.mp4` from the repository root to see the application flow.

## What The System Does

- Accepts natural-language requests from a browser frontend.
- Routes requests through an asynchronous backend job system.
- Uses a supervisor agent to decide whether the user wants normal chat, blog content, or audio content.
- Uses specialist agents for blog writing and speaker dialogue generation.
- Uses an MCP Gateway for web search and text-to-audio tools.
- Converts generated scripts into MP3 files with Amazon Polly.
- Stores generated audio in S3 and returns a temporary presigned URL.

## Architecture

```text
Frontend
  -> API Gateway
  -> proxy Lambda
  -> DynamoDB job record
  -> worker Lambda
  -> AgentCore Runtime: supervisor
       -> AgentCore Runtime: blogWriter
       -> AgentCore Runtime: SpeakerA
       -> AgentCore Runtime: SpeakerB
       -> AgentCore MCP Gateway
            -> web-search-tool Lambda
            -> polly-make-mp3 Lambda
  -> DynamoDB result
  -> Frontend status polling
```

The backend is asynchronous. The frontend creates a job with `POST /jobs`, receives a `job_id`, then polls `GET /status?job_id=...` until the job is completed or failed.

## Main Components

```text
agents/
  supervisor/      Main orchestration agent
  blogWriter/      Blog-writing specialist agent
  SpeakerA/        First podcast/dialogue speaker agent
  SpeakerB/        Second podcast/dialogue speaker agent

lambdas/
  src/proxy.py             Creates jobs and invokes worker asynchronously
  src/worker.py            Calls the supervisor AgentCore runtime
  src/job_Status.py        Reads job status from DynamoDB
  src/web_search.py        Web search tool target
  src/polly_make_mp3.py    Polly/S3 MP3 generation tool target

frontend/
  index.html
  styles.css
  app.js
```

## AWS Services Used

- Amazon Bedrock AgentCore Runtime
- Amazon Bedrock model access
- Amazon Bedrock AgentCore Gateway
- Amazon Cognito JWT authentication for the gateway
- AWS Lambda
- Amazon API Gateway
- Amazon DynamoDB
- Amazon Polly
- Amazon S3
- Amazon CloudWatch
- AWS X-Ray
- Langfuse with OpenTelemetry traces

## Setup Overview

This project is not a single local-only app. It requires AWS resources to be created and connected correctly.

At a high level, you need to:

1. Create the DynamoDB jobs table.
2. Create the S3 bucket for generated audio.
3. Deploy the Lambda functions.
4. Create the AgentCore MCP Gateway and attach the Lambda tool targets.
5. Configure Cognito JWT auth for the gateway.
6. Deploy the four AgentCore agents.
7. Configure the worker Lambda with the supervisor runtime ARN.
8. Create API Gateway routes for the frontend.
9. Configure CORS for browser access.
10. Open the frontend and enter the API Gateway base URL.

More detailed deployment notes are kept in the project documentation files.

## Agent Deployment

Each agent is an AgentCore project. Deploy from the agent folder:

```bash
cd agents/supervisor
agentcore validate
agentcore deploy
```

Repeat for:

```text
agents/blogWriter
agents/SpeakerA
agents/SpeakerB
```

The supervisor agent must know the runtime ARNs of the worker agents through environment variables.

## Lambda Deployment

The Lambda source files are under:

```text
lambdas/src/
```

The web search Lambda requires packaged Python dependencies. Build Lambda packages for the same Python version and CPU architecture used by the deployed Lambda function.

Important Lambda environment variables include:

```text
JOBS_TABLE_NAME
WORKER_LAMBDA_NAME
SUPERVISOR_AGENT_RUNTIME_ARN
SUPERVISOR_AGENT_RUNTIME_QUALIFIER
PODCAST_AUDIO_BUCKET
```

## Frontend Usage

Open the frontend from:

```text
frontend/index.html
```

In the settings panel, set:

```text
API base URL: https://<api-id>.execute-api.<region>.amazonaws.com/<stage>
Create job path: /jobs
Status path: /status
```

The frontend sends messages as natural language. The supervisor decides whether to answer normally, write a blog, or generate audio.

## Operational Notes

To temporarily stop agent execution and avoid token usage, pause the worker Lambda:

```bash
aws lambda put-function-concurrency \
  --region eu-north-1 \
  --function-name worker \
  --reserved-concurrent-executions 0
```

To enable it again:

```bash
aws lambda delete-function-concurrency \
  --region eu-north-1 \
  --function-name worker
```

If the worker is paused, new frontend jobs can remain stuck in `PROCESSING`. Re-enable the worker before testing through the frontend.

## Observability

The system uses multiple observability layers:

- CloudWatch Logs for Lambda and AgentCore runtime logs.
- CloudWatch Metrics for API Gateway, Lambda, Bedrock, and AgentCore metrics.
- X-Ray for AWS service trace maps.
- Langfuse for LLM and agent traces through OpenTelemetry.

Langfuse does not automatically receive all AWS metrics or Cost Explorer billing data. It is mainly used for LLM trace visibility.

## Security Notes

Before sharing this project, remove or rotate any secrets from configuration files, including:

- Langfuse keys or OTEL authorization headers
- Cognito client secrets
- temporary access tokens
- AWS account-specific sensitive values

Generated deployment folders such as `agentcore/.cache`, `agentcore/cdk/cdk.out`, and CLI logs can be removed before zipping the project.
