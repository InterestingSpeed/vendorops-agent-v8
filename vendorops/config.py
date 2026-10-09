from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DEFAULT_DB_PATH = DATA_DIR / "vendorops.db"


def db_path() -> Path:
    raw = os.getenv("VENDOROPS_DB_PATH")
    if not raw:
        return DEFAULT_DB_PATH
    p = Path(raw)
    return p if p.is_absolute() else BASE_DIR / p


def groq_api_key() -> str:
    return os.getenv("GROQ_API_KEY", "").strip()


def groq_model() -> str:
    return os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()


def groq_base_url() -> str:
    return os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")


def ollama_model() -> str:
    # qwen3:4b is intentionally conservative so the local fallback can run on
    # modest student hardware. Users with more RAM can switch to qwen3:8b.
    return os.getenv("OLLAMA_MODEL", "qwen3:4b").strip()


def ollama_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/")


def llm_max_steps() -> int:
    try:
        return max(1, min(50, int(os.getenv("LLM_MAX_STEPS", "20"))))
    except ValueError:
        return 20


def llm_timeout_seconds() -> float:
    try:
        return max(10.0, min(600.0, float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))))
    except ValueError:
        return 120.0


def llm_max_output_tokens() -> int:
    try:
        return max(64, min(1024, int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "320"))))
    except ValueError:
        return 320

def groq_rate_limit_retries() -> int:
    try:
        return max(0, min(5, int(os.getenv("GROQ_RATE_LIMIT_RETRIES", "3"))))
    except ValueError:
        return 3

def groq_rate_limit_max_wait() -> float:
    try:
        return max(1.0, min(60.0, float(os.getenv("GROQ_RATE_LIMIT_MAX_WAIT_SECONDS", "25"))))
    except ValueError:
        return 25.0
