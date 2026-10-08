import copy
import json
from pathlib import Path

import pytest

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "quote_contract_v0.json").read_text())


def field(value, text="synthetic", page=1):
    return {"value": value, "evidence_text": text, "page": page}


@pytest.fixture
def quote():
    return copy.deepcopy(_FIXTURE)
