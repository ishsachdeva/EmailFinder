import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

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


class DomainType(StrEnum):
    COMPANY = "COMPANY"
    ASSOCIATION = "ASSOCIATION"
    DIRECTORY = "DIRECTORY"
    MARKETPLACE = "MARKETPLACE"
    PUBLISHER = "PUBLISHER"
    DATABASE = "DATABASE"
    JOB_BOARD = "JOB_BOARD"
    UNKNOWN = "UNKNOWN"


class ResolutionStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"


NON_TARGET_HOSTS = BLOCKED_HOSTS | {
    "amazon.com", "business.amazon.com", "builtin.com", "clutch.co", "forbes.com",
    "glassdoor.com", "indeed.com", "reuters.com", "bloomberg.com", "yelp.com",
    "yellowpages.com", "zoominfo.com", "ukmarketingmanagement.com",
}
GENERIC_TOKENS = {
    "company", "companies", "group", "limited", "ltd", "services", "solutions",
    "engineering", "engineers", "construction", "logistics", "manufacturing",
    "industrial", "industries", "systems", "international", "procurement",
    "civil", "business", "professional", "distribution", "operations",
}
RANKING_WORDS = {"top", "best", "biggest", "leading", "largest", "greatest"}
CTA_PHRASES = {"request pricing", "learn more", "contact us", "view all", "view company", "read more", "our services"}


@dataclass(frozen=True)
class LinkedEntity:
    name: str
    destination_url: str
    source_fragment: str


@dataclass(frozen=True)
class DomainEvaluation:
    status: ResolutionStatus
    score: int
    domain_type: DomainType
    url: str


def _host(url: str) -> str:
    return normalize_domain(urlparse(url).netloc)


def _blocked(host: str) -> bool:
    return any(host == item or host.endswith("." + item) for item in NON_TARGET_HOSTS)


def distinctive_tokens(name: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", name) if len(token) >= 3 and token.lower() not in GENERIC_TOKENS]


def company_name_sane(name: str) -> bool:
    normalized = " ".join(name.split()).strip(" .,:;|-–").lower()
    words = re.findall(r"[a-z0-9]+", normalized)
    if len(normalized) < 3 or len(words) > 10 or not words:
        return False
    if words[0] in RANKING_WORDS or (words[0] == "the" and len(words) > 1 and words[1] in RANKING_WORDS) or any(phrase in normalized for phrase in CTA_PHRASES):
        return False
    return bool(distinctive_tokens(name))


def classify_search_result(item: dict) -> SearchResultType:
    title = item.get("title", "").lower(); url = item.get("url", "").lower(); content = item.get("content", "").lower()
    host = _host(url); path = urlparse(url).path
    if not _blocked(host) and path in {"", "/"} and company_name_sane(re.split(r"\s+[|–—-]\s+", item.get("title", ""))[0]):
        return SearchResultType.OFFICIAL_COMPANY
    text = f"{title} {url} {content[:400]}"
    if any(token in text for token in ("top 10", "top 20", "best companies", "companies in ", "firms in ", "roundup", "list of ")): return SearchResultType.LISTICLE
    if any(token in url for token in ("/jobs/", "/job/", "/careers/")) or "job opening" in text or "hiring for" in text: return SearchResultType.JOB_POSTING
    if any(token in text for token in ("directory", "company profile", "find businesses")) or host in {"clutch.co", "yelp.com", "yellowpages.com", "zoominfo.com"}: return SearchResultType.DIRECTORY
    if host in {"reuters.com", "bloomberg.com"} or any(token in url for token in ("/news/", "/article/")): return SearchResultType.NEWS
    if _blocked(host): return SearchResultType.VENDOR_CONTENT
    if any(token in url for token in ("/blog/", "/insights/", "/resources/")): return SearchResultType.ARTICLE
    if any(token in url for token in ("/about", "/company", "/who-we-are", "/services", "/operations")): return SearchResultType.OFFICIAL_COMPANY
    return SearchResultType.UNKNOWN


class _LinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(); self.base_url = base_url; self.links = []; self.recent = []; self.anchor = None; self.anchor_text = []; self.in_script = False; self.script_type = ""; self.script_text = []; self.organizations = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a" and values.get("href"): self.anchor = values["href"]; self.anchor_text = []
        if tag == "script": self.in_script = True; self.script_type = values.get("type", ""); self.script_text = []
    def handle_data(self, data):
        clean = " ".join(data.split())
        if self.in_script: self.script_text.append(data)
        elif clean:
            if self.anchor is not None: self.anchor_text.append(clean)
            self.recent = (self.recent + [clean])[-4:]
    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None:
            name = " ".join(self.anchor_text).strip(); url = urljoin(self.base_url, self.anchor)
            self.links.append(LinkedEntity(name, url, " ".join((self.recent + self.anchor_text)[-6:])[:800])); self.anchor = None; self.anchor_text = []
        if tag == "script":
            if "ld+json" in self.script_type:
                try: self._organizations(json.loads("".join(self.script_text)))
                except (json.JSONDecodeError, TypeError): pass
            self.in_script = False
    def _organizations(self, value):
        if isinstance(value, list):
            for item in value: self._organizations(item)
        elif isinstance(value, dict):
            if value.get("@type") in {"Organization", "Corporation", "LocalBusiness"} and value.get("name") and value.get("url"):
                self.organizations.append(LinkedEntity(str(value["name"]), urljoin(self.base_url, str(value["url"])), json.dumps(value)[:800]))
            for item in value.values(): self._organizations(item)


def extract_linked_company_entities(html: str, source_url: str, limit: int = 5) -> list[LinkedEntity]:
    parser = _LinkParser(source_url); parser.feed(html); source_host = _host(source_url); found = []
    for lead in parser.organizations + parser.links:
        host = _host(lead.destination_url)
        if not host or host == source_host or host.endswith("." + source_host) or _blocked(host): continue
        if not company_name_sane(lead.name): continue
        if lead not in found: found.append(lead)
    return found[:limit]


def extract_company_entities(item: dict, limit: int = 5) -> list[str]:
    """Plain search-result text is Level 4 evidence and creates no entity."""
    return []


def classify_domain_type(host: str, homepage_text: str) -> DomainType:
    text = homepage_text.lower()[:12000]
    if host.endswith(".edu") or "university" in text: return DomainType.PUBLISHER
    if host.endswith(".gov"): return DomainType.DATABASE
    if any(term in text for term in ("professional association", "trade association", "membership association", "society of ", "join our members")): return DomainType.ASSOCIATION
    if any(term in text for term in ("marketplace for", "hire freelancers", "freelance marketplace", "buyers and sellers")): return DomainType.MARKETPLACE
    if any(term in text for term in ("open database", "search our database", "database of companies", "browse all data sources")): return DomainType.DATABASE
    if any(term in text for term in ("job board", "find job opportunities", "post a job")): return DomainType.JOB_BOARD
    if any(term in text for term in ("business directory", "company directory", "find local businesses")): return DomainType.DIRECTORY
    return DomainType.UNKNOWN


def build_signal_queries(brief, limit: int = 6) -> list[str]:
    queries = []; signals = brief.qualification.positive_signals or ["operations"]
    for index, industry in enumerate(brief.icp.target_industries):
        geography = brief.icp.target_geographies[index % len(brief.icp.target_geographies)]
        queries.append(f'{industry} company "{signals[index % len(signals)]}" {geography}')
        if len(queries) >= limit: break
    return queries


class TavilyCompanyDiscoveryProvider:
    def __init__(self, api_key=None, client=None, raw_limit=20, candidate_limit=None):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", ""); self.client = client or httpx.Client(timeout=30, follow_redirects=True)
        self.raw_limit = raw_limit; self.candidate_limit = candidate_limit or int(os.getenv("DISCOVERY_LIMIT", "5")); self.raw_result_count = self.unique_domain_count = 0; self.metrics = {}
    def _search(self, query: str, max_results: int = 5) -> list[dict]:
        try:
            response = self.client.post("https://api.tavily.com/search", json={"api_key": self.api_key, "query": query, "search_depth": "basic", "max_results": max_results, "include_answer": False, "include_raw_content": False})
            if response.status_code == 429: raise EmailFinderError(ErrorCategory.RATE_LIMITED, "Tavily free-tier quota/rate limit reached")
            response.raise_for_status(); return response.json().get("results", [])
        except EmailFinderError: raise
        except httpx.HTTPError as exc: raise EmailFinderError(ErrorCategory.PROVIDER_ERROR, f"Tavily discovery unavailable: {exc}") from exc
    def _get_html(self, url: str) -> tuple[str, str] | None:
        try:
            response = self.client.get(url, follow_redirects=True); response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "text/html"): return None
            return str(response.url), response.text
        except (httpx.HTTPError, TypeError): return None
    def _evaluate_domain(self, name: str, url: str) -> DomainEvaluation:
        host = _host(url)
        if not company_name_sane(name) or not host or _blocked(host) or host.endswith((".edu", ".gov")): return DomainEvaluation(ResolutionStatus.REJECTED, 0, DomainType.UNKNOWN, url)
        page = self._get_html(f"{urlparse(url).scheme or 'https'}://{urlparse(url).netloc}")
        if not page: return DomainEvaluation(ResolutionStatus.UNRESOLVED, 0, DomainType.UNKNOWN, url)
        final_url, html = page; dtype = classify_domain_type(_host(final_url), html)
        if dtype not in {DomainType.COMPANY, DomainType.UNKNOWN}: return DomainEvaluation(ResolutionStatus.REJECTED, 0, dtype, final_url)
        tokens = distinctive_tokens(name)
        identity_sections = " ".join(re.findall(r"<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>", html, re.I | re.S))
        identity_text = re.sub(r"<[^>]+>", " ", identity_sections).lower()
        visible = re.sub(r"<[^>]+>", " ", html).lower()[:1500]
        identity_haystack = identity_text or visible
        matched = sum(token in identity_haystack for token in tokens); slug = re.sub(r"[^a-z0-9]", "", _host(final_url).split(".")[0]); domain_match = any(token in slug for token in tokens)
        score = round(70 * matched / len(tokens) + (20 if domain_match else 0) + 10)
        strong_identity = bool(identity_text) or domain_match
        status = ResolutionStatus.CONFIRMED if score >= 80 and matched == len(tokens) and strong_identity else ResolutionStatus.PROBABLE if score >= 60 else ResolutionStatus.UNRESOLVED
        return DomainEvaluation(status, min(score, 100), dtype, final_url)
    def _candidate(self, name: str, lead_url: str, source: dict, kind: SearchResultType, fragment: str) -> DiscoveredCompany | None:
        if not company_name_sane(name): return None
        urls = [lead_url] if lead_url and (kind == SearchResultType.OFFICIAL_COMPANY or _host(lead_url) != _host(source.get("url", ""))) else [item.get("url", "") for item in self._search(f'"{name}" official website', 3)]
        evaluations = [self._evaluate_domain(name, url) for url in urls[:3] if url]
        confirmed = next((item for item in sorted(evaluations, key=lambda x: x.score, reverse=True) if item.status == ResolutionStatus.CONFIRMED), None)
        if not confirmed: return None
        parsed = urlparse(confirmed.url); host = _host(confirmed.url)
        return DiscoveredCompany(name=name, domain=host, website=f"{parsed.scheme or 'https'}://{parsed.netloc}", discovery_url=source["url"], discovery_title=source.get("title", name), discovery_excerpt=f"SEARCH_DISCOVERY: {fragment[:1000]}", discovery_source_type=kind.value, resolution_source=confirmed.url, resolution_confidence=confirmed.score, domain_validation_status="VALIDATED", entity_resolution_status="CONFIRMED")
    def discover(self, brief):
        if not self.api_key: raise EmailFinderError(ErrorCategory.CONFIG_ERROR, "TAVILY_API_KEY is required for Tavily discovery")
        pool = {}; types = Counter(); extracted = attempts = unresolved = duplicates = rejected = 0
        for query in build_signal_queries(brief):
            if self.raw_result_count >= self.raw_limit: break
            results = self._search(query, min(5, self.raw_limit - self.raw_result_count)); self.raw_result_count += len(results)
            for item in results:
                kind = classify_search_result(item); types[kind.value] += 1; leads = []
                if kind == SearchResultType.OFFICIAL_COMPANY:
                    name = re.split(r"\s+[|–—-]\s+", item.get("title", ""))[0].strip()
                    if company_name_sane(name): leads = [LinkedEntity(name, item.get("url", ""), item.get("content", ""))]
                else:
                    page = self._get_html(item.get("url", ""))
                    if page: leads = extract_linked_company_entities(page[1], page[0], 5)
                extracted += len(leads)
                for lead in leads:
                    attempts += 1; candidate = self._candidate(lead.name, lead.destination_url, item, kind, lead.source_fragment)
                    if not candidate: unresolved += 1; rejected += 1; continue
                    score = float(item.get("score", 0)); previous = pool.get(candidate.domain)
                    if previous is not None: duplicates += 1
                    if previous is None or score > previous[0]: pool[candidate.domain] = (score, candidate)
        self.unique_domain_count = len(pool); ranked = sorted(pool.values(), key=lambda pair: pair[0], reverse=True)
        found = [candidate for _, candidate in ranked[:min(self.candidate_limit, brief.prospecting.maximum_candidates_to_process)]]
        self.metrics = {"raw_results": self.raw_result_count, "result_types": dict(types), "company_entities_extracted": extracted, "domain_resolution_attempts": attempts, "resolved_official_domains": len(pool), "domain_resolutions_rejected": rejected, "unresolved_entities": unresolved, "validated_candidate_companies": len(pool), "duplicates_removed": duplicates, "final_candidates": len(found)}
        return found
