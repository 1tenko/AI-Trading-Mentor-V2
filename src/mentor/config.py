"""Local configuration with no dependency beyond the Python standard library."""

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


PRODUCTION_RUNTIME_SCOPE = "production"
PILOT_RUNTIME_SCOPE = "pilot"
RUNTIME_SCOPES = frozenset({PRODUCTION_RUNTIME_SCOPE, PILOT_RUNTIME_SCOPE})


class ConfigError(RuntimeError):
    """Raised when the private application cannot start safely."""


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str = "gpt-5.6-sol"
    vector_store_preflight_approved: bool = False
    runtime_scope: str = PRODUCTION_RUNTIME_SCOPE

    def require_vector_store_preflight(self) -> None:
        if not self.vector_store_preflight_approved:
            raise ConfigError(
                "PHASE3_VECTOR_STORE_PREFLIGHT=disposable-approved is required before a live preflight."
            )


def load_config(environment: Mapping[str, str], dotenv_path: Path) -> Config:
    """Read an API key from the environment, falling back to a local .env file."""
    dotenv = _read_dotenv(dotenv_path)
    api_key = environment.get("OPENAI_API_KEY") or dotenv.get("OPENAI_API_KEY", "")
    if not api_key.strip():
        raise ConfigError("OPENAI_API_KEY is required. Copy .env.example to .env first.")
    return Config(
        api_key=api_key.strip(),
        vector_store_preflight_approved=(
            environment.get("PHASE3_VECTOR_STORE_PREFLIGHT")
            or dotenv.get("PHASE3_VECTOR_STORE_PREFLIGHT")
        )
        == "disposable-approved",
    )


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values
