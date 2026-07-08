# CSV Import Reimplementation Design

Date: 2026-06-18
Status: Approved in brainstorming session

## 1. Problem Evaluation

The current CSV importer service and tests pass, but the end-to-end user workflow is broken:

- The import form in settings submits directly to `POST /settings/import/csv`.
- That endpoint returns JSON preview data.
- The settings page does not render the preview rows, does not support row edits, and does not invoke confirm import from the browser flow.

Result: users can upload and receive preview data, but cannot complete import from the Settings UI.

## 2. Goals

- Provide a complete Settings-based CSV import workflow: upload, preview, edit rows inline, select rows, confirm import.
- Keep endpoint paths stable:
  - `POST /settings/import/csv`
  - `POST /settings/import/confirm`
- Preserve service-layer business logic and ledger write-path invariants.
- Improve correctness for confirm-time race conditions (dedup recheck).
- Return clear import outcomes for partial-success batches.

## 3. Non-Goals

- No bank-sync integration.
- No auto-categorization.
- No multi-account user flow changes.
- No route path renaming.

## 4. Design Summary

Adopt a server-backed import draft model.

1. Preview endpoint parses and normalizes uploaded CSV rows.
2. Backend stores a draft scoped to account, with normalized row state and metadata.
3. Frontend renders editable preview rows and selection controls using JavaScript.
4. Confirm endpoint receives `draft_id` plus user edits/selection intent.
5. Backend applies edits server-side, revalidates, recomputes dedup, imports valid selected rows through ledger, and returns a summary.

This keeps the browser interactive while preserving server authority over parsing, validation, dedup, and import decisions.

## 5. Architecture and Components

### 5.1 Router Layer

`finapp/routers/settings.py` remains the HTTP entry point for preview and confirm. Router responsibilities:

- Parse HTTP/form/json input.
- Delegate all import logic to `finapp/services/csv_import.py`.
- Return JSON payloads for preview and confirm.

### 5.2 Service Layer

`finapp/services/csv_import.py` is expanded into explicit steps:

- Parse raw CSV rows.
- Normalize row candidates (`date`, `amount_cents`, `direction`, `payee`).
- Build row-level issue lists.
- Compute preview duplicate state.
- Persist and read drafts.
- Apply user edits and revalidate at confirm time.
- Recompute import hash and recheck database duplicates before writes.
- Import accepted rows through ledger transaction creation.

### 5.3 Persistence Layer

Add an import draft persistence model (new table) to hold temporary preview state.

Proposed draft fields:

- `id`
- `account_id`
- `created_at`
- `expires_at`
- `column_map_json`
- `spent_is_negative`
- `rows_json` (normalized row payload, issues, duplicate snapshot, default selected)
- `version`

Draft access must always filter by `account_id`.

## 6. Data Flow

### 6.1 Preview

1. User uploads file and mapping from Settings.
2. Backend parses and normalizes rows.
3. Backend computes duplicate flags using:
   - Existing imported hashes in DB.
   - Intra-batch duplicate detection.
4. Backend creates draft and returns:
   - `draft_id`
   - `expires_at`
   - preview rows with row ids, parsed fields, duplicate flag, issues, selected default
   - summary counts (`total`, `valid`, `invalid`, `duplicate`)

### 6.2 Edit and Select

- Client updates row fields in-place (date, amount, direction, payee).
- Client tracks selected row ids.
- Client sends only user intent (edits + selection), not authoritative final row data.

### 6.3 Confirm

1. Backend loads draft by `account_id + draft_id`.
2. Backend applies row edits to draft rows.
3. Backend revalidates edited rows.
4. Backend recomputes import hash and repeats DB dedup check.
5. Backend imports selected rows that are valid and non-duplicate via ledger.
6. Backend returns summary:
   - `imported_count`
   - `skipped_duplicate_count`
   - `skipped_invalid_count`
   - `skipped_unselected_count`

## 7. API Contract Changes (Stable Paths)

Paths remain unchanged. Response/request bodies are upgraded to support draft lifecycle.

### 7.1 `POST /settings/import/csv`

Request: existing multipart form remains.

Response (new shape):

- `draft_id`
- `expires_at`
- `rows` (row_id + parsed fields + duplicate + selected + issues)
- `summary`

### 7.2 `POST /settings/import/confirm`

Request (new shape):

- `draft_id`
- `selected_row_ids`
- `edits` keyed by row id (date, amount, direction, payee)
- optional `version`

Response:

- import summary counts and optional per-row error details for rows that could not be imported.

## 8. UI Design for Settings Import

`finapp/templates/settings.html` adds a JS-driven preview panel below the import form:

- Preview table with editable columns:
  - Date
  - Amount
  - Direction
  - Payee
- Row status indicators:
  - Valid
  - Duplicate
  - Invalid
- Selection controls:
  - per-row checkbox
  - Select all valid
  - Unselect all
  - Reset edits (selected or all)
- Confirm button:
  - sends `draft_id + edits + selection` to confirm endpoint
- Result banner:
  - imported and skipped counts with reasons

## 9. Validation and Error Handling

- Row-level parse/validation errors are surfaced per row and do not abort full preview.
- Confirm is partial-success by design: valid selected rows import even if others fail.
- Expired draft returns recoverable error requiring re-upload.
- Unknown row ids in edits/selection are ignored and reported.
- Confirm dedup is authoritative and uses current DB state, not only preview snapshot.

## 10. Invariants and Safety

- Transactions remain source of truth.
- Ledger service remains single write path for transaction creation.
- `amount_cents` is integer magnitude; `direction` carries sign semantics.
- All operations are account-scoped.
- Dedup hash remains aligned with ledger `compute_import_hash` logic.

## 11. Testing Plan

### 11.1 Service Tests

Extend `tests/test_csv_import.py` to cover:

- Draft creation and expiry.
- Row edit application and revalidation.
- Confirm-time dedup race safety.
- Partial-success imports and summary counts.
- Unknown/invalid row edit handling.

### 11.2 Integration Tests

Extend `tests/integration/test_phase10_import_settings.py` to cover:

- Preview response includes draft metadata and row issues.
- Confirm imports only selected valid non-duplicate rows.
- Expired draft path and recovery behavior.
- End-to-end settings import flow with inline edits.

### 11.3 Regression

Keep existing parser/date/direction tests and verify reconciliation gate remains green after mixed import scenarios.

## 12. Migration and Rollout Notes

- Add migration for import draft table.
- Keep existing endpoint paths and preserve compatibility for current upload form fields.
- Remove no critical current behavior; replace only the broken no-preview/no-confirm UX path with draft-backed flow.

## 13. Acceptance Criteria

- User can complete upload -> preview -> edit -> select -> confirm entirely from Settings.
- Duplicate rows are unchecked by default and reported in summary.
- Invalid rows are clearly surfaced and never silently imported.
- Confirm endpoint safely handles stale/expired previews.
- Reconciliation remains green after import integration tests.
