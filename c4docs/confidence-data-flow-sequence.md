# Confidence Data Flow — End-to-End Sequence

This document traces how a per-field confidence score (e.g. `Confidence: 99.0% / Threshold: 70.0%` shown next to an extracted value in the Visual Editor) is produced by the backend pipeline and rendered in the UI.

The confidence number is **not** a formula. It is an LLM self-assessment produced by the Assessment step of the Step Functions workflow. The UI reads the stored value and multiplies by 100 for display.

---

## 1. Write path — backend produces `explainability_info.confidence`

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant S3in as S3 Input Bucket
    participant EB as EventBridge
    participant SQS
    participant QProc as Queue Processor Lambda
    participant SFN as Step Functions
    participant OCR as OCR Lambda<br/>(Textract)
    participant Extract as Extraction Lambda<br/>(Bedrock)
    participant Assess as Assessment Lambda<br/>(AssessmentService)
    participant Bedrock
    participant S3out as S3 Output Bucket

    User->>S3in: Upload passport.jpg
    S3in->>EB: ObjectCreated event
    EB->>SQS: Enqueue job
    SQS->>QProc: Deliver message
    QProc->>SFN: StartExecution

    SFN->>OCR: Run OCR step
    OCR->>OCR: Textract AnalyzeID / DetectText
    OCR->>S3out: Write textConfidence.json<br/>(per-word OCR confidence %)

    SFN->>Extract: Run Extraction step
    Extract->>Bedrock: Prompt (image + OCR text)
    Bedrock-->>Extract: inference_result JSON<br/>{name:{given_name:"HAPPY"}, ...}
    Extract->>S3out: Write extraction result

    SFN->>Assess: Run Assessment step
    Assess->>S3out: Read extraction_result + textConfidence.json
    Assess->>Bedrock: Prompt w/ {EXTRACTION_RESULTS},<br/>{OCR_TEXT_CONFIDENCE},<br/>{DOCUMENT_IMAGE}
    Bedrock-->>Assess: {"given_name":{"confidence":0.99,<br/>"confidence_reason":"..."}}
    Note right of Assess: service.py:_enhance_dict_assessment<br/>adds confidence_threshold (0.70)
    Assess->>S3out: Write extraction_data with<br/>explainability_info = [{...}]<br/>(service.py:1031)
```

---

## 2. Assessment step — internal detail

Shows prompt construction, Bedrock invocation, parsing, threshold enhancement, and the write back to S3.

```mermaid
sequenceDiagram
    autonumber
    participant SFN as Step Functions
    participant Lambda as Assessment Lambda<br/>(handler)
    participant Svc as AssessmentService<br/>service.py
    participant Cfg as IDPConfig<br/>(DynamoDB-backed)
    participant S3 as S3 Output Bucket
    participant Img as image.prepare_image
    participant BR as bedrock.invoke_model
    participant Bedrock as Amazon Bedrock<br/>(e.g. Nova Pro / Claude)

    SFN->>Lambda: Invoke with {document, section_id}
    Lambda->>Svc: process_document_section(document, section_id)

    Svc->>Cfg: config.assessment.enabled ?
    alt disabled
        Cfg-->>Svc: False
        Svc-->>Lambda: return document (skip)
    else enabled
        Cfg-->>Svc: True
    end

    Note over Svc: Locate section, sort page_ids,<br/>emit metrics (InputDocumentsForAssessment)

    Svc->>S3: get_json_content(section.extraction_result_uri)<br/>(service.py:741)
    S3-->>Svc: {inference_result, metadata}

    loop For each page_id
        Svc->>S3: get_text_content(page.parsed_text_uri)
        S3-->>Svc: page markdown text
    end

    Svc->>Cfg: assessment.image.target_width / target_height
    loop For each page_id
        Svc->>Img: prepare_image(image_uri, w, h)
        Img->>S3: GetObject page image
        S3-->>Img: bytes
        Img-->>Svc: resized image content
    end

    loop For each page_id
        Svc->>Svc: _get_text_confidence_data(page)
        Note right of Svc: Reads textConfidence.json<br/>produced by OCR step<br/>(per-word Textract confidence)
    end

    Svc->>Cfg: model, temperature, top_k, top_p,<br/>max_tokens, system_prompt, task_prompt
    Svc->>Svc: _get_class_schema(class_label)<br/>_format_property_descriptions(...)

    Svc->>Svc: _build_content_with_or_without_image_placeholder(...)
    Note right of Svc: Substitutes<br/>{DOCUMENT_TEXT}, {DOCUMENT_CLASS},<br/>{ATTRIBUTE_NAMES_AND_DESCRIPTIONS},<br/>{EXTRACTION_RESULTS},<br/>{OCR_TEXT_CONFIDENCE},<br/>{DOCUMENT_IMAGE}<br/>→ multimodal content list

    Svc->>BR: invoke_model(model_id, system_prompt,<br/>content, temp, top_k, top_p,<br/>max_tokens, context="Assessment")
    BR->>Bedrock: Converse API (multimodal)
    Bedrock-->>BR: raw response + usage
    BR-->>Svc: {response, metering}

    Svc->>Svc: extract_text_from_response()<br/>extract_json_from_text() → json.loads
    alt JSON parses OK
        Svc->>Svc: assessment_data =<br/>{"given_name":{"confidence":0.99,<br/>  "confidence_reason":"...", "bbox":[...], "page":1}, ...}
    else parsing fails
        Svc->>Svc: Default all attrs to<br/>{confidence:0.5,<br/>confidence_reason:"...default..."}<br/>(service.py:890)<br/>parsing_succeeded = False
    end

    Svc->>Svc: _extract_geometry_from_assessment()
    Note right of Svc: If LLM returned bbox+page,<br/>convert 0–1000 scale → 0–1 geometry

    loop For each attr_name in assessment_data
        Svc->>Svc: threshold = prop_schema[X_AWS_IDP_CONFIDENCE_THRESHOLD]<br/>?? default_confidence_threshold
        alt attr_type = simple / group (dict)
            Svc->>Svc: _enhance_dict_assessment(attr, threshold)
            Svc->>Svc: _check_confidence_alerts(...)
        else attr_type = list
            Svc->>Svc: Per-item enhance + per-item alert check
        else unexpected shape
            Svc->>Svc: default {confidence:0.5, threshold}
        end
    end

    Svc->>Svc: extraction_data["explainability_info"] =<br/>[enhanced_assessment_data]<br/>(service.py:1031)
    Svc->>Svc: metadata.assessment_time_seconds<br/>metadata.assessment_parsing_succeeded

    Svc->>S3: write_content(extraction_data,<br/>bucket, key) — same URI<br/>(service.py:1040)
    S3-->>Svc: OK

    Svc->>Svc: doc_section.confidence_threshold_alerts = alerts
    Svc->>Svc: document.metering = merge(...)
    Svc-->>Lambda: return document
    Lambda-->>SFN: {document (with alerts), metering}
```

### What each attribute looks like at the end

After the loop at [lib/idp_common_pkg/idp_common/assessment/service.py:919-1028](../lib/idp_common_pkg/idp_common/assessment/service.py#L919-L1028), the object written to `explainability_info[0]` has the shape the UI consumes:

```json
{
  "name": {
    "given_name": {
      "confidence": 0.99,
      "confidence_reason": "Clearly visible in MRZ and visual zone",
      "confidence_threshold": 0.70,
      "geometry": [{ "boundingBox": { "top": 0.2, "left": 0.1, "width": 0.2, "height": 0.05 }, "page": 1 }]
    }
  }
}
```

---

## 3. Read path — UI fetches and renders the confidence

```mermaid
sequenceDiagram
    autonumber
    participant Browser
    participant DocDetails as DocumentDetails.tsx
    participant Ctx as useDocumentsContext
    participant Hook as useGraphQlApi hook<br/>(use-graphql-api.ts)
    participant Sections as SectionsPanel.tsx
    participant JSONViewer as JSONViewer.tsx
    participant Modal as VisualEditorModal.tsx
    participant Util as confidence-alerts-utils.ts
    participant Amplify as AWS Amplify
    participant AppSync as AppSync GraphQL API
    participant DDB as DynamoDB<br/>TrackingTable
    participant FileLambda as GetFileContentsResolver Lambda<br/>nested/appsync/src/lambda/<br/>get_file_contents_resolver/index.py
    participant S3out as S3 Output Bucket

    Note over Browser,S3out: Phase A — Load document → get OutputJSONUri per section

    Browser->>DocDetails: Navigate to document
    DocDetails->>Ctx: getDocumentDetailsFromIds([objectKey])<br/>(DocumentDetails.tsx:64)
    Ctx->>Hook: delegate
    Hook->>Amplify: client.graphql({query:getDocument,<br/>variables:{objectKey}})<br/>(use-graphql-api.ts:179)
    Amplify->>AppSync: POST /graphql (signed w/ Cognito JWT)
    Note right of AppSync: Direct DynamoDB VTL resolver<br/>(no Lambda)<br/>nested/appsync/template.yaml:3678-3696
    AppSync->>DDB: GetItem PK=doc#<objectKey>, SK=none
    DDB-->>AppSync: Sections[{Id, PageIds, Class,<br/>OutputJSONUri, ConfidenceThresholdAlerts}]
    AppSync-->>Amplify: $util.toJson($ctx.result)
    Amplify-->>Hook: {data:{getDocument:{...}}}
    Hook-->>Ctx: Document[]
    Ctx-->>DocDetails: Document
    DocDetails->>Sections: render <SectionsPanel items={Sections} />
    Sections->>JSONViewer: <FileViewer fileUri={item.OutputJSONUri} ...>
    Note right of Sections: OutputJSONUri =<br/>s3://<output-bucket>/<docKey>/sections/<id>/result.json

    Note over Browser,S3out: Phase B — User opens Visual Editor → fetch the JSON

    Browser->>JSONViewer: Click "Edit Mode" button
    JSONViewer->>JSONViewer: handleViewEditData()<br/>(JSONViewer.tsx:76)
    JSONViewer->>Amplify: client.graphql({query:getFileContents,<br/>variables:{s3Uri:fileUri}})<br/>(JSONViewer.tsx:82)
    Amplify->>AppSync: POST /graphql
    AppSync->>FileLambda: Invoke Lambda resolver<br/>arguments.s3Uri
    FileLambda->>FileLambda: urlparse → bucket, key<br/>(index.py:43)
    FileLambda->>S3out: s3.get_object(Bucket, Key)
    S3out-->>FileLambda: Body bytes (extraction_data JSON)
    FileLambda-->>AppSync: {content, contentType, size, isBinary}
    AppSync-->>Amplify: response
    Amplify-->>JSONViewer: result
    JSONViewer->>JSONViewer: JSON.parse(content) → jsonData<br/>(JSONViewer.tsx:103)
    Note right of JSONViewer: jsonData = {<br/>  inference_result: {...},<br/>  explainability_info: [{<br/>    name:{given_name:{<br/>      confidence:0.99,<br/>      confidence_threshold:0.70,<br/>      confidence_reason:"...",<br/>      geometry:[...]<br/>    }}<br/>  }]<br/>}
    JSONViewer->>Modal: <VisualEditorModal sectionData=memoized,<br/>jsonData=parsed />

    Note over Browser,S3out: Phase C — Render each field's confidence

    Modal->>Modal: Extract explainabilityInfo =<br/>jsonData.explainability_info
    loop For each field rendered (e.g. given_name)
        Modal->>Util: getFieldConfidenceInfo(<br/>"given_name", explainabilityInfo,<br/>path=["name"], mergedConfig)<br/>(VisualEditorModal.tsx:461)
        Util->>Util: explainabilityData = array[0]<br/>Walk path ["name"]<br/>fieldData = current["given_name"]<br/>confidence = 0.99<br/>confidence_threshold = 0.70<br/>(confidence-alerts-utils.ts:343-400)
        Util->>Util: isAboveThreshold = 0.99 >= 0.70<br/>textColor = "#16794d" (green)<br/>displayMode = "with-threshold"
        Util-->>Modal: {hasConfidenceInfo:true,<br/>confidence:0.99, confidenceThreshold:0.70,<br/>isAboveThreshold:true,<br/>textColor, displayMode}
        Modal->>Browser: Render line<br/>`Confidence: ${(0.99*100).toFixed(1)}%<br/> / Threshold: ${(0.70*100).toFixed(1)}%`<br/>→ "Confidence: 99.0% / Threshold: 70.0%"<br/>(VisualEditorModal.tsx:650)
    end
```

---

## 4. `DocumentDetails.tsx` does not import `GetDocument` directly

`DocumentDetails.tsx` calls a context method; the actual GraphQL call lives in a hook.

### Call chain

```
DocumentDetails.tsx:64   sendInitDocumentRequests()
  └─ getDocumentDetailsFromIds([objectKey])      ← destructured from useDocumentsContext() at line 51
        │
        │  useDocumentsContext → src/ui/src/contexts/documents.ts (context type only)
        │  Provider implementation is in the custom hook:
        │
        └─ src/ui/src/hooks/use-graphql-api.ts:176-192
              const getDocumentPromises = objectKeys.map((objectKey) =>
                client.graphql({ query: getDocument, variables: { objectKey } })
              );
              ▲ This is the actual AppSync call (line 179).
              `getDocument` imported at use-graphql-api.ts:12 from '../graphql/generated',
               which re-exports the compiled query from
               src/ui/src/graphql/operations/queries/GetDocument.graphql
```

### Exact call sites

| File | Line | Role |
|---|---|---|
| [DocumentDetails.tsx:51](../src/ui/src/components/document-details/DocumentDetails.tsx#L51) | Destructure `getDocumentDetailsFromIds` from `useDocumentsContext()` |
| [DocumentDetails.tsx:64](../src/ui/src/components/document-details/DocumentDetails.tsx#L64) | `await getDocumentDetailsFromIds([objectKey])` in `sendInitDocumentRequests`, called from `useEffect` at [:74-80](../src/ui/src/components/document-details/DocumentDetails.tsx#L74-L80) |
| [use-graphql-api.ts:12](../src/ui/src/hooks/use-graphql-api.ts#L12) | `import { getDocument } from '../graphql/generated'` |
| [use-graphql-api.ts:176-192](../src/ui/src/hooks/use-graphql-api.ts#L176-L192) | Defines `getDocumentDetailsFromIds`; line 179 issues `client.graphql({ query: getDocument, ... })` |
| [contexts/documents.ts:13](../src/ui/src/contexts/documents.ts#L13) | Context type: `getDocumentDetailsFromIds: (objectKeys: string[]) => Promise<Document[]>` |

### Why it's wrapped in a context

- List views receive lightweight data via `listDocuments` / subscriptions (no `Sections` field).
- Detail views need the heavier payload including `Sections[].OutputJSONUri`.
- `getDocumentDetailsFromIds` is the only path that issues `GetDocument`, and it is **not** called by subscription handlers ([use-graphql-api.ts:195](../src/ui/src/hooks/use-graphql-api.ts#L195)) — subscriptions already carry rich payloads.
- `DocumentDetails.tsx` triggers it exactly once per `objectKey` mount, then relies on `onUpdateDocument` subscription events handled in the same context to keep state fresh.

---

## 5. Summary of backend calls on the read path

| # | UI call | GraphQL op | Lambda / data source (file path) | Purpose |
|---|---|---|---|---|
| 1 | [DocumentDetails.tsx:64](../src/ui/src/components/document-details/DocumentDetails.tsx#L64) → [use-graphql-api.ts:179](../src/ui/src/hooks/use-graphql-api.ts#L179) | [GetDocument](../src/ui/src/graphql/operations/queries/GetDocument.graphql) | **Direct DynamoDB VTL resolver** — no Lambda. VTL templates inline in [nested/appsync/template.yaml:3678-3696](../nested/appsync/template.yaml#L3678-L3696). Data source: `TrackingTableDataSource` (DynamoDB, `GetItem` on `PK=doc#<ObjectKey>, SK=none`) | Returns `Sections[].OutputJSONUri` |
| 2 | [JSONViewer.tsx:82](../src/ui/src/components/document-viewer/JSONViewer.tsx#L82) | [GetFileContents](../src/ui/src/graphql/operations/queries/GetFileContents.graphql) | Lambda: [nested/appsync/src/lambda/get_file_contents_resolver/index.py](../nested/appsync/src/lambda/get_file_contents_resolver/index.py) — calls `s3.get_object` on the Output bucket. CFN resource at [nested/appsync/extracted_resources.yaml:881-930](../nested/appsync/extracted_resources.yaml#L881-L930) | Reads the extraction JSON (with `explainability_info`) from S3 |
| 3 | [JSONViewer.tsx:103](../src/ui/src/components/document-viewer/JSONViewer.tsx#L103) | — (client) | `JSON.parse` | Parses the JSON string |
| 4 | [VisualEditorModal.tsx:461](../src/ui/src/components/document-viewer/VisualEditorModal.tsx#L461) | — (client) | [confidence-alerts-utils.ts:332-431](../src/ui/src/components/common/confidence-alerts-utils.ts#L332-L431) `getFieldConfidenceInfo` | Walks `explainability_info[0]` along field path, reads `confidence` + `confidence_threshold` |
| 5 | [VisualEditorModal.tsx:650](../src/ui/src/components/document-viewer/VisualEditorModal.tsx#L650) | — (client) | React render | Displays `Confidence: 99.0% / Threshold: 70.0%` |

### Why two AppSync calls (not one)

The `OutputJSONUri` is kept as a pointer in DynamoDB; the actual extraction JSON (potentially large, with per-field reasons and geometries) lives in S3. This separation keeps `GetDocument` fast for the list/detail view and defers the heavier S3 read until the user opens the Visual Editor. The `ConfidenceThresholdAlerts` summary *is* returned directly on `GetDocument` — that is how the Document Details header can show the "Confidence Alerts: 0" badge without opening the JSON.

---

## 6. Key source references

### Backend — assessment (write path)
- Entry: [process_document_section](../lib/idp_common_pkg/idp_common/assessment/service.py#L669)
- Prompt build (with image placeholder): [_build_content_with_or_without_image_placeholder](../lib/idp_common_pkg/idp_common/assessment/service.py#L473)
- Template substitution: [_prepare_prompt_from_template](../lib/idp_common_pkg/idp_common/assessment/service.py#L292)
- Bedrock call: [service.py:856](../lib/idp_common_pkg/idp_common/assessment/service.py#L856)
- Parse failure fallback → `0.5`: [service.py:890-895](../lib/idp_common_pkg/idp_common/assessment/service.py#L890-L895)
- bbox → geometry conversion: [_extract_geometry_from_assessment at :900](../lib/idp_common_pkg/idp_common/assessment/service.py#L900)
- Threshold attach: [_enhance_dict_assessment at :945](../lib/idp_common_pkg/idp_common/assessment/service.py#L945)
- Alert emission: [_check_confidence_alerts at :950](../lib/idp_common_pkg/idp_common/assessment/service.py#L950)
- Persist: [service.py:1031](../lib/idp_common_pkg/idp_common/assessment/service.py#L1031), [:1040](../lib/idp_common_pkg/idp_common/assessment/service.py#L1040)

### Frontend — read path
- [confidence-alerts-utils.ts:332-431](../src/ui/src/components/common/confidence-alerts-utils.ts#L332-L431) — `getFieldConfidenceInfo`
- [VisualEditorModal.tsx:461](../src/ui/src/components/document-viewer/VisualEditorModal.tsx#L461) — call site
- [VisualEditorModal.tsx:650](../src/ui/src/components/document-viewer/VisualEditorModal.tsx#L650) — render formula `(confidence * 100).toFixed(1)%`
- [JSONViewer.tsx:76-120](../src/ui/src/components/document-viewer/JSONViewer.tsx#L76-L120) — `handleViewEditData` flow

### AppSync
- [GetDocument.graphql](../src/ui/src/graphql/operations/queries/GetDocument.graphql)
- [GetFileContents.graphql](../src/ui/src/graphql/operations/queries/GetFileContents.graphql)
- [template.yaml:3678-3696](../nested/appsync/template.yaml#L3678-L3696) — direct DDB resolver for `getDocument`
- [get_file_contents_resolver/index.py](../nested/appsync/src/lambda/get_file_contents_resolver/index.py) — Lambda resolver for `getFileContents`
