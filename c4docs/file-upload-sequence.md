# File Upload Sequence

This diagram shows the end-to-end upload flow implemented by the AppSync upload resolver in `nested/appsync/src/lambda/upload_resolver/index.py`, the input bucket eventing in `template.yaml`, and the ingestion Lambdas in `src/lambda/queue_sender` and `src/lambda/queue_processor`.

```mermaid
sequenceDiagram
    actor User as User
    participant UI as UploadDocumentPanel\nsrc/ui/src/components/upload-document/UploadDocumentPanel.tsx
    participant AppSync as AppSync GraphQL API\ntemplate.yaml + nested/appsync/template.yaml
    participant Upload as UploadResolver Lambda\nnested/appsync/src/lambda/upload_resolver/index.py
    participant S3 as Input S3 Bucket\ntemplate.yaml
    participant EB as EventBridge\ntemplate.yaml
    participant Sender as QueueSender Lambda\nsrc/lambda/queue_sender/index.py
    participant DDB as Tracking and Config DynamoDB\ntemplate.yaml + src/lambda/queue_sender/index.py + src/lambda/queue_processor/index.py
    participant SQS as DocumentQueue\ntemplate.yaml
    participant Processor as QueueProcessor Lambda\nsrc/lambda/queue_processor/index.py
    participant Work as Working S3 Bucket\ntemplate.yaml + src/lambda/queue_processor/index.py
    participant SFN as Step Functions State Machine\ntemplate.yaml + patterns/unified/template.yaml + src/lambda/queue_processor/index.py

    User->>UI: Choose file to upload
    UI->>AppSync: Mutation uploadDocument(fileName, contentType, prefix, version)
    AppSync->>Upload: Invoke Lambda data source
    Upload->>S3: generate_presigned_post(bucket, key, metadata)
    S3-->>Upload: Presigned POST form fields and URL
    Upload-->>AppSync: presignedUrl, objectKey, usePostMethod
    AppSync-->>UI: Upload instructions

    UI->>S3: HTTP POST file to InputBucket using presigned form
    S3-->>UI: Upload success

    S3->>EB: Object-created event
    EB->>Sender: Invoke QueueSender
    Sender->>S3: Read object metadata
    Sender->>DDB: Resolve active config version if needed
    Sender->>DDB: Create queued document record
    Sender->>SQS: Send serialized document message

    SQS->>Processor: Deliver queued document
    Processor->>DDB: Check and increment concurrency
    Processor->>Work: Serialize compressed document payload
    Processor->>DDB: Read merged configuration
    Processor->>SFN: Start document-processing execution
    Processor->>DDB: Update document status to RUNNING
    SFN-->>Processor: Execution ARN recorded
```

## Notes

- The first phase is synchronous: the UI asks AppSync for a presigned upload target.
- The second phase starts only after the browser uploads the file into the input bucket.
- `UploadResolver` does not process the document contents. It only returns a signed upload target.
- Actual ingestion starts from the S3 object-created event, then moves through `QueueSender`, `DocumentQueue`, and `QueueProcessor`.
- `QueueProcessor` compresses the document payload into the working bucket before starting the Step Functions workflow.
- S3 -> EventBridge rule (declared as CloudWatchEvent in SAM) -> QueueSender Lambda
- Source files shown in the diagram:
  `src/ui/src/components/upload-document/UploadDocumentPanel.tsx`,
  `template.yaml`,
  `nested/appsync/template.yaml`,
  `nested/appsync/src/lambda/upload_resolver/index.py`,
  `patterns/unified/template.yaml`,
  `src/lambda/queue_sender/index.py`, and
  `src/lambda/queue_processor/index.py`.

### Detail for "S3->>EB: Object-created event"
- The input bucket has `EventBridgeEnabled: true`, so every successful object write emits an EventBridge event from source `aws.s3` with detail type `Object Created`.
- The `QueueSender` Lambda is subscribed through a SAM `CloudWatchEvent` rule filtered to the configured `InputBucket`.
- `QueueSender` does not receive the file bytes. It receives the EventBridge envelope, extracts `detail.bucket.name`, `detail.object.key`, and `time`, then reconstructs document state from AWS APIs.

```mermaid
sequenceDiagram
    participant S3 as Input S3 Bucket\ntemplate.yaml
    participant EB as EventBridge\ntemplate.yaml
    participant Sender as QueueSender Lambda\nsrc/lambda/queue_sender/index.py
    participant Meta as Document.from_s3_event\nlib/idp_common_pkg/idp_common/models.py
    participant DDB as Configuration / Document Store\ntemplate.yaml + src/lambda/queue_sender/index.py + src/lambda/queue_processor/index.py
    participant SQS as DocumentQueue\ntemplate.yaml
    participant Processor as QueueProcessor Lambda\nsrc/lambda/queue_processor/index.py
    participant Work as Working S3 Bucket\ntemplate.yaml + src/lambda/queue_processor/index.py
    participant SFN as Step Functions\ntemplate.yaml + patterns/unified/template.yaml + src/lambda/queue_processor/index.py

    S3->>EB: Emit Object Created event\nsource=aws.s3, bucket, object key, time
    EB->>Sender: Invoke on matching InputBucket event

    Sender->>Meta: Document.from_s3_event(event, output_bucket)
    Meta->>S3: head_object(bucket, key)
    S3-->>Meta: Object metadata
    Meta-->>Sender: Document(status=QUEUED,\ninput_bucket, input_key, initial_event_time,\nconfig_version from metadata if present)

    alt No config-version in S3 metadata
        Sender->>DDB: Scan configuration records where IsActive=true
        DDB-->>Sender: Active Config#<version>
        Sender->>Sender: Set document.config_version
    end

    Sender->>Sender: Set queued_time and retention TTL
    Sender->>DDB: create_document(document, expires_after)
    DDB-->>Sender: Persist queued document record
    Sender->>SQS: send_message(document.to_json(), attributes)
    SQS-->>Sender: MessageId

    SQS->>Processor: Deliver queued document JSON
    Processor->>DDB: get_document(input_key) and increment concurrency counter
    Processor->>Work: serialize_document(..., \"workflow_start\", ...)
    Processor->>DDB: get_merged_configuration(config_version)
    Processor->>SFN: start_execution({ document })
    Processor->>DDB: update_document(status=RUNNING,\nstart_time, workflow_execution_arn)
```

- `config-version` is expected in S3 object metadata when the upload path provides a selected configuration version.
- If that metadata is absent, `QueueSender` falls back to the active configuration version in DynamoDB before the document is queued.
- The queue message body is the serialized `Document`; SQS message attributes add `EventType=DocumentQueued` and `ObjectKey=<input key>`.
- `QueueProcessor` starts the workflow only after verifying the document was not already aborted and successfully incrementing the concurrency counter.
- Before Step Functions starts, the processor compresses the document payload into the working bucket when `WORKING_BUCKET` is configured.

#### Detail for "Deliver queued document JSON"
- Producer Lambda: `src/lambda/queue_sender/index.py`
  - `handler(...)` reads the EventBridge S3 event, extracts `detail.object.key`, and builds a `Document` via `Document.from_s3_event(...)`.
  - The sender sets `document.status = QUEUED` and `document.queued_time = <utc iso timestamp>`.
  - If `document.config_version` was not found in S3 metadata, the sender scans the configuration table for the active `Config#<version>` record and fills `document.config_version`.
  - The sender persists the queued record with `document_service.create_document(document, expires_after=...)`.
  - The actual SQS body is produced by `document.to_json()` and sent with message attributes:
    - `EventType=DocumentQueued`
    - `ObjectKey=<input_key>`

- Shared model serialization: `lib/idp_common_pkg/idp_common/models.py`
  - `Document.to_dict()` defines the JSON fields that go into SQS, including:
    - `id`, `input_bucket`, `input_key`, `output_bucket`
    - `status`, `initial_event_time`, `queued_time`
    - `start_time`, `completion_time`, `workflow_execution_arn`
    - `num_pages`, `pages`, `sections`
    - `trace_id`, `config_version`, `errors`, `metering`
  - `Document.to_json()` is just `json.dumps(self.to_dict(), default=str)`.
  - At queue-send time, the document is still a lightweight queued record, so `pages` and `sections` are usually empty.

- Queue delivery target: `src/lambda/queue_processor/index.py`
  - `template.yaml` configures `QueueProcessor` as an SQS-triggered Lambda on `DocumentQueue` with:
    - `BatchSize: 50`
    - `MaximumBatchingWindowInSeconds: 1`
    - `FunctionResponseTypes: ReportBatchItemFailures`
  - When SQS invokes the Lambda, each message arrives in `event["Records"]`, and the serialized document is in `record["body"]`.

- Consumer deserialization and validation: `src/lambda/queue_processor/index.py`
  - `handler(...)` loops over `event["Records"]` and calls `process_message(record)`.
  - `process_message(...)` parses `record["body"]` with `json.loads(...)`.
  - It reconstructs the document using `Document.load_document(message_data, working_bucket, logger)`.
  - `Document.load_document(...)` supports both:
    - normal JSON document bodies
    - compressed document references with `{"compressed": true, "s3_uri": ...}`
  - The processor then re-reads the current document state with `document_service.get_document(object_key)` and exits early if the document was already marked `ABORTED`.

- Concurrency gate before workflow start: `src/lambda/queue_processor/index.py`
  - `update_counter(increment=True)` updates the DynamoDB concurrency counter item `counter_id=workflow_counter`.
  - The increment is conditional on `active_count < MAX_CONCURRENT`.
  - If the condition fails, `process_message(...)` returns the message ID in `batchItemFailures`, so SQS retries the message later instead of dropping it.

- Workflow start path: `src/lambda/queue_processor/index.py`
  - `start_workflow(document)` changes the in-memory document to:
    - `status = RUNNING`
    - `start_time = <utc iso timestamp>`
  - If `WORKING_BUCKET` is configured, it calls `document.serialize_document(working_bucket, "workflow_start", logger)`.
  - `Document.serialize_document(...)` in `lib/idp_common_pkg/idp_common/models.py` always compresses here because the default threshold is `0KB`.
  - Compression stores the full document JSON in S3 and returns a lightweight wrapper like:
    - `document_id`
    - `s3_uri`
    - `timestamp`
    - `status`
    - `num_pages`
    - `sections`
    - `compressed=true`
  - The processor reads merged config via `ConfigurationManager.get_merged_configuration(config_version)` and injects routing flags into the Step Functions input:
    - `use_bda`
    - `bda_project_arn` when applicable
  - Step Functions is started with:
    - `sfn.start_execution(input=json.dumps({ "document": compressed_document }))`

- Final persistence after successful delivery: `src/lambda/queue_processor/index.py`
  - After `start_execution(...)` succeeds, the processor writes the updated document back through `document_service.update_document(document)`.
  - That persists at least:
    - `status=RUNNING`
    - `start_time`
    - `workflow_execution_arn`
  - If workflow start fails after the concurrency increment, the processor decrements the counter and reports the message as failed so SQS can retry.

- Document service abstraction used on both sides: `lib/idp_common_pkg/idp_common/docs_service.py`
  - `create_document_service()` selects the backing implementation from `DOCUMENT_TRACKING_MODE`.
  - In this flow, both `queue_sender` and `queue_processor` call the same abstraction for `create_document(...)`, `get_document(...)`, and `update_document(...)`.
