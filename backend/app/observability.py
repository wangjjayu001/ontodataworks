"""Request correlation without request bodies, credentials or parameter logs."""
from contextvars import ContextVar
from uuid import uuid4
import logging
import re
import time

trace_id = ContextVar("workbench_trace_id", default="")
log = logging.getLogger("workbench.request")


def install(app):
    @app.middleware("http")
    async def correlate(request, call_next):
        raw = request.headers.get("X-Trace-Id", "")
        trace = raw if re.fullmatch(r"[a-zA-Z0-9_-]{8,64}", raw) else uuid4().hex
        token = trace_id.set(trace); started = time.monotonic()
        try:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace
            log.info("trace=%s method=%s path=%s status=%s duration_ms=%d", trace, request.method, request.url.path, response.status_code, (time.monotonic()-started)*1000)
            return response
        finally: trace_id.reset(token)
