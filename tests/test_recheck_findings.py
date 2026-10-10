"""The independent recheck shares no code with the app and follows the rules as written."""
import csv
import runpy
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "eval" / "recheck_findings.py"
recheck = runpy.run_path(str(SCRIPT))


def test_it_imports_nothing_from_the_app():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "sys.path.insert" not in source and "from checks" not in source and "from api" not in source
    assert "import checks" not in source and "src" not in source.replace("Describes", "")


def test_the_subsidy_rule_as_written():
    subsidy = recheck["subsidy"]
    assert subsidy(Decimal("2.825")) == Decimal(74850)
    assert subsidy(Decimal("2.5")) == Decimal(69000)
    assert subsidy(Decimal("10")) == Decimal(78000)
    assert subsidy(Decimal("3"), "special") == Decimal(85800)


def test_a_synthetic_file_is_recomputed(tmp_path, capsys):
    rows = [("Z1", "C1_capacity", "inconsistent", "module_groups[0].count", "5"),
            ("Z1", "C1_capacity", "inconsistent", "module_groups[0].wattage_w", "500"),
            ("Z1", "C1_capacity", "inconsistent", "stated_capacity_kw", "3"),
            ("Z1", "C3_net_cost", "consistent", "gross_total", "165850"),
            ("Z1", "C3_net_cost", "consistent", "subsidy_central", "85800"),
            ("Z1", "C3_net_cost", "consistent", "net_cost", "80050"),
            ("Z1", "C2_central_subsidy", "consistent", "module_groups[0].count", "5"),
            ("Z1", "C2_central_subsidy", "consistent", "module_groups[0].wattage_w", "500"),
            ("Z1", "C2_central_subsidy", "consistent", "subsidy_central", "85800")]
    path = tmp_path / "a.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["doc", "check_id", "status", "operand", "value"])
        w.writerows(rows)
    recheck["main"]([str(path)])
    out = capsys.readouterr().out
    assert "C1_capacity: agree 1, disagree 0" in out and "C3_net_cost: agree 1, disagree 0" in out
    assert "C2_central_subsidy: agree 0, disagree 1" in out  # 2.5 kWp gives Rs 69,000, not 85,800
    assert "85800" not in out  # values only with --show-values
