import pytest
from iagis.skill_runtime import SkillValidationError, execute_skill, validate_skill_source

IN='{"type":"object","properties":{"x":{"type":"integer"}},"required":["x"],"additionalProperties":false}'
OUT='{"type":"object","properties":{"a":{"type":"integer"}},"required":["a"],"additionalProperties":false}'

def test_skill_contract_executes_json_io():
    assert execute_skill('def run(input_data):\n    return {"a": input_data["x"] + 1}',IN,OUT,{"x":2}) == {"a":3}

def test_skill_rejects_imports_and_invalid_output():
    with pytest.raises(SkillValidationError): validate_skill_source('import os\ndef run(input_data): return {}')
    with pytest.raises(SkillValidationError):
        execute_skill('def run(input_data):\n    return {"wrong": 1}',IN,OUT,{"x":2})
