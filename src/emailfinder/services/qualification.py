import re

from emailfinder.domain.phase2 import DiscoveredCompany, PublicEvidence, QualificationOutput


def hard_rejection(company: DiscoveredCompany, evidence: list[PublicEvidence], brief) -> str | None:
    text = " ".join(e.excerpt.lower() for e in evidence)
    if not evidence:
        return "INSUFFICIENT_EVIDENCE: no accessible public evidence"
    if any(industry.lower() in text for industry in brief.icp.excluded_industries):
        return "REJECT: excluded industry is explicit in public evidence"
    if any(geo.lower() in text for geo in brief.icp.excluded_geographies):
        return "REJECT: excluded geography is explicit in public evidence"
    explicit_sizes = [int(value.replace(",", "")) for value in re.findall(r"\b([0-9][0-9,]{0,6})\s+(?:employees|staff members|people)\b", text)]
    if explicit_sizes and max(explicit_sizes) < brief.icp.employee_min:
        return "REJECT: explicit employee count is below configured minimum"
    if explicit_sizes and min(explicit_sizes) > brief.icp.employee_max:
        return "REJECT: explicit employee count is above configured maximum"
    if any(signal.lower() in text for signal in ["permanently closed", "ceased trading", "dissolved company"]):
        return "REJECT: public evidence indicates company is no longer operating"
    return None


def inspectable_score(output: QualificationOutput, brief) -> int:
    raise TypeError("use final_icp_score(deterministic_score, model_score)")


def final_icp_score(deterministic: int, model: int) -> int:
    """Combine factual fit (70%) and evidence-constrained soft fit (30%).

    The deterministic score has a documented maximum of 55. NVIDIA returns
    only ``model_score``; it can never supply or override the final score.
    """
    return round((deterministic / 55 * 100) * 0.70 + model * 0.30)
