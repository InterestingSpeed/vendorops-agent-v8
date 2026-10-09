# Architecture notes

I kept the project deliberately narrow: vendor invoice intake and gate-entry decisions.

## Flow

`User goal -> LLM intent/planning -> business tools -> company data / GST adapter -> policy checks -> action -> verification or human approval`

## Main pieces

- **FastAPI**: web/API layer and background job endpoints
- **GPT-OSS on Groq**: primary hosted planner for the demo
- **Qwen3 on Ollama**: optional local planner
- **Tool layer**: vendor, GST, PO, duplicate, policy, approval, notification and gate-entry operations
- **Invoice parser**: QR first, Tesseract OCR fallback
- **SQLite**: sandbox company system
- **Browser UI**: live execution trace plus direct ERP-style data views

## Trust boundary

The LLM is a planner, not the database authority.

- it never receives raw SQL access
- company facts come from tools/database
- write tools repeat important validations
- invoice values are checked against the uploaded document before a gate-entry write
- uncertain/unsafe cases create approval records instead of being forced through
- successful writes are read back and verified

## Why the default demo uses a hybrid executor

I first used a fully iterative model-tool loop. It worked, but repeated model turns made free-tier rate limits and latency part of the critical path. For the main invoice workflow I moved to one LLM planning call followed by guarded execution. The full tool-calling mode is still present for cases where step-by-step model decisions are useful.

## Production direction

Replace the sandbox adapters rather than rewriting the agent:

- SQLite -> Postgres / company ERP APIs
- local GST registry -> authorized GST/GSP service
- simulated outbox -> Microsoft Graph/Gmail/enterprise messaging
- local uploads -> object storage
- in-process jobs -> durable queue/workers
- demo approvals -> real users/RBAC/audit controls
