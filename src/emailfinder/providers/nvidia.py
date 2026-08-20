import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

import httpx
from pydantic import ValidationError

from emailfinder.domain.errors import EmailFinderError, ErrorCategory
from emailfinder.domain.phase2 import QualificationOutput


class ValidationFailureCategory(StrEnum):
    INVALID_JSON = "INVALID_JSON"
    JSON_SCHEMA_FAILURE = "JSON_SCHEMA_FAILURE"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    WRONG_FIELD_TYPE = "WRONG_FIELD_TYPE"
    INVALID_ENUM = "INVALID_ENUM"
    UNKNOWN_EVIDENCE_ID = "UNKNOWN_EVIDENCE_ID"
    MISSING_REQUIRED_EVIDENCE = "MISSING_REQUIRED_EVIDENCE"
    DUPLICATE_EVIDENCE_ID = "DUPLICATE_EVIDENCE_ID"
    UNSUPPORTED_NEED_HYPOTHESIS = "UNSUPPORTED_NEED_HYPOTHESIS"
    UNSUPPORTED_FACTUAL_CLAIM = "UNSUPPORTED_FACTUAL_CLAIM"
    SCORE_OUT_OF_RANGE = "SCORE_OUT_OF_RANGE"
    EXTRA_FIELD_NOT_ALLOWED = "EXTRA_FIELD_NOT_ALLOWED"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    OTHER_VALIDATION_ERROR = "OTHER_VALIDATION_ERROR"


@dataclass
class ValidationIssue(Exception):
    category: ValidationFailureCategory
    stage: str
    field: str | None
    reason: str

    def safe_reason(self) -> str:
        return re_safe(self.reason)[:240]


def re_safe(value: str) -> str:
    return " ".join(str(value).replace("\n", " ").replace("\r", " ").split())


def schema_issue(exc: ValidationError) -> ValidationIssue:
    error = exc.errors()[0]; kind = error.get("type", ""); field = ".".join(map(str, error.get("loc", ()))) or None; message = error.get("msg", kind)
    if "need_hypothesis" in f"{field} {message}".lower(): category = ValidationFailureCategory.UNSUPPORTED_NEED_HYPOTHESIS
    elif kind == "missing": category = ValidationFailureCategory.MISSING_REQUIRED_FIELD
    elif kind == "extra_forbidden": category = ValidationFailureCategory.EXTRA_FIELD_NOT_ALLOWED
    elif kind == "literal_error": category = ValidationFailureCategory.INVALID_ENUM
    elif kind in {"less_than_equal", "greater_than_equal"}: category = ValidationFailureCategory.SCORE_OUT_OF_RANGE
    elif kind in {"list_too_short", "too_short"} and "evidence_ids" in str(field): category = ValidationFailureCategory.MISSING_REQUIRED_EVIDENCE
    elif kind.endswith("_type") or kind.endswith("_parsing"): category = ValidationFailureCategory.WRONG_FIELD_TYPE
    else: category = ValidationFailureCategory.JSON_SCHEMA_FAILURE
    return ValidationIssue(category, "JSON_SCHEMA_VALIDATION", field, message)


class SlidingWindowRateLimiter:
    def __init__(self, requests_per_minute: int = 36, clock=time.monotonic, sleeper=time.sleep):
        self.limit, self.clock, self.sleeper = requests_per_minute, clock, sleeper
        self.calls, self.lock = deque(), threading.Lock()

    def acquire(self):
        with self.lock:
            now = self.clock()
            while self.calls and now - self.calls[0] >= 60: self.calls.popleft()
            if len(self.calls) >= self.limit:
                self.sleeper(max(0, 60 - (now - self.calls[0])))
                now = self.clock()
                while self.calls and now - self.calls[0] >= 60: self.calls.popleft()
            self.calls.append(now)


class NVIDIAReasoningProvider:
    def __init__(self, api_key=None, base_url=None, model=None, client=None, limiter=None, max_attempts=2, sleeper=time.sleep, reasoning_effort=None):
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY", "")
        self.base_url = (base_url or os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")).rstrip("/")
        self.model = model or os.getenv("NVIDIA_MODEL", "")
        # Large reasoning models on NVIDIA commonly exceed 45 seconds; keep the
        # request bounded while matching the known-working local configuration.
        self.client = client or httpx.Client(timeout=240)
        self.limiter = limiter or SlidingWindowRateLimiter(36)
        self.max_attempts, self.sleeper = max_attempts, sleeper
        self.reasoning_effort = reasoning_effort or os.getenv("NVIDIA_REASONING_EFFORT", "")
        self.call_metrics = []

    def qualify_company(self, company, evidence, brief, resolved_facts=None) -> QualificationOutput:
        if not self.api_key or not self.model:
            raise EmailFinderError(ErrorCategory.CONFIG_ERROR, "NVIDIA_API_KEY and NVIDIA_MODEL are required for REAL mode")
        evidence_payload = [{"id": e.id, "source_url": str(e.source_url), "source_quality": e.source_quality, "evidence_type": e.evidence_type, "excerpt": e.excerpt[:1500]} for e in evidence[:5]]
        prompt = {
            "instruction": "Evaluate only the supplied evidence against the Company Brief. Never use prior knowledge or invent facts. Return one JSON object matching the requested contract. Component scores are 0-100. Mark INSUFFICIENT_EVIDENCE when identity, industry, geography, size, or fit cannot be responsibly assessed. A need hypothesis must be cautious and cite only evidence IDs.",
            "company": company.model_dump(mode="json"),
            "resolved_facts": resolved_facts.model_dump(mode="json") if resolved_facts else {},
            "brief": {"target_industries": brief.icp.target_industries, "target_geographies": brief.icp.target_geographies, "employee_min": brief.icp.employee_min, "employee_max": brief.icp.employee_max, "positive_signals": brief.qualification.positive_signals, "excluded_industries": brief.icp.excluded_industries},
            "evidence": evidence_payload,
            "output_contract": "Return qualification, model_score, confidence, positive_signals[{signal,evidence_ids}], negative_signals[{signal,evidence_ids}], reason, need_hypothesis, and need_hypothesis_evidence_ids. For non-ACCEPT, need_hypothesis must be null and its evidence list empty.",
            "boundary": "Resolved facts and hard exclusions are authoritative. Evaluate soft ICP relevance, workflow signals, and cautious need plausibility; do not override resolved facts.",
        }
        last_error = None; last_issue = None; call_started = time.monotonic()
        for attempt in range(self.max_attempts):
            self.limiter.acquire()
            try:
                attempt_prompt = dict(prompt)
                if last_issue: attempt_prompt["repair"] = f"Previous output failed {last_issue.category}: {last_issue.field or 'response'} — {last_issue.safe_reason()}. Correct only that structural defect; retain the same strict schema."
                payload = {"model": self.model, "temperature": 0.1, "stream": False, "messages": [{"role": "system", "content": "You are an evidence-constrained B2B company classifier. Output valid JSON only."}, {"role": "user", "content": json.dumps(attempt_prompt)}]}
                if self.reasoning_effort and "gpt-oss" in self.model: payload["reasoning_effort"] = self.reasoning_effort
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "company_qualification", "strict": True, "schema": QualificationOutput.model_json_schema()}}
                response = self.client.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json=payload)
                if response.status_code == 429:
                    raise EmailFinderError(ErrorCategory.RATE_LIMITED, "NVIDIA rate limit reached")
                response.raise_for_status()
                try: content = response.json()["choices"][0]["message"]["content"].strip()
                except (KeyError, TypeError, AttributeError) as exc: raise ValidationIssue(ValidationFailureCategory.EMPTY_RESPONSE, "RESPONSE_BODY", "choices[0].message.content", "provider response contained no message content") from exc
                if not content: raise ValidationIssue(ValidationFailureCategory.EMPTY_RESPONSE, "RESPONSE_BODY", "content", "empty model response")
                if content.startswith("```"): content = content.split("\n", 1)[1].rsplit("```", 1)[0]
                try: parsed = json.loads(content)
                except json.JSONDecodeError as exc: raise ValidationIssue(ValidationFailureCategory.INVALID_JSON, "JSON_PARSE", None, f"invalid JSON at position {exc.pos}") from exc
                try: output = QualificationOutput.model_validate(parsed)
                except ValidationError as exc: raise schema_issue(exc) from exc
                allowed_ids = {e.id for e in evidence}
                used_ids = [eid for signal in output.positive_signals + output.negative_signals for eid in signal.evidence_ids] + output.need_hypothesis_evidence_ids
                unknown = sorted(set(used_ids) - allowed_ids)
                if unknown: raise ValidationIssue(ValidationFailureCategory.UNKNOWN_EVIDENCE_ID, "EVIDENCE_VALIDATION", "evidence_ids", f"unknown evidence ID {unknown[0]}")
                claim_id_lists = [signal.evidence_ids for signal in output.positive_signals + output.negative_signals] + [output.need_hypothesis_evidence_ids]
                if any(len(ids) != len(set(ids)) for ids in claim_id_lists): raise ValidationIssue(ValidationFailureCategory.DUPLICATE_EVIDENCE_ID, "EVIDENCE_VALIDATION", "evidence_ids", "an evidence ID was repeated within one claim")
                self.call_metrics.append({"company": company.name, "elapsed_seconds": round(time.monotonic() - call_started, 3), "http_attempts": attempt + 1, "success": True, "retry_count": attempt, "json_parsed": True, "schema_validated": True, "evidence_ids_validated": True, "validation_success": True, "failure_category": None, "failure_stage": None, "failure_field": None})
                return output
            except EmailFinderError as exc:
                last_error = exc
                if exc.category != ErrorCategory.RATE_LIMITED or attempt + 1 >= self.max_attempts: raise
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt + 1 >= self.max_attempts:
                    self.call_metrics.append({"company": company.name, "elapsed_seconds": round(time.monotonic() - call_started, 3), "http_attempts": attempt + 1, "success": False, "retry_count": attempt, "validation_success": False, "failure_category": ValidationFailureCategory.PROVIDER_ERROR, "failure_stage": "HTTP", "failure_field": None, "safe_reason": type(exc).__name__})
                    raise EmailFinderError(ErrorCategory.NETWORK_ERROR, "NVIDIA unavailable after bounded retries") from exc
            except ValidationIssue as exc:
                last_error = exc; last_issue = exc
                if attempt + 1 >= self.max_attempts:
                    self.call_metrics.append({"company": company.name, "elapsed_seconds": round(time.monotonic() - call_started, 3), "http_attempts": attempt + 1, "success": False, "retry_count": attempt, "json_parsed": exc.stage != "JSON_PARSE", "schema_validated": exc.stage not in {"JSON_PARSE", "JSON_SCHEMA_VALIDATION"}, "evidence_ids_validated": exc.stage not in {"JSON_PARSE", "JSON_SCHEMA_VALIDATION", "EVIDENCE_VALIDATION"}, "validation_success": False, "failure_category": exc.category, "failure_stage": exc.stage, "failure_field": exc.field, "safe_reason": exc.safe_reason()})
                    raise EmailFinderError(ErrorCategory.INVALID_RESULT, f"NVIDIA validation failed [{exc.category}] at {exc.field or exc.stage}: {exc.safe_reason()}") from exc
            self.sleeper(2 ** attempt)
        raise EmailFinderError(ErrorCategory.PROVIDER_ERROR, f"NVIDIA qualification failed: {last_error}")
