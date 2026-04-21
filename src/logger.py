import logging
import os
from datetime import datetime

from src.config import settings


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger with a console handler (stdout, INFO level).
    Call this once per module: log = get_logger(__name__)

    File logging is NOT set up here — call setup_file_logging() from main.py
    so that test runs never create output log files.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler only
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Suppress noisy third-party loggers
    for noisy in ("httpx", "httpcore", "anthropic", "langsmith", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logger


def setup_file_logging() -> str:
    """
    Add a file handler to the root logger for the current pipeline run.
    Creates output/run_YYYYMMDD_HHMMSS.log. Call once from main.py before
    the pipeline starts. Returns the log file path.
    """
    root = logging.getLogger()
    # Skip if a FileHandler is already attached
    if any(isinstance(h, logging.FileHandler) for h in root.handlers):
        return ""

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    os.makedirs(settings.output_dir, exist_ok=True)
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(settings.output_dir, f"run_{run_ts}.log")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    return log_path


def attach_db_handler(run_id: str) -> None:
    """
    Add a DBLogHandler to the root logger so every log record is also written
    to run_logs in SQLite. Call once after the run row is created in the DB.
    """
    root = logging.getLogger()
    # Avoid adding duplicates if called more than once
    if any(isinstance(h, DBLogHandler) for h in root.handlers):
        return
    handler = DBLogHandler(run_id)
    handler.setLevel(logging.DEBUG)
    root.addHandler(handler)


class DBLogHandler(logging.Handler):
    """Logging handler that inserts each record into the run_logs SQLite table."""

    def __init__(self, run_id: str):
        super().__init__()
        self.run_id = run_id

    def emit(self, record: logging.LogRecord) -> None:
        # Late import to avoid circular dependency at module load time
        from src.db.database import Database
        try:
            Database().add_log_entry(
                run_id=self.run_id,
                level=record.levelname,
                logger=record.name,
                message=record.getMessage(),
            )
        except Exception:
            self.handleError(record)
