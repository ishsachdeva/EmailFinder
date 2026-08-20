import os
from urllib.parse import urlparse

import httpx

from emailfinder.domain.errors import EmailFinderError, ErrorCategory
from emailfinder.domain.phase2 import DiscoveredCompany
from emailfinder.persistence.database import normalize_domain
from emailfinder.providers.brave import BLOCKED_HOSTS


def build_signal_queries(brief, limit: int = 6) -> list[str]:
    queries = []
    signals = brief.qualification.positive_signals or ["operations"]
    for index, industry in enumerate(brief.icp.target_industries):
        geography = brief.icp.target_geographies[index % len(brief.icp.target_geographies)]
        signal = signals[index % len(signals)]
        queries.append(f'{industry} company "{signal}" {geography}')
        if len(queries) >= limit: break
    return queries


class TavilyCompanyDiscoveryProvider:
    def __init__(self, api_key=None, client=None, raw_limit=20, candidate_limit=None):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        self.client = client or httpx.Client(timeout=30)
        self.raw_limit = raw_limit
        self.candidate_limit = candidate_limit or int(os.getenv("DISCOVERY_LIMIT", "5"))
        self.raw_result_count = self.unique_domain_count = 0

    def discover(self, brief):
        if not self.api_key: raise EmailFinderError(ErrorCategory.CONFIG_ERROR, "TAVILY_API_KEY is required for Tavily discovery")
        pool = {}
        for query in build_signal_queries(brief):
            if self.raw_result_count >= self.raw_limit: break
            try:
                response = self.client.post("https://api.tavily.com/search", json={"api_key": self.api_key, "query": query, "search_depth": "basic", "max_results": min(5, self.raw_limit - self.raw_result_count), "include_answer": False, "include_raw_content": False})
                if response.status_code == 429: raise EmailFinderError(ErrorCategory.RATE_LIMITED, "Tavily free-tier quota/rate limit reached")
                response.raise_for_status()
            except EmailFinderError: raise
            except httpx.HTTPError as exc: raise EmailFinderError(ErrorCategory.PROVIDER_ERROR, f"Tavily discovery unavailable: {exc}") from exc
            results = response.json().get("results", []); self.raw_result_count += len(results)
            for item in results:
                parsed = urlparse(item.get("url", "")); domain = normalize_domain(parsed.netloc)
                if not domain or any(domain == h or domain.endswith("." + h) for h in BLOCKED_HOSTS): continue
                title = item.get("title", "").strip(); content = item.get("content", "").strip()
                if len(title) < 2 or len(content) < 20: continue
                name = title.split("|")[0].split(" - ")[0].strip()
                try: candidate = DiscoveredCompany(name=name, domain=domain, website=f"{parsed.scheme or 'https'}://{parsed.netloc}", discovery_url=item["url"], discovery_title=title, discovery_excerpt=f"SEARCH_DISCOVERY: {content[:1000]}")
                except ValueError: continue
                score = float(item.get("score", 0)); previous = pool.get(domain)
                if previous is None or score > previous[0]: pool[domain] = (score, candidate)
        self.unique_domain_count = len(pool)
        ranked = sorted(pool.values(), key=lambda pair: pair[0], reverse=True)
        return [candidate for _, candidate in ranked[: min(self.candidate_limit, brief.prospecting.maximum_candidates_to_process)]]
