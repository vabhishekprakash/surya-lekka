import json
import re
from datetime import date
from pathlib import Path

from checks.subsidy import GATES
from rules import load_cfa_rules

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def day(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b %Y}"


def section(title):
    start = README.index(f"\n## {title}\n")
    end = README.find("\n## ", start + 1)
    return README[start:end if end != -1 else None]


def test_sections_in_order():
    titles = re.findall(r"^## (.+)$", README, re.M)
    assert titles == ["Press release", "FAQ", "How it works", "What it checks", "Privacy and safety", "Run it locally",
                      "Deploy", "Tests", "Limits", "Results", "Team"]


def test_rule_values_match_the_rules_file():
    rules = load_cfa_rules()
    table = section("What it checks")
    for rule in (r for r in rules["rules"] if "slabs" in r):
        row = next(line for line in table.splitlines() if line.startswith(f"| `{rule['rule_id']}` |"))
        amounts = [s["inr_per_kwp"] for s in rule["slabs"]] + [rule["cap_inr"]]
        assert [f"Rs {int(a):,}" for a in amounts] == re.findall(r"Rs [\d,]+", row)
        assert all(f"{s}" in row for s in rule["sections"])
    assert f"on or after {day(rules['effective_from']['value'])}" in table
    assert f"checked against the sources on {day(rules['verified_on'])}" in table
    for source in rules["sources"]:
        assert day(source["date"]) in table
    special = rules["special_category"]
    assert ", ".join(special["states"] + special["union_territories"]) in table


def test_gate_table_follows_the_code():
    rows = re.findall(r"^\| (\d+) \| `([a-z_]+)` \|", section("What it checks"), re.M)
    assert rows == [(str(n), gate) for n, (gate, _, _) in enumerate(GATES, 1)]


def test_privacy_line_matches_the_web_app():
    line = ("Your pages are processed in AWS's Mumbai region (India) and deleted after reading. If reading fails, "
            "they're removed automatically, usually within two days.")
    assert f'"{line}"' in section("Privacy and safety")
    assert "inRegion: \"Your pages are processed in {where} and deleted after reading." in APP
    assert "AWS's Sydney region (Australia)" in README


def test_results_team_and_limits_say_only_what_is_known():
    assert section("Results").split("\n\n")[1] == "Evaluation pending."
    assert "8 for development and 9 held out" in section("Results") and "run once" in section("Results")
    assert re.search(r"\d+(\.\d+)?\s?%", section("Results")) is None
    assert section("Team").strip().endswith("TEAM: to be filled in by the authors")
    limits = section("Limits")
    assert "Lambda concurrency on our account is 10." in limits
    assert "Amazon Nova reading is switched off until Bedrock access is granted." in limits


def test_deploy_commands_and_architecture_image():
    assert ".\\scripts\\deploy.ps1 -Profile default -Region ap-south-1 -ExpectedAccount <your-account-id>" in README
    assert re.search(r"(?<!\d)\d{12}(?!\d)", README) is None  # no real account number
    assert "-HostingEnabled false" in README and ".\\scripts\\smoke_test.ps1 -Profile default" in README
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", README)
    assert images and all((ROOT / i).is_file() for i in images)


def test_plain_text_rules():
    assert "—" not in README and "–" not in README
    assert not re.search(r"Claude|ChatGPT|Copilot|Anthropic|generated (by|with)|AI[- ]assist", README, re.I)
    assert "**" not in README
    assert "illustrative" in section("Press release")  # the household quote is not presented as real


def test_textract_engine_privacy_cost_and_limits():
    privacy = section("Privacy and safety")
    for kind in ("textractOptedOut", "textract"):
        line = json.loads(re.search(rf'^\s*{kind}: (".*"),$', APP, re.M).group(1))
        assert line.replace("{where}", "AWS's Mumbai region (India)") in privacy
    assert "describe-effective-policy" in privacy
    assert "Read by Amazon Textract" in README
    deploy = section("Deploy")
    assert "-ReadingEngine textract" in deploy and "$0.020 per page" in deploy
    assert "300 pages" in deploy and "$6.00" in deploy
    limits = section("Limits")
    assert "English" in limits and "15 queries" in limits and "15 pixels" in limits
    assert "set on the AWS account by hand" in README and "template creates" not in README


def test_limits_say_how_panel_answers_are_paired():
    assert ("paired in the order Textract returns them on a page" in section("Limits")
            and "the household confirms the pairing" in section("Limits"))


def test_the_privacy_summary_near_the_top_is_the_sentence_the_site_shows():
    # The public site reads with Textract and the account's opt-out is confirmed.
    line = json.loads(re.search(r'^\s*textractOptedOut: (".*"),$', APP, re.M).group(1))
    assert line.replace("{where}", "AWS's Mumbai region (India)") in section("FAQ")


def test_the_site_is_on_github_pages_while_cloudfront_is_blocked():
    how = section("How it works")
    assert "GitHub Pages" in how and "CloudFront" in how and "still" in how
    deploy = section("Deploy")
    assert "-SiteOrigin https://<your-user>.github.io -Pages" in deploy
    assert ".github/workflows/pages.yml" in deploy and "gh variable set" in deploy
    assert "https://vabhishekprakash.github.io/surya-lekka/" in README
