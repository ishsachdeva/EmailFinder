import re
import os
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from emailfinder.domain.phase2 import DiscoveredCompany, PublicEvidence, SourceQuality
from emailfinder.persistence.database import normalize_domain


REPUTABLE_HOSTS = {"crunchbase.com", "reuters.com", "bloomberg.com", "companieshouse.gov.uk", "indeed.com", "glassdoor.com"}


def classify_source(url: str, company_domain: str) -> SourceQuality:
    host = normalize_domain(urlparse(url).netloc)
    if host == normalize_domain(company_domain) or host.endswith("." + normalize_domain(company_domain)):
        return SourceQuality.PRIMARY
    if any(host == d or host.endswith("." + d) for d in REPUTABLE_HOSTS):
        return SourceQuality.REPUTABLE_SECONDARY
    return SourceQuality.WEAK_SECONDARY


def normalize_evidence_text(text: str, limit: int = 3500) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


class _TextParser(HTMLParser):
    def __init__(self): super().__init__(); self.parts = []; self.links = []; self.title = ""; self.ignored_depth = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}: self.ignored_depth += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href: self.links.append(href)
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.ignored_depth: self.ignored_depth -= 1
    def handle_data(self, data):
        if self.ignored_depth: return
        clean = normalize_evidence_text(data)
        if clean: self.parts.append(clean)


class PublicWebsiteEvidenceProvider:
    LINK_TERMS = ("about", "company", "who-we-are", "location", "facilit", "operation", "service", "industr", "career", "jobs", "team", "leadership", "investor", "annual-report", "profile", "procurement", "supply-chain", "finance")
    def __init__(self, client: httpx.Client | None = None, page_limit: int | None = None, search_client: httpx.Client | None = None, api_key: str | None = None, targeted_search_budget: int | None = None):
        self.client = client or httpx.Client(timeout=12, follow_redirects=True, headers={"User-Agent": "EmailFinder/0.2 public-evidence research"})
        self.page_limit = page_limit or int(__import__("os").getenv("EVIDENCE_PAGE_LIMIT", "5"))
        self.search_client = search_client or httpx.Client(timeout=30)
        self.api_key = api_key if api_key is not None else os.getenv("TAVILY_API_KEY", "")
        self.targeted_search_budget = targeted_search_budget

    def collect(self, company: DiscoveredCompany) -> list[PublicEvidence]:
        homepage = str(company.website); evidence = []; urls = [homepage]
        try:
            response = self.client.get(homepage); response.raise_for_status()
            parser = _TextParser(); parser.feed(response.text)
            host = normalize_domain(urlparse(str(response.url)).netloc)
            ranked = []
            for link in parser.links:
                absolute = urljoin(str(response.url), link); parsed = urlparse(absolute)
                if normalize_domain(parsed.netloc) != host: continue
                path = parsed.path.lower(); score = sum(term in path for term in self.LINK_TERMS)
                if score: ranked.append((score, absolute.split("#", 1)[0]))
            urls += [url for _, url in sorted(set(ranked), key=lambda item: (-item[0], item[1]))[: self.page_limit]]
        except httpx.HTTPError:
            pass
        for index, url in enumerate(dict.fromkeys(urls)):
            try:
                response = self.client.get(url)
                response.raise_for_status()
                if "text/html" not in response.headers.get("content-type", "text/html"):
                    continue
            except httpx.HTTPError:
                continue
            parser = _TextParser(); parser.feed(response.text)
            excerpt = normalize_evidence_text(" ".join(parser.parts))
            if len(excerpt) < 80:
                continue
            path = urlparse(url).path.lower()
            kind = "company_website" if index == 0 else "investor_page" if "investor" in path or "annual-report" in path else "careers_page" if "career" in path or "jobs" in path else "company_detail_page"
            evidence.append(PublicEvidence(id=f"web-{index+1}", evidence_type=kind, source_url=str(response.url), source_title=company.name + " " + kind.replace("_", " "), excerpt=excerpt, source_quality=classify_source(str(response.url), company.domain)))
        budget = self.targeted_search_budget
        if budget is None: budget = int(os.getenv("TARGETED_EVIDENCE_SEARCH_BUDGET", "2"))
        combined = " ".join(item.excerpt.lower() for item in evidence)
        objectives = []
        if not re.search(r"\b[0-9][0-9,]{0,6}\s*(?:employees|people|staff members)\b", combined): objectives.append("company employees team size about")
        if not any(token in combined for token in ("united states", "united kingdom", "headquartered", "head office", "locations")): objectives.append("company headquarters locations about")
        for objective in objectives[:budget]:
            if not self.api_key: break
            try:
                result = self.search_client.post("https://api.tavily.com/search", json={"api_key": self.api_key, "query": f'"{company.name}" {objective}', "search_depth": "basic", "max_results": 3, "include_domains": [company.domain], "include_answer": False, "include_raw_content": False})
                result.raise_for_status()
            except httpx.HTTPError: continue
            for item in result.json().get("results", []):
                if normalize_domain(urlparse(item.get("url", "")).netloc) != normalize_domain(company.domain): continue
                try:
                    page = self.client.get(item["url"]); page.raise_for_status()
                except httpx.HTTPError: continue
                parser = _TextParser(); parser.feed(page.text); excerpt = normalize_evidence_text(" ".join(parser.parts))
                if len(excerpt) < 80: continue
                evidence.append(PublicEvidence(id=f"targeted-{len(evidence)+1}", evidence_type="targeted_company_evidence", source_url=str(page.url), source_title=item.get("title", company.name), excerpt=excerpt, source_quality=SourceQuality.PRIMARY))
                break
        return evidence


def identity_confirmed(company: DiscoveredCompany, evidence: list[PublicEvidence]) -> bool:
    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9]+", company.name) if len(t) >= 3]
    primary = " ".join(e.excerpt.lower() for e in evidence if e.source_quality == SourceQuality.PRIMARY)
    return bool(primary and tokens and any(token in primary for token in tokens))
