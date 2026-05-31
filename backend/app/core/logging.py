"""Logging configuration with request/tenant correlation (O3).

The whole app logs through stdlib ``logging.getLogger(__name__)``. Before this, those
logs had no correlation — you couldn't tie a warning to a request or a tenant. Here we:

- keep contextvars for request_id / tenant_id / user_id,
- inject them into every LogRecord via a filter (so existing ``logger.*`` calls gain
  the fields with no change at the call sites),
- format them into each line.

The request_id is set by RequestContextMiddleware; tenant_id/user_id are set by the
get_current_user dependency once the caller is known.
"""

import contextvars
import logging

from app.core.config import settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
tenant_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("tenant_id", default="-")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")


def bind_tenant(tenant_id: str, user_id: str) -> None:
    """Bind tenant/user to the logging context for the current request."""
    tenant_id_var.set(str(tenant_id))
    user_id_var.set(str(user_id))


class CorrelationFilter(logging.Filter):
    """Attach the current request/tenant/user ids to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.tenant_id = tenant_id_var.get()
        record.user_id = user_id_var.get()
        return True


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(CorrelationFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [req=%(request_id)s tenant=%(tenant_id)s] "
            "%(name)s: %(message)s"
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    # SQLAlchemy echo is very chatty; keep it out of the app log unless debugging.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.app_debug else logging.WARNING
    )
