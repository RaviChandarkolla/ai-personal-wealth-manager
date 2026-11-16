import logging
import uuid
from contextvars import ContextVar
from pathlib import Path

_run_id: ContextVar[str] = ContextVar("run_id", default="-")


def new_run_id() -> str:
    rid = uuid.uuid4().hex[:12]
    _run_id.set(rid)
    return rid


def get_run_id() -> str:
    return _run_id.get()


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get()
        return True


def setup_logging(
    level: str = "INFO",
    log_dir: Path | None = Path("logs"),
    log_file: str = "wealth_advisor.log",
) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s run=%(run_id)s %(name)s: %(message)s")
    run_filter = _RunIdFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.addFilter(run_filter)
    stream_handler.setFormatter(fmt)
    handlers: list[logging.Handler] = [stream_handler]

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / log_file, encoding="utf-8")
        file_handler.addFilter(run_filter)
        file_handler.setFormatter(fmt)
        handlers.append(file_handler)

        for uv_name in ("uvicorn", "uvicorn.access"):
            uv_logger = logging.getLogger(uv_name)
            uv_logger.handlers = [
                h for h in uv_logger.handlers if not isinstance(h, logging.FileHandler)
            ]
            uv_logger.addHandler(file_handler)

    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level)

