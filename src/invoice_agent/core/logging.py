from __future__ import annotations

import logging
import sys
from types import FrameType
from typing import Any

from loguru import logger

from invoice_agent.core.config import get_config


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame: FrameType | None = logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging() -> None:
    cfg = get_config().observability
    logger.remove()
    logger.add(
        sys.stdout,
        level=cfg.log_level,
        serialize=cfg.json_logs,
        backtrace=False,
        diagnose=False,
    )
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "httpx", "sqlalchemy.engine"):
        logging.getLogger(name).handlers = [_InterceptHandler()]
        logging.getLogger(name).propagate = False


def bind_run(run_id: Any) -> Any:
    return logger.bind(run_id=str(run_id))
