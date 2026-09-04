import json

import pytest

from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.infrastructure.persistence.repositories import _dump, _load


def test_wire_format_uses_stable_versioned_tags_not_python_paths():
    encoded = _dump(CommandResult("c", CommandStatus.APPLIED, "ok", {"spoken": "sí"}))
    tree = json.loads(encoded)
    assert tree["$type"] == "command-result/v1"
    assert "math_tutor" not in encoded
    assert _load(encoded) == CommandResult("c", CommandStatus.APPLIED, "ok", {"spoken": "sí"})


def test_wire_decoder_rejects_unknown_type_tag():
    with pytest.raises(ValueError, match="unknown durable type"):
        _load('{"$type":"future/v9","fields":{}}')
