import re
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
    def __init__(self): super().__init__(); self.parts = []; self.title = ""
    def handle_data(self, data):
        clean = normalize_evidence_text(data)
        if clean: self.parts.append(clean)


class PublicWebsiteEvidenceProvider:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=12, follow_redirects=True, headers={"User-Agent": "EmailFinder/0.2 public-evidence research"})

    def collect(self, company: DiscoveredCompany) -> list[PublicEvidence]:
        urls = [str(company.website), urljoin(str(company.website), "/about"), urljoin(str(company.website), "/careers")]
        evidence = []
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
            kind = "company_website" if index == 0 else "about_page" if "/about" in url else "careers_page"
            evidence.append(PublicEvidence(id=f"web-{index+1}", evidence_type=kind, source_url=str(response.url), source_title=company.name + " " + kind.replace("_", " "), excerpt=excerpt, source_quality=classify_source(str(response.url), company.domain)))
        return evidence


def identity_confirmed(company: DiscoveredCompany, evidence: list[PublicEvidence]) -> bool:
    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9]+", company.name) if len(t) >= 3]
    primary = " ".join(e.excerpt.lower() for e in evidence if e.source_quality == SourceQuality.PRIMARY)
    return bool(primary and tokens and any(token in primary for token in tokens))

