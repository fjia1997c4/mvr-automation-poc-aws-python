# Active Context

## Current Work Focus

### Pattern Unification: Phase 2 — COMPLETE (February 17, 2026)
**Status:** ✅ All code changes complete. Ready for deploy & test.

#### What Was Done
Merged the two separate IDP patterns (Pattern-1/BDA and Pattern-2/Pipeline) into a single unified pattern. The `use_bda` configuration flag (set at runtime via the UI) determines whether documents are processed via BDA or the step-by-step pipeline.

#### Architecture Summary (Unified)
```
Main template (template.yaml)
  └── PATTERNSTACK (patterns/unified/)
        ├── 1 ECR Repository (all 12 images)
        ├── 1 CodeBuild Project (sequential build via buildspec.yml)
        ├── 1 ImageVersion (content-based hash)
        ├── 1 SourceZipfile (unified-source-{hash}.zip)
        ├── Unified State Machine (routes via use_bda flag)
        │     ├── BDA branch: InvokeBDA → BDAProcessResults → shared tail
        │     └── Pipeline branch: OCR → Classification → Extraction → Assessment → ProcessResults → RuleValidation → shared tail
        │     └── Shared tail: HITL check → Summarization → Evaluation
        ├── 12 Lambda Functions:
        │     BDA: InvokeBDAFunction, BDAProcessResultsFunction, BDACompletionFunction
        │     Pipeline: OCRFunction, ClassificationFunction, ExtractionFunction,
        │               AssessmentFunction, ProcessResultsFunction, RuleValidationFunction,
        │               RuleValidationOrchestrationFunction
        │     Shared: SummarizationFunction, EvaluationFunction
        └── Supporting: BDAMetadataTable, BDAEventRule, CloudWatch Dashboard
```

#### Key Files Modified/Created
- **`patterns/unified/template.yaml`** — Unified CloudFormation nested stack (single ECR, CodeBuild, all Lambda functions)
- **`patterns/unified/buildspec.yml`** — Builds all 12 Docker images sequentially
- **`patterns/unified/src/`** — All 12 function directories (BDA functions from pattern-1, Pipeline functions from pattern-2)
- **`patterns/unified/statemachine/workflow.asl.json`** — Routes via `use_bda` flag
- **`config_library/unified/`** — Configuration presets (copy of pattern-2 library)
- **`template.yaml`** — Main stack: single `PATTERNSTACK`, no `IDPPattern` selector, consolidated `ConfigurationPreset` parameter
- **`publish.py`** — `package_unified_source()`, unified tokens (`<UNIFIED_IMAGE_VERSION>`, `<UNIFIED_SOURCE_ZIPFILE_TOKEN>`), component dependency map
- **`nested/appsync/template.yaml`** — Removed `IsPattern1` conditional (BDA resolvers always created)

#### Key Design Decisions
1. **Single ECR + Single CodeBuild** — All 12 images built sequentially from one source zip
2. **Source in `patterns/unified/src/`** — Copied from pattern-1/pattern-2, with BDA processresults renamed to `bda_processresults_function`
3. **Pattern-2 schema as superset** — `UpdateSchemaConfig` from pattern-2 includes `use_bda` toggle and all step-by-step config sections
4. **Shared functions from Pipeline** — Summarization/Evaluation use pattern-2 versions (superset with LambdaHook support)
5. **Config from `config_library/unified/`** — Same as pattern-2 configs for now; `use_bda` toggle is in the schema, not the preset configs

#### Resource Naming
- BDA-specific: `BDAProcessResultsFunction`, `BDAMetadataTable`, `BDACompletionFunction`
- Pipeline-specific: `OCRFunction`, `ClassificationFunction`, etc. (no prefix)
- Shared: `DocumentProcessingStateMachine`, `ECRRepository`, `DockerBuildProject`

#### Parameters (Main Template)
- **Removed**: `IDPPattern`, `Pattern1Configuration`, `Pattern2Configuration`
- **Added**: `ConfigurationPreset` (single dropdown, default `lending-package-sample`)
- **Relabeled**: BDA Project ARN, Custom Classification/Extraction Model ARNs (removed "Pattern1"/"Pattern2" prefixes)
- **Kept** (parameter names preserved for updates): `Pattern1BDAProjectArn`, `Pattern2CustomClassificationModelARN`, `Pattern2CustomExtractionModelARN`

#### Token Flow (publish.py → template.yaml → unified template)
```
publish.py:
  package_unified_source() → unified-source-{hash}.zip → S3
  
template.yaml tokens:
  <UNIFIED_SOURCE_ZIPFILE_TOKEN> → unified-source-{hash}.zip
  <UNIFIED_IMAGE_VERSION> → {hash} (extracted from zipfile name)
  
PATTERNSTACK params:
  ImageVersion: "{hash}"
  SourceZipfile: "unified-source-{hash}.zip"
```

### BDA Routing Fix (February 23, 2026)
**Status:** ✅ Code changes complete. Ready for deploy & test.

Fixed 3 bugs preventing BDA processing from being triggered:
1. **Bug #0 (Blocking):** `CONFIG_TABLE` env var was missing from QueueProcessor Lambda — `os.environ.get('CONFIG_TABLE')` always returned `None`, so `use_bda` always defaulted to `False`
2. **Bug #1:** Even with CONFIG_TABLE set, the raw DynamoDB `get_item` read could never find `use_bda` because config data is gzip-compressed — needed to use `ConfigurationManager` to decompress
3. **Bug #2:** BDA Project ARN was static from CloudFormation deploy time (`${BDAProjectArn}` substitution), but BDA projects are now per-config-version — changed to `$.document.bda_project_arn` from state machine input

**Files Modified:**
- `src/lambda/queue_processor/index.py` — Uses `ConfigurationManager` to read `use_bda` + `bda_project_arn` per config version
- `template.yaml` — Added `CONFIG_TABLE` env var and `DynamoDBReadPolicy` to QueueProcessor
- `patterns/unified/statemachine/workflow.asl.json` — `BDA_InvokeDataAutomation` now uses `"BDAProjectArn.$": "$.document.bda_project_arn"` (dynamic from input)

**Safety features added:**
- If `use_bda=True` but no BDA project ARN is linked, falls back to pipeline mode with clear warning log
- If `CONFIG_TABLE` env var is missing, logs warning and defaults to pipeline mode

### Remaining Work (Next Session)

#### 🔴 High Priority
1. **Deploy & Test** — `python publish.py <bucket> <prefix> <region> --clean-build` → deploy as new stack
2. **Fix any deploy issues** — Watch for template validation errors, CodeBuild failures, etc.

#### 🟡 Medium Priority  
3. **Validate Makefile/CI** — `make validate-buildspec` checks `patterns/*/buildspec.yml` — may need to include `patterns/unified/buildspec.yml`
4. **GovCloud template** — `scripts/generate_govcloud_template.py` may reference old pattern paths
5. **CI/CD pipeline** — `.gitlab-ci.yml` may reference old pattern paths

#### 🟢 Low Priority
6. **Clean up old dirs** — `patterns/pattern-1/`, `patterns/pattern-2/` still exist (source now in `unified/src/`)
7. **Update docs** — `docs/pattern-1.md`, `docs/pattern-2.md`, deployment docs
8. **Config library enhancement** — Add `use_bda: true` variant configs for BDA-specific presets

---

### Configuration Storage: Full Configs Per Version (February 14, 2026)
**Status:** ✅ Completed

Each config version stores a complete, self-contained configuration snapshot (no more sparse deltas).

---

## Important Patterns and Preferences

### Template Outputs Interface (Unified Pattern)
Same outputs as before — no breaking changes:
- `StateMachineName`, `StateMachineArn`, `StateMachineLogGroup`
- `PatternLogGroups` (all 12 function log groups + state machine)
- `DashboardName`, `DashboardArn`
