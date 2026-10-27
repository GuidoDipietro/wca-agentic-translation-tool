"""Bound provider streams without truncating or accepting partial responses."""
import time

from core import ReviewError

LOCAL_REQUEST_TIMEOUT = 15
AI_IDLE_TIMEOUT = 180
AI_STREAM_DEADLINE = 600
MAX_AI_STREAM_BYTES = 32 * 1024 * 1024
MAX_AI_LINE_BYTES = 8 * 1024 * 1024
MAX_PROVIDER_JSON_BYTES = 4 * 1024 * 1024


def sse_lines(response):
    deadline = time.monotonic() + AI_STREAM_DEADLINE
    received = 0
    while True:
        if time.monotonic() >= deadline:
            raise ReviewError('The AI stream exceeded its time limit. Validated batches are preserved.')
        line = response.readline(MAX_AI_LINE_BYTES + 1)
        if not line:
            return
        received += len(line)
        if len(line) > MAX_AI_LINE_BYTES or received > MAX_AI_STREAM_BYTES:
            raise ReviewError('The AI stream exceeded its size limit. Validated batches are preserved.')
        if time.monotonic() >= deadline:
            raise ReviewError('The AI stream exceeded its time limit. Validated batches are preserved.')
        yield line
