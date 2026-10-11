"""Configuration management for BlackSmith.

Loads settings from environment variables and .env file with validation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv, find_dotenv


@dataclass(frozen=True)
class AppConfig:
    """Application configuration container."""
    database_url: str
    groq_api_key: str
    groq_model: str = "llama-3.3-70b-versatile"
    allow_seed: bool = False
    log_level: str = "INFO"
    max_tool_iterations: int = 10
    debug_graph: bool = False

    @classmethod
    def load(cls, env_path: Optional[str | Path] = None) -> AppConfig:
        """Load and validate configuration from environment and .env file."""
        if env_path:
            load_dotenv(dotenv_path=env_path, override=True)
        else:
            dotenv_file = find_dotenv(usecwd=True)
            if dotenv_file:
                load_dotenv(dotenv_file, override=False)
            else:
                load_dotenv()

        missing: list[str] = []

        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            missing.append("DATABASE_URL")

        groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not groq_api_key:
            missing.append("GROQ_API_KEY")

        if missing:
            missing_vars = ", ".join(missing)
            raise ValueError(
                f"Missing required configuration variables: {missing_vars}.\n"
                f"Please ensure these variables are defined in your .env file or system environment.\n"
                f"Reference .env.example for required variables and format."
            )

        groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
        allow_seed_str = os.getenv("ALLOW_SEED", "false").strip().lower()
        allow_seed = allow_seed_str in ("true", "1", "yes", "t")

        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()

        try:
            max_tool_iterations = int(os.getenv("MAX_TOOL_ITERATIONS", "10").strip())
        except ValueError:
            max_tool_iterations = 10

        debug_graph_str = os.getenv("DEBUG_GRAPH", "false").strip().lower()
        debug_graph = debug_graph_str in ("true", "1", "yes", "t")

        return cls(
            database_url=database_url,
            groq_api_key=groq_api_key,
            groq_model=groq_model,
            allow_seed=allow_seed,
            log_level=log_level,
            max_tool_iterations=max_tool_iterations,
            debug_graph=debug_graph,
        )


_config_instance: Optional[AppConfig] = None


def get_config(reload: bool = False, env_path: Optional[str | Path] = None) -> AppConfig:
    """Retrieve or create singleton application configuration."""
    global _config_instance
    if _config_instance is None or reload:
        _config_instance = AppConfig.load(env_path=env_path)
    return _config_instance
