from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from app.settings import settings


class RequestMetrics:
    BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._duration_count: dict[tuple[str, str], int] = defaultdict(int)
        self._buckets: dict[tuple[str, str], list[int]] = {}

    def record(self, method: str, path: str, status: int, duration: float) -> None:
        key = (method, path)
        with self._lock:
            self._requests[(method, path, status)] += 1
            self._duration_sum[key] += duration
            self._duration_count[key] += 1
            counts = self._buckets.setdefault(key, [0] * len(self.BUCKETS))
            for index, bucket in enumerate(self.BUCKETS):
                if duration <= bucket:
                    counts[index] += 1

    @staticmethod
    def _label(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def render(self) -> str:
        lines = [
            "# HELP afterops_http_requests_total Total HTTP requests.",
            "# TYPE afterops_http_requests_total counter",
        ]
        with self._lock:
            for (method, path, status), count in sorted(self._requests.items()):
                lines.append(
                    f'afterops_http_requests_total{{method="{self._label(method)}",'
                    f'path="{self._label(path)}",status="{status}"}} {count}'
                )
            lines += [
                "# HELP afterops_http_request_duration_seconds HTTP request duration.",
                "# TYPE afterops_http_request_duration_seconds histogram",
            ]
            for (method, path), total in sorted(self._duration_sum.items()):
                count = self._duration_count[(method, path)]
                labels = (
                    f'method="{self._label(method)}",path="{self._label(path)}"'
                )
                counts = self._buckets.get((method, path), [0] * len(self.BUCKETS))
                for bucket, bucket_count in zip(self.BUCKETS, counts):
                    lines.append(
                        f"afterops_http_request_duration_seconds_bucket{{{labels},"
                        f'le="{bucket}"}} {bucket_count}'
                    )
                lines.append(
                    f"afterops_http_request_duration_seconds_bucket{{{labels},"
                    f'le="+Inf"}} {count}'
                )
                lines.append(
                    f"afterops_http_request_duration_seconds_sum{{{labels}}} {total:.6f}"
                )
                lines.append(
                    f"afterops_http_request_duration_seconds_count{{{labels}}} {count}"
                )
        return "\n".join(lines) + "\n"


metrics = RequestMetrics()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "request_id",
            "trace_id",
            "method",
            "path",
            "status",
            "duration_ms",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False, default=str)


logger = logging.getLogger("afterops.request")
logger.setLevel(logging.INFO)
logger.propagate = False


def configure_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(message)s %(request_id)s")
        )
    logger.addHandler(handler)


def configure_tracing(app: FastAPI) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        logger.warning("tracing dependencies unavailable: %s", exc)
        return

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=settings.otel_exporter_otlp_endpoint.rstrip("/")
                + "/v1/traces"
            )
        )
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)


def install(app: FastAPI) -> None:
    configure_logging()

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        started = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            elapsed = time.monotonic() - started
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            metrics.record(request.method, path, status, elapsed)
            trace_id = None
            try:
                from opentelemetry import trace

                span_context = trace.get_current_span().get_span_context()
                if span_context.is_valid:
                    trace_id = format(span_context.trace_id, "032x")
            except Exception:  # noqa: BLE001 - tracing must never break requests
                trace_id = None
            logger.info(
                "request",
                extra={
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "method": request.method,
                    "path": path,
                    "status": status,
                    "duration_ms": round(elapsed * 1000, 3),
                },
            )
        response.headers["X-Request-Id"] = request_id
        return response

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> PlainTextResponse:
        return PlainTextResponse(
            metrics.render(), media_type="text/plain; version=0.0.4"
        )
