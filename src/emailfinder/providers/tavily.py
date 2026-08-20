import os
import re
from collections import Counter
from enum import StrEnum
from urllib.parse import urlparse

import httpx

from emailfinder.domain.errors import EmailFinderError, ErrorCategory
from emailfinder.domain.phase2 import DiscoveredCompany
from emailfinder.persistence.database import normalize_domain
from emailfinder.providers.brave import BLOCKED_HOSTS


class SearchResultType(StrEnum):
    OFFICIAL_COMPANY = "OFFICIAL_COMPANY"
    ARTICLE = "ARTICLE"
    LISTICLE = "LISTICLE"
    DIRECTORY = "DIRECTORY"
    JOB_POSTING = "JOB_POSTING"
    NEWS = "NEWS"
    VENDOR_CONTENT = "VENDOR_CONTENT"
    UNKNOWN = "UNKNOWN"


NON_TARGET_HOSTS = BLOCKED_HOSTS | {
    "amazon.com", "business.amazon.com", "builtin.com", "clutch.co", "forbes.com",
    "glassdoor.com", "indeed.com", "reuters.com", "bloomberg.com", "yelp.com",
    "yellowpages.com", "zoominfo.com", "ukmarketingmanagement.com",
}
STOP_NAMES = {"United States", "United Kingdom", "Privacy Policy", "Terms Conditions", "Amazon Business", "Built In"}
GENERIC_NAME_TOKENS = {"company", "group", "limited", "services", "solutions", "engineering", "industries", "systems", "construction", "logistics", "international", "official", "website"}


def _host(url: str) -> str:
    return normalize_domain(urlparse(url).netloc)


def _blocked(host: str) -> bool:
    return any(host == item or host.endswith("." + item) for item in NON_TARGET_HOSTS)


def classify_search_result(item: dict) -> SearchResultType:
    title = item.get("title", "").lower(); url = item.get("url", "").lower(); content = item.get("content", "").lower()
    text = f"{title} {url} {content[:300]}"
    if any(token in text for token in ("top 10", "top 20", "best companies", "companies in ", "firms in ", "roundup", "list of ")):
        return SearchResultType.LISTICLE
    if any(token in url for token in ("/jobs/", "/job/", "/careers/")) or "job opening" in text or "hiring for" in text:
        return SearchResultType.JOB_POSTING
    if any(token in text for token in ("directory", "company profile", "find businesses")) or _host(url) in {"clutch.co", "yelp.com", "yellowpages.com", "zoominfo.com"}:
        return SearchResultType.DIRECTORY
    if _host(url) in {"reuters.com", "bloomberg.com"} or any(token in url for token in ("/news/", "/article/")):
        return SearchResultType.NEWS
    if _blocked(_host(url)):
        return SearchResultType.VENDOR_CONTENT
    if any(token in url for token in ("/blog/", "/insights/", "/resources/")):
        return SearchResultType.ARTICLE
    if any(token in url for token in ("/about", "/company", "/who-we-are", "/services", "/operations")) or urlparse(url).path in {"", "/"}:
        return SearchResultType.OFFICIAL_COMPANY
    return SearchResultType.UNKNOWN


def extract_company_entities(item: dict, limit: int = 5) -> list[str]:
    """Conservative bounded extraction from the result title/snippet only."""
    text = f"{item.get('title', '')}. {item.get('content', '')}"
    candidates = []
    # Prefer explicit corporate suffixes and two-or-more title-cased tokens.
    pattern = r"\b(?:[A-Z][A-Za-z0-9&'’-]+(?:\s+|$)){1,5}(?:Inc\.?|Ltd\.?|Limited|LLC|PLC|Corp\.?|Corporation|Group|Holdings|Engineering|Industries|Systems|Services|Solutions|Construction|Logistics)\b"
    for match in re.finditer(pattern, text):
        name = re.sub(r"\s+", " ", match.group(0)).strip(" .,:;|-–")
        # Navigation/CTA labels immediately before a company name are common in
        # snippets and must not become part of the entity name.
        parts = name.split()
        while parts and parts[0].isupper() and len(parts[0]) > 2: parts.pop(0)
        name = " ".join(parts)
        if name not in STOP_NAMES and len(name) <= 100 and name not in candidates: candidates.append(name)
    # Listicle titles often contain a single company without a legal suffix.
    if not candidates:
        head = re.split(r"\s+[|–—-]\s+|:\s+", item.get("title", ""))[0].strip()
        words = head.split()
        if 2 <= len(words) <= 6 and all(w[:1].isupper() or w.lower() in {"and", "&", "of", "the"} for w in words):
            if head not in STOP_NAMES: candidates.append(head)
    return candidates[:limit]


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
        self.metrics = {}

    def _search(self, query: str, max_results: int = 5) -> list[dict]:
        try:
            response = self.client.post("https://api.tavily.com/search", json={"api_key": self.api_key, "query": query, "search_depth": "basic", "max_results": max_results, "include_answer": False, "include_raw_content": False})
            if response.status_code == 429: raise EmailFinderError(ErrorCategory.RATE_LIMITED, "Tavily free-tier quota/rate limit reached")
            response.raise_for_status(); return response.json().get("results", [])
        except EmailFinderError: raise
        except httpx.HTTPError as exc: raise EmailFinderError(ErrorCategory.PROVIDER_ERROR, f"Tavily discovery unavailable: {exc}") from exc

    def _validate_domain(self, name: str, item: dict, source: dict, source_type: SearchResultType) -> DiscoveredCompany | None:
        host = _host(item.get("url", ""))
        if not host or _blocked(host) or host.endswith(".edu") or host.endswith(".gov"): return None
        useful = [t.lower() for t in re.findall(r"[A-Za-z0-9]+", name) if len(t) >= 3 and t.lower() not in GENERIC_NAME_TOKENS]
        if not useful: return None
        url = item["url"]; parsed = urlparse(url)
        # Validation is against the actual proposed official site, never merely
        # against the search provider's snippet.
        try:
            response = self.client.get(f"{parsed.scheme or 'https'}://{parsed.netloc}", follow_redirects=True)
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "text/html"): return None
            haystack = re.sub(r"<[^>]+>", " ", response.text).lower()
        except (httpx.HTTPError, TypeError):
            return None
        matches = sum(token in haystack for token in useful)
        if matches < max(1, (len(useful) + 1) // 2): return None
        try:
            return DiscoveredCompany(name=name, domain=host, website=f"{parsed.scheme or 'https'}://{parsed.netloc}", discovery_url=source["url"], discovery_title=source.get("title", name), discovery_excerpt=f"SEARCH_DISCOVERY: {source.get('content', '')[:1000]}", discovery_source_type=source_type.value, resolution_source=url, resolution_confidence=90 if source_type == SearchResultType.OFFICIAL_COMPANY else 80, domain_validation_status="VALIDATED")
        except ValueError:
            return None

    def discover(self, brief):
        if not self.api_key: raise EmailFinderError(ErrorCategory.CONFIG_ERROR, "TAVILY_API_KEY is required for Tavily discovery")
        pool = {}; types = Counter(); extracted = []; attempts = unresolved = duplicates = 0
        for query in build_signal_queries(brief):
            if self.raw_result_count >= self.raw_limit: break
            results = self._search(query, min(5, self.raw_limit - self.raw_result_count)); self.raw_result_count += len(results)
            for item in results:
                kind = classify_search_result(item); types[kind.value] += 1
                if kind == SearchResultType.OFFICIAL_COMPANY:
                    name = re.split(r"\s+[|–—-]\s+", item.get("title", ""))[0].strip()
                    names = [name] if name else []
                else: names = extract_company_entities(item)
                extracted.extend(names)
                for name in names:
                    attempts += 1
                    resolutions = [item] if kind == SearchResultType.OFFICIAL_COMPANY else self._search(f'"{name}" official website', 3)
                    candidate = next((valid for result in resolutions if (valid := self._validate_domain(name, result, item, kind))), None)
                    if not candidate: unresolved += 1; continue
                    score = float(item.get("score", 0)); previous = pool.get(candidate.domain)
                    if previous is not None: duplicates += 1
                    if previous is None or score > previous[0]: pool[candidate.domain] = (score, candidate)
        self.unique_domain_count = len(pool)
        ranked = sorted(pool.values(), key=lambda pair: pair[0], reverse=True)
        found = [candidate for _, candidate in ranked[: min(self.candidate_limit, brief.prospecting.maximum_candidates_to_process)]]
        self.metrics = {"raw_results": self.raw_result_count, "result_types": dict(types), "company_entities_extracted": len(extracted), "domain_resolution_attempts": attempts, "resolved_official_domains": len(pool), "unresolved_entities": unresolved, "validated_candidate_companies": len(pool), "duplicates_removed": duplicates, "final_candidates": len(found)}
        return found
