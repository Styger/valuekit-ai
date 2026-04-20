import os
import toml
from pathlib import Path
import sys

PIPELINE_VERSION = "1.0.1"

# Valuation defaults (used by app.py and displayed in Pipeline Configuration)
DEFAULT_DISCOUNT_RATE = 0.15   # 15%
DEFAULT_MARGIN_OF_SAFETY = 0.50  # 50%
DEFAULT_BASE_YEAR = 2025

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))


class RAGConfig:
    """Configuration for RAG system with Claude and Voyage AI"""

    # Try to load from secrets.toml or environment variables
    @staticmethod
    def _load_secret(key_path: list, env_var: str):
        """Load secret from secrets.toml or environment variable"""
        # First try environment variable
        env_value = os.getenv(env_var)
        if env_value:
            return env_value

        # Then try secrets.toml
        try:
            # Look for secrets.toml in common locations
            possible_paths = [
                Path(".streamlit/secrets.toml"),
                Path("../.streamlit/secrets.toml"),
                Path("../../.streamlit/secrets.toml"),
            ]

            for secrets_path in possible_paths:
                if secrets_path.exists():
                    secrets = toml.load(secrets_path)
                    # Navigate nested keys (e.g., ["anthropic", "api_key"])
                    value = secrets
                    for key in key_path:
                        value = value.get(key, {})
                    if isinstance(value, str):
                        return value
        except Exception as e:
            print(f"Warning: Could not load from secrets.toml: {e}")

        return None

    @staticmethod
    def _load_numeric(key_path: list, env_var: str, default, cast):
        """Load a numeric value from secrets.toml or environment variable.

        Falls back to `default` if neither source provides a parseable value.
        `cast` is the type constructor (float or int).
        """
        # First try environment variable
        env_value = os.getenv(env_var)
        if env_value:
            try:
                return cast(env_value)
            except (ValueError, TypeError):
                print(f"Warning: Could not parse {env_var}={env_value!r} as {cast.__name__}, using default {default}")

        # Then try secrets.toml
        try:
            possible_paths = [
                Path(".streamlit/secrets.toml"),
                Path("../.streamlit/secrets.toml"),
                Path("../../.streamlit/secrets.toml"),
            ]
            for secrets_path in possible_paths:
                if secrets_path.exists():
                    secrets = toml.load(secrets_path)
                    value = secrets
                    for key in key_path:
                        value = value.get(key, {})
                    if isinstance(value, (int, float, str)):
                        try:
                            return cast(value)
                        except (ValueError, TypeError):
                            print(f"Warning: Could not parse secrets.toml {key_path}={value!r} as {cast.__name__}, using default {default}")
        except Exception as e:
            print(f"Warning: Could not load from secrets.toml: {e}")

        return default

    # API Keys
    ANTHROPIC_API_KEY = _load_secret.__func__(
        ["anthropic", "api_key"], "ANTHROPIC_API_KEY"
    )
    VOYAGE_API_KEY = _load_secret.__func__(["voyage", "api_key"], "VOYAGE_API_KEY")

    # ChromaDB Settings
    CHROMA_PERSIST_DIR = str(
        Path(__file__).resolve().parent.parent.parent.parent / "data" / "chroma_db"
    )

    COLLECTION_NAME = "valuekit_financial_data"

    # Chunking Settings
    CHUNK_SIZE = 1000
    CHUNK_OVERLAP = 100

    # Model Settings
    EMBEDDING_MODEL = "voyage-finance-2"  # Spezialisiert auf Financial Data

    # LLM model in LiteLLM provider/model format.
    # Override via secrets.toml [llm] model or LLM_MODEL env var.
    # Examples:
    #   "anthropic/claude-sonnet-4-6"  (default)
    #   "openai/gpt-4o"
    LLM_MODEL = (
        _load_secret.__func__(["llm", "model"], "LLM_MODEL")
        or "anthropic/claude-sonnet-4-6"
    )

    LLM_TEMPERATURE = 0.0  # Für präzise quantitative Analysen

    # Monthly spend cap enforced by LiteLLM (USD).
    # Override via secrets.toml [llm] monthly_budget_usd or LLM_MONTHLY_BUDGET_USD env var.
    MONTHLY_BUDGET_USD = _load_numeric.__func__(
        ["llm", "monthly_budget_usd"], "LLM_MONTHLY_BUDGET_USD", 50.0, float
    )

    # LLM API key — loaded from [llm][api_key] / LLM_API_KEY first, then falls
    # back to the provider-specific key derived from the LLM_MODEL prefix.
    LLM_API_KEY = _load_secret.__func__(["llm", "api_key"], "LLM_API_KEY") or (
        _load_secret.__func__(["anthropic", "api_key"], "ANTHROPIC_API_KEY")
        if LLM_MODEL.startswith("anthropic/")
        else os.getenv("OPENAI_API_KEY")
    )

    # Retrieval Settings
    TOP_K_RESULTS = 8  # Anzahl relevanter Chunks für Context

    # Generation Settings
    # Override via secrets.toml [llm] max_tokens or LLM_MAX_TOKENS env var.
    LLM_MAX_TOKENS = _load_numeric.__func__(
        ["llm", "max_tokens"], "LLM_MAX_TOKENS", 4096, int
    )

    @classmethod
    def validate(cls):
        """Validate that required API keys are present"""
        errors = []
        if not cls.LLM_API_KEY:
            errors.append(
                "LLM_API_KEY not found — add [llm] api_key to secrets.toml "
                "or set LLM_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY env var"
            )
        if not cls.VOYAGE_API_KEY:
            errors.append("VOYAGE_API_KEY not found in secrets.toml or environment")

        if errors:
            raise ValueError(
                "Missing API keys:\n"
                + "\n".join(f"  - {e}" for e in errors)
                + "\n\nAdd them to .streamlit/secrets.toml or set as environment variables"
            )

        return True
