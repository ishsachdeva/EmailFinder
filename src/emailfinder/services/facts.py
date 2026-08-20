import re

from emailfinder.domain.phase2 import ResolvedFact, ResolvedFacts, SourceQuality


QUALITY = {SourceQuality.PRIMARY: 4, SourceQuality.REPUTABLE_SECONDARY: 3, SourceQuality.WEAK_SECONDARY: 2, SourceQuality.SEARCH_DISCOVERY: 1}
GEOGRAPHIES = {"United States": ("united states", " usa ", " u.s.", "california", "washington", "florida", "texas", "new york"), "United Kingdom": ("united kingdom", " uk ", "england", "scotland", "wales", "london"), "United Arab Emirates": ("united arab emirates", " uae ", "dubai", "abu dhabi")}


def _choose(claims):
    if not claims: return ResolvedFact()
    claims.sort(key=lambda claim: claim[0], reverse=True); top = claims[0]
    alternatives = sorted({str(value) for _, value, _ in claims if value != top[1]})
    return ResolvedFact(value=top[1], evidence_ids=[eid for quality, value, eid in claims if value == top[1] and quality == top[0]], confidence=95 if top[0] == 4 else 75, conflict=bool(alternatives), alternatives=alternatives)


def resolve_facts(evidence, brief) -> ResolvedFacts:
    geography_claims, employee_claims, industry_claims = [], [], []
    for item in evidence:
        if item.source_quality == SourceQuality.SEARCH_DISCOVERY:
            continue
        text = " " + item.excerpt.lower() + " "; quality = QUALITY[item.source_quality]
        for value, tokens in GEOGRAPHIES.items():
            if any(token in text for token in tokens): geography_claims.append((quality, value, item.id))
        for raw in re.findall(r"\b([0-9][0-9,]{0,6})\s*(?:\+\s*)?(?:employees|staff members|people)\b", text): employee_claims.append((quality, int(raw.replace(",", "")), item.id))
        # Industry is factual only when the organization explicitly describes
        # itself that way. Incidental product/job terminology is not enough.
        for industry in brief.icp.target_industries:
            label = re.escape(industry.lower())
            explicit = rf"(?:we are|is|are|leading|an?)\s+(?:a\s+)?{label}\s+(?:company|business|firm|provider|organization)\b|\b{label}\s+(?:company|business|firm|provider|organization)\b"
            if re.search(explicit, text): industry_claims.append((quality, industry, item.id))
    inactive = [(QUALITY[e.source_quality], "INACTIVE", e.id) for e in evidence if any(x in e.excerpt.lower() for x in ("permanently closed", "ceased trading", "dissolved company"))]
    return ResolvedFacts(geography=_choose(geography_claims), employee_count=_choose(employee_claims), industry=_choose(industry_claims), operating_status=_choose(inactive) if inactive else ResolvedFact(value="UNKNOWN"))


def resolved_hard_rejection(facts: ResolvedFacts, brief):
    if facts.operating_status.value == "INACTIVE": return "REJECT: strong evidence indicates the company is inactive"
    if facts.geography.value and facts.geography.confidence >= 90 and facts.geography.value not in brief.icp.target_geographies:
        return f"REJECT: resolved primary geography {facts.geography.value} is outside configured targets"
    if isinstance(facts.employee_count.value, int) and facts.employee_count.confidence >= 75:
        if facts.employee_count.value < brief.icp.employee_min: return "REJECT: resolved employee count is below configured minimum"
        if facts.employee_count.value > brief.icp.employee_max: return "REJECT: resolved employee count is above configured maximum"
    return None


def deterministic_score(facts: ResolvedFacts, brief):
    weights = brief.qualification.scoring_weights; score = 0
    if facts.industry.value in brief.icp.target_industries: score += weights.industry_fit
    if facts.geography.value in brief.icp.target_geographies: score += weights.geography_fit
    if isinstance(facts.employee_count.value, int) and brief.icp.employee_min <= facts.employee_count.value <= brief.icp.employee_max: score += weights.company_size_fit
    return score
