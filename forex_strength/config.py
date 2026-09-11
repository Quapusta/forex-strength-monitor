from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_PAIRS = "EURUSD,GBPUSD,AUDUSD,NZDUSD,USDJPY,USDCHF,USDCAD"


def load_dotenv(path: str = ".env") -> None:
    """Load a simple local .env file without adding a runtime dependency."""
    from pathlib import Path

    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    refresh_token: str
    database_url: str
    pair_symbols: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> "Settings":
        load_dotenv()
        required = (
            "CTRADER_CLIENT_ID",
            "CTRADER_CLIENT_SECRET",
            "CTRADER_REFRESH_TOKEN",
            "DATABASE_URL",
        )
        missing = [name for name in required if not os.environ.get(name, "").strip()]
        if missing:
            raise RuntimeError(f"Missing configuration: {', '.join(missing)}")
        pairs = tuple(
            value.strip().upper()
            for value in os.environ.get("FOREX_PAIRS", DEFAULT_PAIRS).split(",")
            if value.strip()
        )
        if not pairs:
            raise RuntimeError("FOREX_PAIRS must contain at least one currency pair.")
        return cls(
            os.environ["CTRADER_CLIENT_ID"].strip(),
            os.environ["CTRADER_CLIENT_SECRET"].strip(),
            os.environ["CTRADER_REFRESH_TOKEN"].strip(),
            os.environ["DATABASE_URL"].strip(),
            pairs,
        )
