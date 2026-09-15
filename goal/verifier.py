"""
goal/verifier.py — Task Result Verification for IRIS Goal Engine.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from goal.models import ErrorClassification, Task


@dataclass
class VerificationResult:
    success: bool
    reason: str
    needs_retry: bool = False
    error_type: ErrorClassification = ErrorClassification.NON_RECOVERABLE
    requires_user_confirmation: bool = False
    confirmation_details: Optional[Dict[str, Any]] = None
    next_action_required: bool = False

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "reason": self.reason,
            "needs_retry": self.needs_retry,
            "error_type": self.error_type.value,
            "requires_user_confirmation": self.requires_user_confirmation,
            "confirmation_details": self.confirmation_details,
            "next_action_required": self.next_action_required,
        }


# Keywords that strongly indicate recoverable transient failures
_RECOVERABLE_PATTERNS = [
    r"timed? out",
    r"connection (refused|reset|error|lost)",
    r"temporary failure",
    r"rate limit",
    r"429",
    r"503 service unavailable",
    r"network unreachable",
    r"resource temporarily unavailable",
]

# Keywords that indicate human intervention or permission issues
_USER_INTERVENTION_PATTERNS = [
    r"permission denied",
    r"access denied",
    r"unauthorized",
    r"401",
    r"403",
    r"api key missing",
    r"authentication required",
    r"\[confirmation_pending\]",
]

# Dangerous actions requiring confirmation
_DESTRUCTIVE_ACTIONS = {
    "shutdown_iris",
    "toggle_wifi",
    "delete_file",
    "delete_folder",
    "deploy",
    "push_code",
    "format_disk",
    "kill_process",
}


class TaskVerifier:
    def verify(
        self,
        task: Task,
        raw_output: Any,
        raw_error: Optional[Exception | str] = None,
    ) -> VerificationResult:
        """Verify whether a task execution actually achieved its objective."""
        # 1. Check for explicit exception / error object
        if raw_error is not None:
            err_str = str(raw_error)
            err_type = self._classify_error_string(err_str)
            return VerificationResult(
                success=False,
                reason=f"Task threw exception: {err_str}",
                needs_retry=(err_type == ErrorClassification.RECOVERABLE and task.retry_count < task.max_retries),
                error_type=err_type,
            )

        output_str = str(raw_output or "").strip()

        # 2. Check for confirmation pending signal
        if "[CONFIRMATION_PENDING]" in output_str or task.assigned_action in _DESTRUCTIVE_ACTIONS:
            return VerificationResult(
                success=False,
                reason="Task requires explicit user confirmation before executing destructive/irreversible action.",
                needs_retry=False,
                error_type=ErrorClassification.USER_INTERVENTION,
                requires_user_confirmation=True,
                confirmation_details={
                    "action": task.assigned_action,
                    "parameters": task.parameters,
                    "prompt": output_str,
                },
            )

        # 3. Check for specific failure indicators in tool output
        lowered = output_str.lower()
        if lowered.startswith("action '") and "is not available" in lowered:
            return VerificationResult(
                success=False,
                reason=output_str,
                needs_retry=False,
                error_type=ErrorClassification.NON_RECOVERABLE,
            )

        if lowered.startswith("unknown tool:"):
            return VerificationResult(
                success=False,
                reason=output_str,
                needs_retry=False,
                error_type=ErrorClassification.NON_RECOVERABLE,
            )

        if any(re.search(pat, lowered) for pat in _RECOVERABLE_PATTERNS):
            return VerificationResult(
                success=False,
                reason=f"Transient failure detected in output: {output_str[:200]}",
                needs_retry=(task.retry_count < task.max_retries),
                error_type=ErrorClassification.RECOVERABLE,
            )

        if any(re.search(pat, lowered) for pat in _USER_INTERVENTION_PATTERNS):
            return VerificationResult(
                success=False,
                reason=f"User intervention or credentials required: {output_str[:200]}",
                needs_retry=False,
                error_type=ErrorClassification.USER_INTERVENTION,
                requires_user_confirmation=True,
            )

        # Check for explicit failure prefixes from actions
        if output_str.startswith("Search failed") or output_str.startswith("Error:"):
            return VerificationResult(
                success=False,
                reason=output_str,
                needs_retry=False,
                error_type=ErrorClassification.NON_RECOVERABLE,
            )

        # 4. Valid output
        return VerificationResult(
            success=True,
            reason="Task execution completed and verified successfully.",
            needs_retry=False,
        )

    def _classify_error_string(self, err: str) -> ErrorClassification:
        low = err.lower()
        if any(re.search(pat, low) for pat in _RECOVERABLE_PATTERNS):
            return ErrorClassification.RECOVERABLE
        if any(re.search(pat, low) for pat in _USER_INTERVENTION_PATTERNS):
            return ErrorClassification.USER_INTERVENTION
        return ErrorClassification.NON_RECOVERABLE
