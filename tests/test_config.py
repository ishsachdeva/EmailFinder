import pytest
import yaml

from emailfinder.config.brief import CompanyBriefError, load_company_brief


def test_example_brief_validates(brief):
    assert brief.company.name == "Example Consulting"
    assert brief.qualification.minimum_confidence_score == 70


def test_bad_brief_has_human_readable_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump({"company": {"name": "Incomplete"}}), encoding="utf-8")
    with pytest.raises(CompanyBriefError, match="Invalid Company Brief"):
        load_company_brief(path)

