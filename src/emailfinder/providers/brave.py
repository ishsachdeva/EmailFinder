import os
from urllib.parse import urlparse

import httpx

from emailfinder.domain.errors import EmailFinderError, ErrorCategory
from emailfinder.domain.phase2 import DiscoveredCompany
from emailfinder.persistence.database import normalize_domain


BLOCKED_HOSTS = {"linkedin.com", "facebook.com", "instagram.com", "x.com", "youtube.com", "wikipedia.org", "crunchbase.com", "reuters.com", "bloomberg.com", "indeed.com", "glassdoor.com"}


class BraveCompanyDiscoveryProvider:
    """One official search provider. Results become candidates, never evidence of truth by themselves."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key or os.getenv("SEARCH_API_KEY", "")
        self.base_url = base_url or os.getenv("SEARCH_BASE_URL", "https://api.search.brave.com/res/v1/web/search")
        self.client = client or httpx.Client(timeout=15, follow_redirects=True)

    def discover(self, brief) -> list[DiscoveredCompany]:
        if not self.api_key:
            raise EmailFinderError(ErrorCategory.CONFIG_ERROR, "SEARCH_API_KEY is required for REAL mode (Brave Search API)")
        queries = [f'"{industry}" company {geography} official website' for geography in brief.icp.target_geographies for industry in brief.icp.target_industries]
        candidates: dict[str, DiscoveredCompany] = {}
        for query in queries:
            try:
                response = self.client.get(self.base_url, params={"q": query, "count": 20, "result_filter": "web", "text_decorations": "false"}, headers={"Accept": "application/json", "X-Subscription-Token": self.api_key})
                if response.status_code == 429:
                    raise EmailFinderError(ErrorCategory.RATE_LIMITED, "Brave Search quota/rate limit reached")
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise EmailFinderError(ErrorCategory.NETWORK_ERROR, "Brave Search timed out") from exc
            except httpx.HTTPError as exc:
                raise EmailFinderError(ErrorCategory.PROVIDER_ERROR, f"Brave Search unavailable: {exc}") from exc
            for item in response.json().get("web", {}).get("results", []):
                parsed = urlparse(item.get("url", ""))
                domain = normalize_domain(parsed.netloc)
                if not domain or any(domain == h or domain.endswith("." + h) for h in BLOCKED_HOSTS):
                    continue
                title = item.get("title", "").strip()
                name = title.split("|")[0].split("-")[0].strip()
                try:
                    candidate = DiscoveredCompany(name=name, domain=domain, website=f"{parsed.scheme or 'https'}://{parsed.netloc}", discovery_url=item["url"], discovery_title=title, discovery_excerpt=item.get("description", ""))
                except (KeyError, ValueError):
                    continue
                candidates.setdefault(domain, candidate)
                if len(candidates) >= brief.prospecting.maximum_candidates_to_process:
                    return list(candidates.values())
        return list(candidates.values())
