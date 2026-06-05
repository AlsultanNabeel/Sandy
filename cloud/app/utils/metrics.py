"""Simple Prometheus metrics wrapper with safe no-op fallback.

The module keeps the application code free from Prometheus-specific details
and makes metrics calls safe when the dependency is absent during tests.
"""

_ENABLED = True
try:
    from prometheus_client import Counter, Histogram, generate_latest
    from prometheus_client import CONTENT_TYPE_LATEST
except Exception:
    _ENABLED = False


def _noop(*_args, **_kwargs):
    return None


if _ENABLED:
    # HTTP / webhook metrics
    telegram_webhook_ingress_total = Counter(
        "sandy_telegram_webhook_ingress_total", "Total telegram webhook ingresses"
    )
    telegram_webhook_dedup_total = Counter(
        "sandy_telegram_webhook_dedup_total", "Total telegram webhook dedup hits"
    )
    telegram_webhook_processing_seconds = Histogram(
        "sandy_telegram_webhook_processing_seconds", "Webhook processing latency"
    )

    # LLM metrics
    llm_completion_seconds = Histogram(
        "sandy_llm_completion_seconds", "LLM chat completion latency"
    )
    llm_completion_success_total = Counter(
        "sandy_llm_completion_success_total", "Successful LLM completions"
    )
    llm_completion_failure_total = Counter(
        "sandy_llm_completion_failure_total", "Failed LLM completions"
    )

    # Self-Coding metrics
    agent_resume_state_saved_total = Counter(
        "sandy_agent_resume_state_saved_total", "Times agent resume_state was saved"
    )
    agent_resume_signal_total = Counter(
        "sandy_agent_resume_signal_total", "Times owner signalled resume"
    )
    agent_resume_wait_seconds = Histogram(
        "sandy_agent_resume_wait_seconds", "Time spent waiting for resume"
    )
    agent_resume_wait_resumed_total = Counter(
        "sandy_agent_resume_wait_resumed_total", "Resume waits that completed successfully"
    )
    agent_resume_wait_shutdown_total = Counter(
        "sandy_agent_resume_wait_shutdown_total", "Resume waits interrupted by shutdown"
    )
    agent_resume_wait_timeout_total = Counter(
        "sandy_agent_resume_wait_timeout_total", "Resume waits that expired naturally"
    )

    # Error persistence metrics
    error_log_total = Counter(
        "sandy_error_log_total", "Unhandled errors persisted successfully"
    )
    error_log_failure_total = Counter(
        "sandy_error_log_failure_total", "Unhandled errors that failed to persist"
    )

    # Self-Coding agent metrics
    self_coding_task_total = Counter(
        "sandy_self_coding_task_total",
        "Self-Coding tasks by type + terminal status",
        ["task_type", "status"],
    )
    self_coding_iterations = Histogram(
        "sandy_self_coding_iterations",
        "Agent loop iterations per feature",
        buckets=(1, 3, 5, 8, 12, 16, 20, 25),
    )
    self_coding_tokens_used = Histogram(
        "sandy_self_coding_tokens_used",
        "Total tokens (input+output) per agent run",
        buckets=(10_000, 50_000, 100_000, 200_000, 400_000, 600_000, 800_000),
    )
    self_coding_tool_calls_total = Counter(
        "sandy_self_coding_tool_calls_total",
        "Tool calls from the Self-Coding agent",
        ["tool"],
    )
    self_coding_duration_seconds = Histogram(
        "sandy_self_coding_duration_seconds",
        "End-to-end duration of a Self-Coding task",
        buckets=(30, 60, 120, 300, 600, 1200, 1800, 3600),
    )
    self_coding_ci_outcome_total = Counter(
        "sandy_self_coding_ci_outcome_total",
        "CI outcome counts at the end of a Self-Coding task",
        ["result"],
    )

    def inc_webhook_ingress():
        telegram_webhook_ingress_total.inc()

    def inc_webhook_dedup():
        telegram_webhook_dedup_total.inc()

    def observe_webhook_duration(sec: float):
        telegram_webhook_processing_seconds.observe(sec)

    def observe_llm_completion(sec: float):
        llm_completion_seconds.observe(sec)

    def inc_llm_completion_success():
        llm_completion_success_total.inc()

    def inc_llm_completion_failure():
        llm_completion_failure_total.inc()

    def inc_agent_resume_saved():
        agent_resume_state_saved_total.inc()

    def inc_agent_resume_signal():
        agent_resume_signal_total.inc()

    def observe_resume_wait(sec: float):
        agent_resume_wait_seconds.observe(sec)

    def inc_resume_wait_resumed():
        agent_resume_wait_resumed_total.inc()

    def inc_resume_wait_shutdown():
        agent_resume_wait_shutdown_total.inc()

    def inc_resume_wait_timeout():
        agent_resume_wait_timeout_total.inc()

    def inc_error_log_success():
        error_log_total.inc()

    def inc_error_log_failure():
        error_log_failure_total.inc()

    def inc_self_coding_task(task_type: str, status: str):
        self_coding_task_total.labels(task_type=task_type or "unknown", status=status or "unknown").inc()

    def observe_self_coding_iterations(n: int):
        self_coding_iterations.observe(int(n or 0))

    def observe_self_coding_tokens(n: int):
        self_coding_tokens_used.observe(int(n or 0))

    def inc_self_coding_tool_call(tool: str):
        self_coding_tool_calls_total.labels(tool=tool or "unknown").inc()

    def observe_self_coding_duration(sec: float):
        self_coding_duration_seconds.observe(float(sec or 0))

    def inc_self_coding_ci_outcome(result: str):
        self_coding_ci_outcome_total.labels(result=result or "unknown").inc()

    def metrics_wsgi() -> (bytes, str):
        return generate_latest(), CONTENT_TYPE_LATEST

else:
    # No-op shim
    inc_webhook_ingress = _noop
    inc_webhook_dedup = _noop
    observe_webhook_duration = _noop
    observe_llm_completion = _noop
    inc_llm_completion_success = _noop
    inc_llm_completion_failure = _noop
    inc_agent_resume_saved = _noop
    inc_agent_resume_signal = _noop
    observe_resume_wait = _noop
    inc_resume_wait_resumed = _noop
    inc_resume_wait_shutdown = _noop
    inc_resume_wait_timeout = _noop
    inc_error_log_success = _noop
    inc_error_log_failure = _noop
    inc_self_coding_task = _noop
    observe_self_coding_iterations = _noop
    observe_self_coding_tokens = _noop
    inc_self_coding_tool_call = _noop
    observe_self_coding_duration = _noop
    inc_self_coding_ci_outcome = _noop

    def metrics_wsgi() -> (bytes, str):
        return b"", "text/plain"
