"""Small parallel-execution helper for the batch SSE generators.

The AI batch operations (Generate Markdown, Check Questions, Auto-tag, PDF
detection) all share the same shape: a list of independent work items, each
needing one LLM round-trip plus some DB / filesystem writes. For **cloud**
endpoints that tolerate concurrent requests we can fan those round-trips out
across a thread pool instead of waiting for them one at a time.

``run_parallel`` is deliberately tiny: it yields per-item results in
*completion order* so the calling generator keeps full control of counters,
ordering-sensitive bookkeeping and the SSE event shape. Workers run inside
``app.app_context()`` so Flask-SQLAlchemy hands each thread its own
thread-local scoped session.

Cancellation matches the sequential generators: setting ``cancel`` makes any
not-yet-started worker short-circuit (returning the :data:`CANCELLED`
sentinel); in-flight requests finish naturally. ``max_workers <= 1`` falls
back to a plain sequential loop (so the same call site works for local
endpoints with no thread-pool overhead).
"""
import concurrent.futures


# Sentinel returned by a worker that found the cancel flag already set before
# it started. Consumers skip these (no event, no counter increment).
CANCELLED = object()


def run_parallel(app, cancel, items, worker_fn, max_workers):
    """Run ``worker_fn(item)`` over ``items``, yielding result dicts.

    Each yielded dict is ``{'item': item, 'result': value, 'error': exc}``
    where exactly one of ``result`` / ``error`` is meaningful. ``result`` may
    be :data:`CANCELLED` when the run was cancelled before that item started.

    - ``max_workers <= 1`` → sequential loop in the caller's context.
    - otherwise a ``ThreadPoolExecutor`` of that size; results are yielded in
      completion order via :func:`concurrent.futures.as_completed`.
    """
    items = list(items)

    if not max_workers or max_workers <= 1:
        for it in items:
            if cancel is not None and cancel.is_set():
                break
            try:
                yield {'item': it, 'result': worker_fn(it), 'error': None}
            except Exception as e:  # noqa: BLE001 - surfaced to the consumer
                yield {'item': it, 'result': None, 'error': e}
        return

    def _wrapped(it):
        # Skip cheaply if cancelled before this task got a worker.
        if cancel is not None and cancel.is_set():
            return CANCELLED
        with app.app_context():
            return worker_fn(it)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        fut_to_item = {executor.submit(_wrapped, it): it for it in items}
        for fut in concurrent.futures.as_completed(fut_to_item):
            it = fut_to_item[fut]
            try:
                yield {'item': it, 'result': fut.result(), 'error': None}
            except Exception as e:  # noqa: BLE001 - surfaced to the consumer
                yield {'item': it, 'result': None, 'error': e}
    finally:
        executor.shutdown(wait=True)
