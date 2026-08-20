import os
from urllib.parse import urlparse

import httpx

from emailfinder.domain.errors import EmailFinderError, ErrorCategory
from emailfinder.domain.phase2 import DiscoveredCompany
from emailfinder.persistence.database import normalize_domain


COUNTRY_IDS = {"united states": "Q30", "united kingdom": "Q145"}


class WikidataCompanyDiscoveryProvider:
    """Zero-cost structured discovery using Wikidata's public APIs (CC0)."""

    def __init__(self, client: httpx.Client | None = None, limit: int | None = None):
        self.client = client or httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "EmailFinder/0.2 (company research; https://github.com/ishsachdeva/EmailFinder)"})
        self.limit = limit or int(os.getenv("DISCOVERY_LIMIT", "5"))

    def discover(self, brief) -> list[DiscoveredCompany]:
        limit = min(self.limit, brief.prospecting.maximum_candidates_to_process)
        industry_ids = []
        try:
            for industry in brief.icp.target_industries:
                response = self.client.get("https://www.wikidata.org/w/api.php", params={"action": "wbsearchentities", "search": industry, "language": "en", "format": "json", "limit": 1, "type": "item"})
                response.raise_for_status()
                if response.json().get("search"):
                    industry_ids.append(response.json()["search"][0]["id"])
            country_ids = [COUNTRY_IDS[g.lower()] for g in brief.icp.target_geographies if g.lower() in COUNTRY_IDS]
            if not industry_ids or not country_ids:
                raise EmailFinderError(ErrorCategory.CONFIG_ERROR, "Wikidata discovery requires resolvable target industries and supported target geographies")
            query = f"""
SELECT DISTINCT ?company ?companyLabel ?website ?industryLabel ?countryLabel ?employees WHERE {{
  VALUES ?industry {{ {' '.join('wd:' + item for item in industry_ids)} }}
  VALUES ?country {{ {' '.join('wd:' + item for item in country_ids)} }}
  ?company wdt:P452 ?industry; wdt:P17 ?country; wdt:P856 ?website.
  OPTIONAL {{ ?company wdt:P1128 ?employees. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}} LIMIT {limit}
"""
            response = self.client.get("https://query.wikidata.org/sparql", params={"query": query, "format": "json"}, headers={"Accept": "application/sparql-results+json"})
            if response.status_code == 429:
                raise EmailFinderError(ErrorCategory.RATE_LIMITED, "Wikidata public query service rate limited the request")
            response.raise_for_status()
        except EmailFinderError:
            raise
        except httpx.TimeoutException as exc:
            raise EmailFinderError(ErrorCategory.NETWORK_ERROR, "Wikidata discovery timed out") from exc
        except httpx.HTTPError as exc:
            raise EmailFinderError(ErrorCategory.PROVIDER_ERROR, f"Wikidata discovery unavailable: {exc}") from exc
        candidates = []
        for row in response.json().get("results", {}).get("bindings", []):
            try:
                website = row["website"]["value"]
                domain = normalize_domain(urlparse(website).netloc)
                name = row["companyLabel"]["value"]
                details = [row.get("industryLabel", {}).get("value", ""), row.get("countryLabel", {}).get("value", "")]
                if row.get("employees"): details.append(f'{row["employees"]["value"]} employees')
                entity_url = row["company"]["value"]
                candidates.append(DiscoveredCompany(name=name, domain=domain, website=website, discovery_url=entity_url, discovery_title=f"{name} — Wikidata", discovery_excerpt="; ".join(part for part in details if part)))
            except (KeyError, ValueError):
                continue
        return candidates
