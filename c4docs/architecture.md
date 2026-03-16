# Repository Architecture

This diagram is a C4-style container view of the system deployed by this repository. It is based on the main SAM stack in `template.yaml`, the unified processing pattern in `patterns/unified/template.yaml`, and the AppSync nested stack in `nested/appsync/template.yaml`.

```mermaid
flowchart LR
    user[User]
    ext[External MCP Client]
    ops[Operations Team]

    subgraph edge[Access and Experience]
        cf[CloudFront Web UI]
        cognito[Cognito User Pool and Identity Pool]
        appsync[AppSync GraphQL API]
    end

    subgraph ingest[Ingestion and Control Plane]
        input[(Input S3 Bucket)]
        discovery[(Discovery S3 Bucket)]
        qsender[Queue Sender Lambda]
        docq[SQS Document Queue]
        qproc[Queue Processor Lambda]
        tracker[Workflow Tracker Lambda]
        lookup[Lookup and Status Lambdas]
        tracking[(DynamoDB Tracking Table)]
        concurrency[(DynamoDB Concurrency Table)]
        config[(Configuration S3 Bucket and DynamoDB Config Table)]
    end

    subgraph process[Unified Processing Pattern]
        sfn[Step Functions Document Processing State Machine]
        bda[BDA Branch Lambdas]
        pipe[Pipeline Branch Lambdas
OCR, Classification, Extraction, Assessment]
        shared[Shared Tail Lambdas
Rule Validation, Summarization, Evaluation]
        work[(Working S3 Bucket)]
        output[(Output S3 Bucket)]
    end

    subgraph optional[Optional Analytics and Extension Paths]
        glue[Glue Catalog and Crawler]
        athena[Athena Reporting Queries]
        baseline[(Evaluation Baseline S3 Bucket)]
        reporting[(Reporting S3 Bucket)]
        kb[Bedrock Knowledge Base]
        agentcore[Bedrock AgentCore Gateway and Analytics Lambda]
        post[Post-Processing Lambda Hook]
        monitor[CloudWatch Dashboards, Logs, Alarms]
    end

    user --> cf
    user --> input
    user --> cognito
    ext --> agentcore
    ops --> monitor

    cf --> cognito
    cf --> appsync
    cognito --> appsync
    appsync --> lookup
    appsync --> config
    appsync --> input
    appsync --> output
    appsync --> docq

    input --> qsender
    discovery --> qsender
    qsender --> docq
    docq --> qproc
    qproc --> concurrency
    qproc --> tracking
    qproc --> sfn
    tracker --> tracking
    sfn --> tracker

    sfn --> bda
    sfn --> pipe
    bda --> work
    pipe --> work
    bda --> shared
    pipe --> shared
    shared --> output
    shared --> baseline
    shared --> reporting
    shared --> kb
    shared --> post

    output --> glue
    reporting --> glue
    baseline --> glue
    glue --> athena

    appsync --> agentcore

    qsender --> monitor
    qproc --> monitor
    sfn --> monitor
    shared --> monitor
    appsync --> monitor
```

## Component Notes

- Web access is provided through CloudFront, with Cognito handling authentication and AppSync acting as the main application API.
- Documents typically enter through the Input S3 bucket, then move through `QueueSender`, `DocumentQueue`, and `QueueProcessor` before a Step Functions execution is started.
- The unified pattern stack supports two runtime paths: a BDA branch and a pipeline branch. Both converge on shared downstream steps such as rule validation, summarization, and evaluation.
- Output artifacts are stored in S3 and can feed optional evaluation reporting, Athena or Glue analytics, Bedrock Knowledge Base indexing, and external MCP access through AgentCore.
- DynamoDB tables back execution tracking, workflow concurrency control, agent data, and configuration state.
