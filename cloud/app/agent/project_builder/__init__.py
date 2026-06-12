"""Project Builder Agent — Sandy as a software engineer.


SA1: repo_grep
SA2: repo_view_lines
SA3: repo_apply_patch
SA4: github_create_branch
SA5: github_ci_status
SA8: Project Builder
SA9: Task queue + resume state

All operations are GitHub-API-only — no shell access on the server.
Production is never affected without a manual PR merge.
"""

# NOTE: لا نُعيد تصدير `repo_grep` (الـ function) من هنا — اسمها يتطابق مع اسم
# الـ submodule فيحدث shadowing ويكسر `from app.agent.project_builder import repo_grep`
# في الـ consumers (orchestrator + project_builder_tools) — يتوقعون الـ submodule.
from app.agent.project_builder.repo_view import repo_view_lines, invalidate_file_cache
from app.agent.project_builder.repo_patch import repo_apply_patch
from app.agent.project_builder.branch_ops import github_create_branch
from app.agent.project_builder.ci_status import github_ci_status, wait_for_ci

__all__ = [
    "repo_view_lines",
    "invalidate_file_cache",
    "repo_apply_patch",
    "github_create_branch",
    "github_ci_status",
    "wait_for_ci",
]
