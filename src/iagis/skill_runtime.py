"""Contrato mínimo para skills Python administradas e executadas como subprocessos."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from typing import Any

_BANNED_NAMES = {"open", "eval", "exec", "compile", "__import__", "breakpoint", "input"}
_BANNED_NODES = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef)


class SkillValidationError(ValueError):
    pass


def _validate_schema(schema: dict[str, Any], value: Any) -> None:
    """Subconjunto intencional: objeto, propriedades, required e tipos escalares/lista."""
    types = {"object": dict, "array": list, "string": str, "integer": int,
             "number": (int, float), "boolean": bool, "null": type(None)}
    expected = schema.get("type")
    if expected not in types or not isinstance(value, types[expected]):
        raise SkillValidationError(f"valor não corresponde ao tipo {expected}")
    if expected == "object":
        for key in schema.get("required", []):
            if key not in value:
                raise SkillValidationError(f"campo obrigatório ausente: {key}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(schema.get("properties", {}))
            if extra:
                raise SkillValidationError("campos extras não permitidos")
        for key, child in schema.get("properties", {}).items():
            if key in value:
                _validate_schema(child, value[key])


def validate_skill_source(source: str) -> None:
    if len(source.encode()) > 50_000:
        raise SkillValidationError("skill excede 50 KB")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SkillValidationError(f"Python inválido: linha {exc.lineno}") from exc
    if any(isinstance(node, _BANNED_NODES) for node in ast.walk(tree)):
        raise SkillValidationError("imports, classes e escopo global/nonlocal não são permitidos")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BANNED_NAMES:
            raise SkillValidationError(f"chamada proibida: {node.func.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise SkillValidationError("atributos internos não são permitidos")
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(functions) != 1 or functions[0].name != "run" or len(functions[0].args.args) != 1:
        raise SkillValidationError("defina exatamente: def run(input_data): ...")


def execute_skill(source: str, input_schema: str, output_schema: str,
                  payload: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
    """Reduz risco, mas não é sandbox: somente administradores devem importar código confiável."""
    validate_skill_source(source)
    in_schema, out_schema = json.loads(input_schema), json.loads(output_schema)
    _validate_schema(in_schema, payload)
    wrapper = source + "\n_result = run(" + repr(payload) + ")\nprint(__import__('json').dumps(_result))\n"
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
    process = subprocess.run(
        [sys.executable, "-I", "-S", "-c", wrapper], capture_output=True, text=True,
        timeout=timeout, env=env, check=False,
    )
    if process.returncode:
        raise RuntimeError(f"skill falhou com código {process.returncode}")
    if len(process.stdout.encode()) > 100_000:
        raise RuntimeError("saída da skill excede 100 KB")
    result = json.loads(process.stdout)
    _validate_schema(out_schema, result)
    if not isinstance(result, dict):
        raise RuntimeError("a saída da skill deve ser objeto JSON")
    return result
