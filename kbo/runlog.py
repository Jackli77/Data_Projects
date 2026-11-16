"""Logging setup shared by the pipeline: every line carries the ID of the load it belongs to."""

import logging
from contextvars import ContextVar

logger = logging.getLogger("kbo")

# Short ID of the archive load in progress ("-" outside a load).
run_id_var: ContextVar[str] = ContextVar("kbo_run_id", default="-")


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """Sends the pipeline's log to stderr. Safe to call more than once."""
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.addFilter(_RunIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s [%(run_id)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(level)
