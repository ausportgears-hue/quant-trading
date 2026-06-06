"""v7.0 日志系统 — structlog + rich"""
import logging
import structlog
from rich.logging import RichHandler
from pathlib import Path


def setup_logger(log_dir: str = "data/logs", level: str = "INFO") -> structlog.BoundLogger:
    """初始化结构化日志"""
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    stream_handler = RichHandler(
        rich_tracebacks=True,
        show_path=False,
        markup=True,
    )
    stream_handler.setLevel(getattr(logging, level.upper()))

    file_handler = logging.FileHandler(f"{log_dir}/trading.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    ))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(logging.DEBUG)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    return structlog.get_logger()


log = setup_logger()
