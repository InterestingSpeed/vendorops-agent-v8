# Validation summary — v8

Last validation pass before submission.

- `python -m pytest -q` -> **37 passed**
- 12 business scenarios retained
- SQLite integrity check -> **ok**
- foreign-key check -> **0 violations**
- background execution emits live tool start/finish events
- gate-entry writes are verified after creation
- premature model completion is guarded
- Groq rate-limit retry path is covered
- approval decisions are audited and now update the execution outcome in the UI
- unread approval badge/highlight state is handled in the browser
- Vendors/POs/Invoices/Gate Entries/Approvals/Outbox/Audit/Policies are directly browsable

External Groq/Ollama calls depend on the user's own runtime/key. Provider protocol paths are covered in tests with compatible mocked responses; `scripts/preflight.py` checks the local setup before a demo.

## Windows test isolation hotfix

The submission test suite now runs against a per-test temporary SQLite database rather than `data/vendorops.db`. This avoids Windows file-lock errors when the demo app is open. OCR-only tests are automatically skipped when the optional Tesseract executable is unavailable.

- Windows live-reset regression: database resets in-place even while a reader connection is open; no file unlink is required.
