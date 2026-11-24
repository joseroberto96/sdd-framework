#!/usr/bin/env python3
"""Resumen pequeño y honesto de resultados de validadores."""

from __future__ import annotations

from typing import Any


def _strings(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return list(value)


def summarize_result(*, tool: str, exit_code: int | None, parsed: dict | None,
                     evidence_ref: str) -> dict:
    """Resume una comprobación sin inferir PASS desde texto libre."""
    failures: list[str] = []
    warnings: list[str] = []
    status = "ERROR"
    if not isinstance(tool, str) or not tool.strip():
        failures.append("tool inválido")
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        failures.append("evidence_ref inválido")
    if isinstance(exit_code, bool) or (exit_code is not None and not isinstance(exit_code, int)):
        failures.append("exit_code inválido")
        exit_value = None
    else:
        exit_value = exit_code
    if exit_value is None:
        failures.append("comprobación no ejecutada o timeout")
    elif not isinstance(parsed, dict):
        failures.append("resultado ausente o no estructurado")
    else:
        parsed_status = parsed.get("status")
        parsed_failures = _strings(parsed.get("failures"))
        parsed_warnings = _strings(parsed.get("warnings", []))
        if parsed_status not in {"pass", "fail", "error", "unavailable"}:
            failures.append("contrato de resultado incompleto: status")
        if parsed_failures is None:
            failures.append("contrato de resultado incompleto: failures")
        else:
            failures.extend(parsed_failures)
        if parsed_warnings is None:
            failures.append("contrato de resultado incompleto: warnings")
        else:
            warnings = parsed_warnings
        if not failures and exit_value == 0 and parsed_status == "pass":
            status = "PASS"
        elif exit_value != 0 or parsed_status == "fail":
            status = "FAIL"
        elif parsed_status == "unavailable":
            status = "UNAVAILABLE"
        else:
            status = "ERROR"
    if exit_value not in (None, 0) and not failures:
        failures.append(f"exit_code={exit_value}")
        status = "FAIL"
    return {
        "schema_version": 1,
        "tool": tool,
        "status": status,
        "exit_code": exit_value,
        "failure_count": len(failures),
        "failures": failures,
        "warnings": warnings,
        "evidence_ref": evidence_ref,
    }
