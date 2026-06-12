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

    # Project Builder metrics
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

    # Project Builder agent metrics
    project_builder_task_total = Counter(
        "sandy_project_builder_task_total",
        "Project Builder tasks by type + terminal status",
        ["task_type", "status"],
    )
    project_builder_iterations = Histogram(
        "sandy_project_builder_iterations",
        "Agent loop iterations per feature",
        buckets=(1, 3, 5, 8, 12, 16, 20, 25),
    )
    project_builder_tokens_used = Histogram(
        "sandy_project_builder_tokens_used",
        "Total tokens (input+output) per agent run",
        buckets=(10_000, 50_000, 100_000, 200_000, 400_000, 600_000, 800_000),
    )
    project_builder_tool_calls_total = Counter(
        "sandy_project_builder_tool_calls_total",
        "Tool calls from the Project Builder agent",
        ["tool"],
    )
    project_builder_duration_seconds = Histogram(
        "sandy_project_builder_duration_seconds",
        "End-to-end duration of a Project Builder task",
        buckets=(30, 60, 120, 300, 600, 1200, 1800, 3600),
    )
    project_builder_ci_outcome_total = Counter(
        "sandy_project_builder_ci_outcome_total",
        "CI outcome counts at the end of a Project Builder task",
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

    def inc_project_builder_task(task_type: str, status: str):
        project_builder_task_total.labels(task_type=task_type or "unknown", status=status or "unknown").inc()

    def observe_project_builder_iterations(n: int):
        project_builder_iterations.observe(int(n or 0))

    def observe_project_builder_tokens(n: int):
        project_builder_tokens_used.observe(int(n or 0))

    def inc_project_builder_tool_call(tool: str):
        project_builder_tool_calls_total.labels(tool=tool or "unknown").inc()

    def observe_project_builder_duration(sec: float):
        project_builder_duration_seconds.observe(float(sec or 0))

    def inc_project_builder_ci_outcome(result: str):
        project_builder_ci_outcome_total.labels(result=result or "unknown").inc()

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
    inc_project_builder_task = _noop
    observe_project_builder_iterations = _noop
    observe_project_builder_tokens = _noop
    inc_project_builder_tool_call = _noop
    observe_project_builder_duration = _noop
    inc_project_builder_ci_outcome = _noop

    def metrics_wsgi() -> (bytes, str):
        return b"", "text/plain"
