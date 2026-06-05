"""Central configuration — reads .env and exposes all settings."""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Database ────────────────────────────────────────────────────────────────
DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:universe@localhost:5432/postgres").strip()

# ── Anthropic (Claude) ──────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
# Haiku: used for lightweight agents (Context, Data Analyst, QA) — ~8x cheaper & faster
CLAUDE_HAIKU_MODEL: str = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-6")

# ── Token pricing (USD per 1M tokens) — for cost-estimate telemetry ──────────
# Update these if Anthropic pricing changes. Matched by substring on model id.
# cache_read is ~10% of input; cache_write (creation) is ~125% of input.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "sonnet": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "haiku":  {"input": 1.00, "output": 5.00,  "cache_read": 0.10, "cache_write": 1.25},
    "opus":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
}
_DEFAULT_PRICING = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75}


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_read_tokens: int = 0, cache_creation_tokens: int = 0) -> float:
    """Estimate USD cost of a Claude call from token counts.

    `input_tokens` from the API already EXCLUDES cached tokens, so cache_read
    and cache_creation are billed separately at their own rates.
    """
    model_l = (model or "").lower()
    rates = _DEFAULT_PRICING
    for key, table in MODEL_PRICING.items():
        if key in model_l:
            rates = table
            break
    return round(
        (input_tokens / 1_000_000) * rates["input"]
        + (output_tokens / 1_000_000) * rates["output"]
        + (cache_read_tokens / 1_000_000) * rates["cache_read"]
        + (cache_creation_tokens / 1_000_000) * rates["cache_write"],
        6,
    )
