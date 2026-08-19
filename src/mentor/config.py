"""Local configuration with no dependency beyond the Python standard library."""

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigError(RuntimeError):
    """Raised when the private application cannot start safely."""


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str = "gpt-5.6-sol"


def load_config(environment: Mapping[str, str], dotenv_path: Path) -> Config:
    """Read an API key from the environment, falling back to a local .env file."""
    api_key = environment.get("OPENAI_API_KEY") or _read_dotenv(dotenv_path).get(
        "OPENAI_API_KEY", ""
    )
    if not api_key.strip():
        raise ConfigError("OPENAI_API_KEY is required. Copy .env.example to .env first.")
    return Config(api_key=api_key.strip())


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
