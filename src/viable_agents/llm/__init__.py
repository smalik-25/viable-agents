"""The model client layer. Implements the kernel's ``LLMClient`` protocol.

The kernel defines the seam; this package is the only legal path from an agent to
a model, and it records every call (hard rule 3). ``ScriptedLLMClient`` returns
deterministic, priced fake responses so cost accounting is exercised without an
API key; the real Anthropic client is a thin wrapper used only by the ``live``
test. Pricing and the model registry live here because they depend on
``config/models.yaml``, which the kernel does not read.
"""

from viable_agents.llm.budget import BudgetGuard
from viable_agents.llm.budgets_config import BudgetsConfig, CostRunawayConfig, load_budgets
from viable_agents.llm.client import (
    MAX_STRUCTURED_OUTPUT_ATTEMPTS,
    LLMCall,
    ScriptedLLMClient,
    StructuredOutputError,
    check_budget_or_refuse,
)
from viable_agents.llm.config import ModelsConfig, TierSpec, load_models
from viable_agents.llm.pricing import NoPriceError, Price, cost_usd, price_for
from viable_agents.llm.recorder import CallRecorder, InMemoryCallRecorder

__all__ = [
    "MAX_STRUCTURED_OUTPUT_ATTEMPTS",
    "BudgetGuard",
    "BudgetsConfig",
    "CallRecorder",
    "CostRunawayConfig",
    "InMemoryCallRecorder",
    "LLMCall",
    "ModelsConfig",
    "NoPriceError",
    "Price",
    "ScriptedLLMClient",
    "StructuredOutputError",
    "TierSpec",
    "check_budget_or_refuse",
    "cost_usd",
    "load_budgets",
    "load_models",
    "price_for",
]
