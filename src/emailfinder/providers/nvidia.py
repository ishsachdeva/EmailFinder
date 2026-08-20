import json
import os
import threading
import time
from collections import deque

import httpx
from pydantic import ValidationError

from emailfinder.domain.errors import EmailFinderError, ErrorCategory
from emailfinder.domain.phase2 import QualificationOutput


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
            "output_contract": "JSON fields: company_name, domain, industry_assessment, geography_assessment, size_assessment, positive_signals[], negative_signals[], industry_fit(0-100), company_size_fit(0-100), geography_fit(0-100), workflow_signals(0-100), exclusion_risk(0-100), icp_score(0-100), qualification(ACCEPT|REJECT|INSUFFICIENT_EVIDENCE), reason, need_hypothesis, evidence_ids_used[], confidence(0-100). Non-ACCEPT need_hypothesis must be empty.",
            "boundary": "Resolved facts and hard exclusions are authoritative. Evaluate soft ICP relevance, workflow signals, and cautious need plausibility; do not override resolved facts.",
        }
        last_error = None; call_started = time.monotonic()
        for attempt in range(self.max_attempts):
            self.limiter.acquire()
            started = time.monotonic()
            try:
                payload = {"model": self.model, "temperature": 0.1, "stream": False, "messages": [{"role": "system", "content": "You are an evidence-constrained B2B company classifier. Output valid JSON only."}, {"role": "user", "content": json.dumps(prompt)}]}
                if self.reasoning_effort and "gpt-oss" in self.model: payload["reasoning_effort"] = self.reasoning_effort
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "company_qualification", "strict": True, "schema": QualificationOutput.model_json_schema()}}
                response = self.client.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json=payload)
                if response.status_code == 429:
                    raise EmailFinderError(ErrorCategory.RATE_LIMITED, "NVIDIA rate limit reached")
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"].strip()
                if content.startswith("```"): content = content.split("\n", 1)[1].rsplit("```", 1)[0]
                output = QualificationOutput.model_validate_json(content)
                allowed_ids = {e.id for e in evidence}
                if not set(output.evidence_ids_used) <= allowed_ids:
                    raise ValueError("NVIDIA referenced evidence IDs that were not supplied")
                if output.domain.lower() != company.domain.lower():
                    raise ValueError("NVIDIA output domain does not match candidate")
                self.call_metrics.append({"company": company.name, "elapsed_seconds": round(time.monotonic() - call_started, 3), "success": True, "retry_count": attempt, "validation_success": True})
                return output
            except EmailFinderError as exc:
                last_error = exc
                if exc.category != ErrorCategory.RATE_LIMITED or attempt + 1 >= self.max_attempts: raise
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt + 1 >= self.max_attempts:
                    raise EmailFinderError(ErrorCategory.NETWORK_ERROR, "NVIDIA unavailable after bounded retries") from exc
            except (KeyError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                if attempt + 1 >= self.max_attempts:
                    self.call_metrics.append({"company": company.name, "elapsed_seconds": round(time.monotonic() - call_started, 3), "success": False, "retry_count": attempt, "validation_success": False})
                    raise EmailFinderError(ErrorCategory.INVALID_RESULT, f"NVIDIA returned invalid structured output: {exc}") from exc
            self.sleeper(2 ** attempt)
        raise EmailFinderError(ErrorCategory.PROVIDER_ERROR, f"NVIDIA qualification failed: {last_error}")
