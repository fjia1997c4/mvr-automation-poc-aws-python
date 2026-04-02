# File Upload Sequence Documentation

## End-to-End Upload Flow
This flow is implemented across multiple components, as described below:

1. **User Interaction**
   - **Step**: The user selects a file to upload.
   - **Code Reference**: 
     - `src/ui/src/components/upload-document/UploadDocumentPanel.tsx` (UI component for file upload).

2. **AppSync GraphQL API**
   - **Step**: The UI sends a GraphQL mutation (`uploadDocument`) to AppSync.
   - **Code Reference**:
     - `template.yaml` (AppSync resource definition).
     - `nested/appsync/template.yaml` (nested AppSync template).

3. **UploadResolver Lambda**
   - **Step**: AppSync invokes the `UploadResolver` Lambda to generate a presigned POST URL for S3.
   - **Code Reference**:
     - `nested/appsync/src/lambda/upload_resolver/index.py` (Lambda implementation).

4. **S3 Input Bucket**
   - **Step**: The browser uploads the file to the S3 Input Bucket using the presigned URL.
   - **Code Reference**:
     - `template.yaml` (S3 bucket configuration).

5. **EventBridge Notification**
   - **Step**: S3 emits an `Object Created` event to EventBridge.
   - **Code Reference**:
     - `template.yaml` (EventBridge rule configuration).

6. **QueueSender Lambda**
   - **Step**: EventBridge invokes the `QueueSender` Lambda, which:
     - Reads object metadata from S3.
     - Resolves the active configuration version from DynamoDB.
     - Creates a queued document record in DynamoDB.
     - Sends a serialized document message to the SQS DocumentQueue.
   - **Code Reference**:
     - `src/lambda/queue_sender/index.py` (Lambda implementation).
     - `template.yaml` (DynamoDB and SQS configuration).

7. **QueueProcessor Lambda**
   - **Step**: The `QueueProcessor` Lambda:
     - Reads the document message from SQS.
     - Checks and increments concurrency in DynamoDB.
     - Compresses the document payload into the Working S3 Bucket.
     - Reads the merged configuration from DynamoDB.
     - Starts the Step Functions execution for document processing.
     - Updates the document status to `RUNNING` in DynamoDB.
   - **Code Reference**:
     - `src/lambda/queue_processor/index.py` (Lambda implementation).
     - `template.yaml` (DynamoDB, S3, and Step Functions configuration).
     - `patterns/unified/template.yaml` (Step Functions definition).

---

## S3 to EventBridge Flow
This flow details the interaction between S3, EventBridge, and the `QueueSender` Lambda:

1. **S3 Event**
   - **Step**: The Input S3 Bucket emits an `Object Created` event.
   - **Code Reference**:
     - `template.yaml` (S3 bucket configuration with `EventBridgeEnabled: true`).

2. **EventBridge Rule**
   - **Step**: EventBridge filters events for the InputBucket and invokes the `QueueSender` Lambda.
   - **Code Reference**:
     - `template.yaml` (EventBridge rule configuration).

3. **QueueSender Lambda**
   - **Step**: The Lambda processes the EventBridge envelope to extract bucket name, object key, and event time. It reconstructs the document state using AWS APIs.
   - **Code Reference**:
     - `src/lambda/queue_sender/index.py` (Lambda implementation).
     - `lib/idp_common_pkg/idp_common/models.py` (Document model for S3 events).

4. **DynamoDB and SQS**
   - **Step**: The Lambda interacts with DynamoDB for configuration and document tracking, and sends messages to the SQS DocumentQueue.
   - **Code Reference**:
     - `template.yaml` (DynamoDB and SQS configuration).

---

## Key Notes
- The `UploadResolver` Lambda only generates a presigned upload target and does not process document contents.
- Actual ingestion begins with the S3 `Object Created` event, which triggers the `QueueSender` Lambda.
- The `QueueProcessor` Lambda handles document compression and initiates the Step Functions workflow.

## Diagram

Below is a version of the file upload sequence diagram:

```mermaid
sequenceDiagram
    actor User as User
    participant UI as UploadDocumentPanel
    participant AppSync as AppSync GraphQL API
    participant Upload as UploadResolver Lambda
    participant S3 as Input S3 Bucket
    participant EB as EventBridge
    participant Sender as QueueSender Lambda
    participant DDB as Tracking and Config DynamoDB
    participant SQS as DocumentQueue
    participant Processor as QueueProcessor Lambda
    participant Work as Working S3 Bucket
    participant SFN as Step Functions State Machine

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