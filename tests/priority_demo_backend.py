"""Run an isolated local backend for the manual priority demonstration."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn

from procnanny.api import create_app
from procnanny.config import load_config, save_config

DEMO_DIRECTORY = PROJECT_ROOT / ".priority-demo"
CONFIG_PATH = DEMO_DIRECTORY / "config.yaml"
LOG_PATH = DEMO_DIRECTORY / "procnanny.log"


def main() -> None:
    DEMO_DIRECTORY.mkdir(exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(load_config(), CONFIG_PATH)
    app = create_app(log_path=LOG_PATH, config_path=CONFIG_PATH)
    uvicorn.run(app, host="127.0.0.1", port=8001)


if __name__ == "__main__":
    main()
