"""Dockerfile generation from AnalysisResult.

Public surface:

    from shared.docker import generate_dockerfile, UnsupportedFrameworkError

This package is import-safe from anywhere — no I/O at import time, no AWS,
no LLM. Build worker calls it; tests call it; the API can call it for a
"preview the Dockerfile" endpoint without spinning up a build.
"""
from shared.docker.generate import (
    UnsupportedFrameworkError,
    generate_dockerfile,
)

__all__ = ["generate_dockerfile", "UnsupportedFrameworkError"]
