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
    assert titles == ["Press release", "FAQ", "How it works", "What it checks", "Privacy and safety", "Alarms",
                      "Run it locally", "Deploy", "Tests", "Limits", "Results", "Usability testing", "How this was built",
                      "Credits and licences", "Team"]


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
    results = section("Results")
    assert "8 for development and 9 held out" in results and "read once" in results
    assert "reader-safe-2" in results and "frozen before any reading" in results
    # dev and held-out side by side with their denominators, never pooled
    assert "| Development (8 quotes) | Held out (9 quotes) |" in results
    assert "36 of 82 (44%)" in results and "27 of 92 (29%)" in results
    assert section("Team").strip() != "## Team"  # written by the authors themselves
    limits = section("Limits")
    assert "Lambda concurrency on our account is 10." in limits
    assert "Amazon Nova reading is built but switched off while Bedrock access isn't granted." in limits
    assert "Nova" not in section("Press release")


def test_deploy_commands_and_architecture_image():
    assert ".\\scripts\\deploy.ps1 -Profile default -Region ap-south-1 -ExpectedAccount <your-account-id>" in README
    assert re.search(r"(?<!\d)\d{12}(?!\d)", README) is None  # no real account number
    assert "-HostingEnabled false" in README and ".\\scripts\\smoke_test.ps1 -Profile default" in README
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", README)
    assert images and all((ROOT / i).is_file() for i in images)


def test_plain_text_rules():
    ours = README.replace(section("Team"), "")  # the authors write the Team section themselves
    assert "—" not in ours and "–" not in ours
    # AI tools are named in "How this was built" (an event rule) and nowhere else.
    built = section("How this was built")
    # ...and in the agreed sentence on who reviewed the Telugu (Limits)
    telugu = "Claude Opus 5.5 reviewed it, with a second independent back-translation check"
    personas = "AI personas, made with Claude, to find usability problems"  # the Usability testing note
    assert not re.search(r"Claude|ChatGPT|Copilot|Anthropic|GPT-|generated (by|with)|AI[- ]assist",
                         README.replace(built, "").replace(telugu, "").replace(personas, ""), re.I)
    assert "**" not in ours
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


def test_confirm_before_finding_is_said_plainly_and_accuracy_is_what_was_measured():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    phrase = "a finding only appears after"
    assert phrase in readme.lower() and phrase in index.lower()
    assert "36 of the 82 printed values" in readme and "English quotes only" in readme
    for claim in ("never wrong", "never guesses", "always right", "100% accurate"):
        assert claim not in readme.lower() and claim not in index.lower()


def test_how_this_was_built_lists_each_ai_tool_and_its_role():
    # the authors word the roles themselves; every row names a tool and its role
    built = section("How this was built")
    rows = dict(re.findall(r"^\| ([^|]+?) \| ([^|]+?) *\|$", built, re.M)[1:])
    assert rows and all(role.strip() for role in rows.values())
    assert any(tool.startswith("Claude Code") for tool in rows) and any("ChatGPT" in tool for tool in rows)
    assert "Amazon Translate drafted the Telugu once (never at runtime)" in built


def test_credits_cover_every_pinned_dependency_and_pdfjs():
    credits = section("Credits and licences")
    names = {"boto3": "boto3", "botocore": "botocore", "aws-xray-sdk": "X-Ray SDK", "pytest": "pytest",
             "jsonschema": "jsonschema", "moto": "moto", "cfn-lint": "cfn-lint", "aws-sam-translator": "SAM translator",
             "PyMuPDF": "PyMuPDF", "rapidocr-onnxruntime": "rapidocr-onnxruntime", "onnxruntime": "ONNX Runtime",
             "opencv-python": "opencv-python", "numpy": "NumPy"}
    for req in ROOT.glob("requirements*.txt"):
        for line in req.read_text(encoding="utf-8").splitlines():
            name = re.split(r"[=<>\[ ]", line.strip())[0]
            if name and not name.startswith("#"):
                assert names[name] in credits, name
    for line in (ROOT / "src" / "requirements.txt").read_text(encoding="utf-8").splitlines():
        name = re.split(r"[=<>\[ ]", line.strip())[0]
        if name and not name.startswith("#"):
            assert names[name] in credits, name
    version = re.search(r"pdf\.js/([\d.]+)/", (ROOT / "web" / "index.html").read_text(encoding="utf-8")).group(1)
    assert f"[pdf.js](https://github.com/mozilla/pdf.js) {version}, from cdnjs" in credits and "Apache-2.0" in credits
    assert "AGPL-3.0" in credits  # PyMuPDF, used only by tools outside the deployed app
    jsdom = json.loads((ROOT / "tests" / "package.json").read_text(encoding="utf-8"))["devDependencies"]["jsdom"]
    assert f"[jsdom](https://github.com/jsdom/jsdom) {jsdom}" in credits
    for action in re.findall(r"uses: actions/([\w-]+)@", "".join(
            f.read_text(encoding="utf-8") for f in (ROOT / ".github" / "workflows").glob("*.yml"))):
        assert f"[{action}](https://github.com/actions/{action})" in credits, action


def test_the_test_sample_s4_is_described_as_deliberately_wrong():
    assert "#sample=S4" in README and "deliberately wrong reading" in README


def test_no_missing_detail_count_is_published_from_the_labels():
    results = section("Results")
    # the labels record DCR wording in a field the evaluation didn't read, and have no field for the others
    assert "We publish no count of quotes missing a DCR declaration" in results
    assert "unrecorded in 16" not in results and "| DCR declaration not stated |" not in results
    assert ("All 11 definitive labelled findings were recomputed by an independent script that shares no code with "
            "the app (11 of 11 agree), and rechecked by hand by a team member, also 11 of 11.") in results
    assert "being checked by eye" not in README
    assert "never pooled" not in README.replace(section("Team"), "")  # the authors' own words stay theirs


def test_typed_checks_and_traces_are_described_as_they_are():
    assert "with nothing stored" not in README and "stores nothing" not in README
    assert "a random id, a counter and a keyed hash of the values" in README and "15 minutes" in README
    privacy = section("Privacy and safety")
    assert "operation, HTTP status and timing only" in privacy
    for line in privacy.splitlines():
        if "X-Ray" in line or "trace" in line:
            assert "never" not in line, line  # trace claims are only those a test backs


def test_the_telugu_limit_is_stated_as_agreed():
    assert ("The results screen and the vendor message can be shown in Telugu. Amazon Translate drafted it; Claude "
            "Opus 5.5 reviewed it, with a second independent back-translation check; the later wording changes came "
            "from an AI review and were approved by a native Telugu speaker on our team. No professional translator "
            "has reviewed it. Other screens are in English.") in section("Limits")
    assert "Hindi isn't offered." in section("Limits")
    assert README.count("native") == 1  # nowhere else says a native speaker reviewed the Telugu


def test_usability_testing_note_is_marked_as_simulated():
    note = section("Usability testing")
    assert "simulated dry run with AI personas, made with Claude" in note
    assert "Simulated results are not reported as user evidence." in note

def test_round7_claims_are_stated_as_built():
    assert "at most four" not in README and "never publishes stale settings" not in README
    assert "Every request is checked in full before it takes a slot" in README
    assert "CORS is a browser access policy, not a security boundary" in README
    assert "accept requests from" not in README and "accepts requests only" not in README
    assert "subsidy findings that use a rule also name the rule and its date" in README
    assert "stops the job, with reason `read_limit`" in README and "counts reads started, not AWS charges" in README
