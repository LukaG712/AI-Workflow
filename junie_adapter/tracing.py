"""OpenTelemetry tracing for Slinker.

Mirrors Greenie's tracing.py but configured for the Slinker service name.
"""

import os

from fastapi import FastAPI, Request, Response
from opentelemetry import trace
from opentelemetry.propagate import inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio

_tracer: trace.Tracer | None = None


def get_tracer() -> trace.Tracer:
    return _tracer or trace.get_tracer("Slinker")


def setup_tracing(app: FastAPI | None = None) -> TracerProvider:
    global _tracer

    service_name = os.environ.get("OTEL_SERVICE_NAME", "Slinker")
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    sample_ratio = float(os.environ.get("OTEL_TRACES_SAMPLE_RATIO", "1.0"))

    resource = Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
    })

    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    sampler = ParentBasedTraceIdRatio(sample_ratio)
    provider = TracerProvider(resource=resource, sampler=sampler)
    trace.set_tracer_provider(provider)

    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    # Auto-instrument FastAPI
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app) if app else FastAPIInstrumentor.instrument()
    except ImportError:
        pass

    # Auto-instrument HTTPX
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor.instrument()
    except ImportError:
        pass

    if app:
        @app.middleware("http")
        async def trace_middleware(request: Request, call_next: callable) -> Response:
            span_name = f"{request.method} {request.url.path}"
            with get_tracer().start_as_current_span(span_name) as span:
                span.set_attribute("http.method", request.method)
                span.set_attribute("http.url", str(request.url))
                headers = dict(request.headers)
                inject(headers)
                response = await call_next(request)
                span.set_attribute("http.status_code", response.status_code)
            return response

    _tracer = get_tracer()
    return provider
