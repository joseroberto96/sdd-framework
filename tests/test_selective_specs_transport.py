#!/usr/bin/env python3
"""Pruebas para la selección acotada por módulo de specs formales y control fail-closed."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import context_sources
import state_queries
import yaml

if __package__:
    from .test_state_queries import fixture_state
    from .test_framework_corrections import runtime_context_root, runtime_module_docs, run_cli_query
else:
    from test_state_queries import fixture_state
    from test_framework_corrections import runtime_context_root, runtime_module_docs, run_cli_query


class TestSelectiveSpecsTransport(unittest.TestCase):
    """Verifica la selección por módulo, resolución transitiva y control fail-closed."""

    def _base_state(self, *, data_model: str = "n/a", api_contract: str = "n/a") -> dict:
        state = fixture_state()
        state["current_step"] = "implementation"
        state["work_tiers"] = {
            "M1": {
                "tier": "tier_1",
                "risk": "standard",
                "rationale": "módulo estándar",
                "state_reasons": ["módulo estándar"],
                "classified_at": "2026-09-09T00:00:00Z",
                "classified_by": "fixture",
            },
            "M2": {
                "tier": "tier_1",
                "risk": "standard",
                "rationale": "módulo estándar",
                "state_reasons": ["módulo estándar"],
                "classified_at": "2026-09-09T00:00:00Z",
                "classified_by": "fixture",
            },
        }
        state["module_specs"] = {
            "M1": {
                "data_model": data_model,
                "api_contract": api_contract,
                "justification": "fixture de prueba",
                "classified_at": "2026-09-09T00:00:00Z",
                "classified_by": "fixture",
            },
            "M2": {
                "data_model": "n/a",
                "api_contract": "n/a",
                "justification": "sin specs",
                "classified_at": "2026-09-09T00:00:00Z",
                "classified_by": "fixture",
            },
        }
        return state

    def test_isolated_broken_ref_in_valid_non_empty_yaml_blocks_gate(self):
        """Prueba decisiva: contenido OpenAPI válido y no vacío con un único $ref roto bloquea fail-closed."""
        state = self._base_state(api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_file.parent.mkdir(parents=True, exist_ok=True)
            # Contenido válido y no vacío con un único $ref roto
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "Users API", "version": "1.0.0"},
                "paths": {
                    "/api/v1/users": {
                        "get": {
                            "tags": ["M1"],
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {
                                        "application/json": {
                                            "schema": {"$ref": "#/components/schemas/NonExistentSchema"}
                                        }
                                    },
                                }
                            },
                        }
                    }
                },
                "components": {
                    "schemas": {
                        "User": {"type": "object", "properties": {"id": {"type": "string"}}}
                    }
                },
            }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)

            # El predicado _state_obligation_delivered debe devolver False
            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            self.assertFalse(result["required_context_complete"])

            # Cita la referencia rota exacta en validation_reasons de SPECS
            specs_ref = next((ref for ref in result["refs"] if ref.get("obligation") == "SPECS"), None)
            self.assertIsNotNone(specs_ref, "La obligación SPECS debe figurar en refs bloqueantes")
            self.assertIn(
                "api_contract contiene referencia rota: #/components/schemas/NonExistentSchema",
                specs_ref.get("validation_reasons", []),
            )

            # Corrección: cuando la referencia se resuelve, el gate pasa inmediatamente
            api_content["paths"]["/api/v1/users"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] = "#/components/schemas/User"
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            process_fixed = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process_fixed.returncode, 0, process_fixed.stderr)
            result_fixed = json.loads(process_fixed.stdout)
            self.assertEqual(result_fixed["gate_status"], "PASS")
            self.assertTrue(result_fixed["required_context_complete"])
        finally:
            holder.cleanup()

    def test_isolated_external_ref_blocks_gate_in_hermetic_env(self):
        """Un $ref externo bloquea con NEEDS_INPUT en entorno hermético."""
        state = self._base_state(api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_file.parent.mkdir(parents=True, exist_ok=True)
            external_ref = "https://schema.example.com/user.json"
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "Users API", "version": "1.0.0"},
                "paths": {
                    "/api/v1/users": {
                        "get": {
                            "tags": ["M1"],
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {
                                        "application/json": {
                                            "schema": {"$ref": external_ref}
                                        }
                                    },
                                }
                            },
                        }
                    }
                },
            }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)

            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            self.assertFalse(result["required_context_complete"])
            specs_ref = next((ref for ref in result["refs"] if ref.get("obligation") == "SPECS"), None)
            self.assertIsNotNone(specs_ref)
            self.assertIn(
                f"api_contract contiene referencia externa no resuelta en entorno hermético: {external_ref}",
                specs_ref.get("validation_reasons", []),
            )
        finally:
            holder.cleanup()

    def test_module_exclusion_data_model_and_api_contract(self):
        """M1 excluye entidades y operaciones de M2 sin relación."""
        state = self._base_state(data_model="required", api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            dm_file = root / "_project" / "02_technical_plan" / "data_model.md"
            dm_file.parent.mkdir(parents=True, exist_ok=True)
            dm_file.write_text(
                "# Modelo de Datos\n\n"
                "## Entidades\n\n"
                "### EntidadM1\n"
                "- Módulo propietario: M1\n"
                "- Campos: id\n\n"
                "### EntidadM2\n"
                "- Módulo propietario: M2\n"
                "- Campos: id\n\n"
                "## Restricciones e invariantes del modelo\n"
                "- Invariante global\n",
                encoding="utf-8",
            )
            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "Multi API", "version": "1.0.0"},
                "paths": {
                    "/api/v1/m1_resource": {
                        "get": {"tags": ["M1"], "responses": {"200": {"description": "OK"}}}
                    },
                    "/api/v1/m2_resource": {
                        "get": {"tags": ["M2"], "responses": {"200": {"description": "OK"}}}
                    },
                },
            }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "PASS")

            # Verificar contenido entregado en items
            dm_items = [item["content"] for item in result["data"]["items"] if item.get("selector", "").startswith("module_data_model")]
            dm_delivered = "".join(dm_items)
            self.assertIn("EntidadM1", dm_delivered)
            self.assertNotIn("EntidadM2", dm_delivered)

            api_items = [item["content"] for item in result["data"]["items"] if item.get("selector", "").startswith("module_api_contract")]
            api_delivered = "".join(api_items)
            self.assertIn("/api/v1/m1_resource", api_delivered)
            self.assertNotIn("/api/v1/m2_resource", api_delivered)
        finally:
            holder.cleanup()

    def test_connected_entities_in_er_diagram_are_preserved(self):
        """Entidad de otro módulo conectada mediante Mermaid erDiagram se preserva."""
        state = self._base_state(data_model="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            dm_file = root / "_project" / "02_technical_plan" / "data_model.md"
            dm_file.parent.mkdir(parents=True, exist_ok=True)
            dm_file.write_text(
                "# Modelo de Datos\n\n"
                "## Diagrama ER\n\n"
                "```mermaid\n"
                "erDiagram\n"
                "    PEDIDO ||--o{ CLIENTE : realiza\n"
                "```\n\n"
                "## Entidades\n\n"
                "### Pedido\n"
                "- Módulo propietario: M1\n\n"
                "### Cliente\n"
                "- Módulo propietario: M2\n\n"
                "### Proveedor\n"
                "- Módulo propietario: M2\n\n"
                "## Restricciones e invariantes del modelo\n"
                "- Regla\n",
                encoding="utf-8",
            )
            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "PASS")

            dm_items = [item["content"] for item in result["data"]["items"] if item.get("selector", "").startswith("module_data_model")]
            dm_delivered = "".join(dm_items)
            self.assertIn("### Pedido", dm_delivered)
            self.assertIn("### Cliente", dm_delivered, "Cliente debe incluirse por arista en erDiagram")
            self.assertNotIn("### Proveedor", dm_delivered, "Proveedor no tiene relación y debe excluirse")
        finally:
            holder.cleanup()

    def test_shared_path_operations_filtered_by_module(self):
        """Path compartido extrae solo el método correspondiente al módulo activo y conserva parameters."""
        state = self._base_state(api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_file.parent.mkdir(parents=True, exist_ok=True)
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "Shared API", "version": "1.0.0"},
                "paths": {
                    "/api/v1/items": {
                        "parameters": [{"name": "org_id", "in": "header", "required": True}],
                        "get": {"tags": ["M1"], "responses": {"200": {"description": "List"}}},
                        "post": {"tags": ["M2"], "responses": {"201": {"description": "Create"}}},
                    }
                },
            }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "PASS")

            api_items = [item["content"] for item in result["data"]["items"] if item.get("selector", "").startswith("module_api_contract")]
            api_delivered = yaml.safe_load("".join(api_items))

            self.assertIn("/api/v1/items", api_delivered["paths"])
            item_ops = api_delivered["paths"]["/api/v1/items"]
            self.assertIn("get", item_ops)
            self.assertNotIn("post", item_ops, "POST pertenece a M2 y no debe entregarse a M1")
            self.assertIn("parameters", item_ops, "Parameters a nivel de path deben conservarse")
        finally:
            holder.cleanup()

    def test_conflicting_tags_and_x_module_raises_source_ambiguous(self):
        """Tags y x-module contradictorios lanzan QueryError SOURCE_AMBIGUOUS."""
        content = yaml.safe_dump({
            "openapi": "3.1.0",
            "info": {"title": "Conflict API", "version": "1.0.0"},
            "paths": {
                "/api/v1/conflict": {
                    "get": {
                        "tags": ["M1"],
                        "x-module": "M2",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        })
        with self.assertRaises(state_queries.QueryError) as ctx:
            context_sources._api_contract_section(content, "M1")
        self.assertEqual(ctx.exception.code, "SOURCE_AMBIGUOUS")

    def test_transitive_ref_closure_and_cycle_prevention(self):
        """Resuelve schemas transitivos alcanzables, evita ciclos y descarta huérfanos de otros módulos."""
        api_content = {
            "openapi": "3.1.0",
            "info": {"title": "Cycle API", "version": "1.0.0"},
            "paths": {
                "/api/v1/orders": {
                    "get": {
                        "tags": ["M1"],
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Order"}
                                    }
                                },
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "Order": {
                        "type": "object",
                        "properties": {
                            "item": {"$ref": "#/components/schemas/OrderItem"},
                        },
                    },
                    "OrderItem": {
                        "type": "object",
                        "properties": {
                            "product": {"$ref": "#/components/schemas/Product"},
                        },
                    },
                    "Product": {
                        "type": "object",
                        "properties": {
                            "order_parent": {"$ref": "#/components/schemas/Order"},  # Ciclo
                        },
                    },
                    "OrphanM2": {
                        "type": "object",
                        "properties": {"unused": {"type": "string"}},
                    },
                }
            },
        }
        content_str = yaml.safe_dump(api_content, sort_keys=False)
        selected_yaml, selector, problems = context_sources._api_contract_section(content_str, "M1")
        self.assertEqual(problems, [])
        parsed = yaml.safe_load(selected_yaml)

        schemas = parsed.get("components", {}).get("schemas", {})
        self.assertIn("Order", schemas)
        self.assertIn("OrderItem", schemas)
        self.assertIn("Product", schemas)
        self.assertNotIn("OrphanM2", schemas, "OrphanM2 no está referenciado y debe ser excluido")

    def test_empty_selection_fails_closed(self):
        """Archivo presente sin entidades ni paths para el módulo produce empty_source y NEEDS_INPUT."""
        state = self._base_state(data_model="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            dm_file = root / "_project" / "02_technical_plan" / "data_model.md"
            dm_file.parent.mkdir(parents=True, exist_ok=True)
            # data_model tiene entidades, pero todas pertenecen a M2
            dm_file.write_text(
                "# Modelo de Datos\n\n"
                "## Entidades\n\n"
                "### EntidadM2\n"
                "- Módulo propietario: M2\n",
                encoding="utf-8",
            )
            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)

            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            self.assertFalse(result["required_context_complete"])

            dm_ref = next((ref for ref in result["refs"] if ref.get("section") == "data_model_spec"), None)
            self.assertIsNotNone(dm_ref)
            self.assertEqual(dm_ref.get("reason"), "empty_source")

            specs_ref = next((ref for ref in result["refs"] if ref.get("obligation") == "SPECS"), None)
            self.assertIsNotNone(specs_ref)
            self.assertIn("falta contenido real de data_model", specs_ref.get("validation_reasons", []))
        finally:
            holder.cleanup()

    def test_na_classification_bypasses_specs_without_forcing_global_pass(self):
        """data_model y api_contract n/a con justificación no bloquean el gate."""
        state = self._base_state(data_model="n/a", api_contract="n/a")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "PASS")
            self.assertTrue(result["required_context_complete"])
            self.assertFalse(any(ref.get("obligation") == "SPECS" for ref in result["refs"]))
        finally:
            holder.cleanup()

    def test_cursor_invalidation_on_selection_source_modification(self):
        """Modificar 11_file_dependencies.md o 04_active_scope.md invalida el cursor con STALE_CURSOR."""
        state = self._base_state(api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_file.parent.mkdir(parents=True, exist_ok=True)
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "API", "version": "1.0.0"},
                "paths": {
                    "/api/v1/users": {
                        "get": {
                            "tags": ["M1"],
                            "responses": {"200": {"description": "OK"}},
                        }
                    },
                },
            }
            for i in range(50):
                api_content["paths"][f"/api/v1/resource_{i}"] = {
                    "get": {
                        "tags": ["M1"],
                        "description": "Texto descriptivo largo para forzar la paginación del documento OpenAPI " * 20,
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            # Primera consulta para obtener cursor
            process1 = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process1.returncode, 0, process1.stderr)
            res1 = json.loads(process1.stdout)
            self.assertFalse(res1["complete"], "Debe paginar al superar el límite de bytes")
            cursor = res1["next_cursor"]
            self.assertIsNotNone(cursor)

            # Modificar 11_file_dependencies.md (fuente de selección precargada)
            dep_file = root / "_project" / "11_file_dependencies.md"
            with dep_file.open("a", encoding="utf-8") as stream:
                stream.write("\n# Modificación para invalidar digest\n")

            # Reconsultar con cursor anterior debe fallar con STALE_CURSOR
            process2 = run_cli_query(root, "implementation", "M1", cursor=cursor)
            self.assertNotEqual(process2.returncode, 0)
            self.assertIn("STALE_CURSOR", process2.stderr + process2.stdout)
        finally:
            holder.cleanup()

    def test_canonical_markdown_entity_owner_recognized(self):
        """Reconoce el formato canónico de plantilla `- **Módulo propietario:** M1`."""
        state = self._base_state(data_model="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            dm_file = root / "_project" / "02_technical_plan" / "data_model.md"
            dm_file.parent.mkdir(parents=True, exist_ok=True)
            dm_file.write_text(
                "# Modelo de Datos\n\n"
                "## Entidades\n\n"
                "### Usuario\n"
                "- **Módulo propietario:** M1\n"
                "- **Campos:**\n"
                "  - `id: uuid`\n\n"
                "### Factura\n"
                "- **Módulo propietario:** M2\n"
                "- **Campos:**\n"
                "  - `id: uuid`\n\n"
                "## Restricciones e invariantes del modelo\n"
                "- Invariante 1\n",
                encoding="utf-8",
            )
            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "PASS")

            dm_items = [item["content"] for item in result["data"]["items"] if item.get("selector", "").startswith("module_data_model")]
            dm_delivered = "".join(dm_items)
            self.assertIn("### Usuario", dm_delivered, "Usuario debe ser reconocido como propiedad de M1")
            self.assertNotIn("### Factura", dm_delivered, "Factura pertenece a M2 y no debe entregarse a M1")
        finally:
            holder.cleanup()

    def test_contracts_scoping_prevents_route_contamination(self):
        """Un contrato con sección M2 y puntero a /private no contamina la selección de M1."""
        state = self._base_state(api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            # 03_contracts.md declara sección de M2 con puntero a /private
            contracts_file = root / "_project" / "03_contracts.md"
            contracts_file.write_text(
                "# Contratos — Proyecto\n\n"
                "## M1 — Core\n"
                "- Puntero: `api_contract.yaml#paths./public`\n\n"
                "## M2 — Admin\n"
                "- Puntero: `api_contract.yaml#paths./private`\n",
                encoding="utf-8",
            )
            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_file.parent.mkdir(parents=True, exist_ok=True)
            # Operaciones sin tags para depender de la declaración en contratos
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "API", "version": "1.0.0"},
                "paths": {
                    "/public": {
                        "get": {"responses": {"200": {"description": "Public"}}}
                    },
                    "/private": {
                        "get": {"responses": {"200": {"description": "Private"}}}
                    },
                },
            }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "PASS")

            api_items = [item["content"] for item in result["data"]["items"] if item.get("selector", "").startswith("module_api_contract")]
            api_delivered = yaml.safe_load("".join(api_items))
            self.assertIn("/public", api_delivered["paths"])
            self.assertNotIn("/private", api_delivered["paths"], "/private pertenece a M2 y no debe contaminar a M1")
        finally:
            holder.cleanup()

    def test_operation_security_schemes_preserved_and_fail_closed(self):
        """Operación con security conserva securitySchemes o falla closed si el esquema no está definido."""
        state = self._base_state(api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_file.parent.mkdir(parents=True, exist_ok=True)

            # Caso 1: Bearer no definido en components.securitySchemes -> spec_problems y NEEDS_INPUT
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "Secure API", "version": "1.0.0"},
                "paths": {
                    "/secure": {
                        "get": {
                            "tags": ["M1"],
                            "security": [{"Bearer": []}],
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
                "components": {
                    "securitySchemes": {
                        "OtherScheme": {"type": "http", "scheme": "basic"}
                    }
                },
            }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            process1 = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process1.returncode, 0, process1.stderr)
            res1 = json.loads(process1.stdout)
            self.assertEqual(res1["gate_status"], "NEEDS_INPUT")
            specs_ref = next((ref for ref in res1["refs"] if ref.get("obligation") == "SPECS"), None)
            self.assertIsNotNone(specs_ref)
            self.assertIn(
                "api_contract contiene esquema de seguridad no definido: Bearer",
                specs_ref.get("validation_reasons", []),
            )

            # Caso 2: Bearer definido en components.securitySchemes -> PASS y components.securitySchemes.Bearer entregado
            api_content["components"]["securitySchemes"]["Bearer"] = {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            process2 = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process2.returncode, 0, process2.stderr)
            res2 = json.loads(process2.stdout)
            self.assertEqual(res2["gate_status"], "PASS")

            api_items = [item["content"] for item in res2["data"]["items"] if item.get("selector", "").startswith("module_api_contract")]
            api_delivered = yaml.safe_load("".join(api_items))
            sec_schemes = api_delivered.get("components", {}).get("securitySchemes", {})
            self.assertIn("Bearer", sec_schemes, "components.securitySchemes.Bearer debe conservarse")
            self.assertEqual(sec_schemes["Bearer"]["type"], "http")
            self.assertNotIn("OtherScheme", sec_schemes, "OtherScheme no está referenciado y debe ser excluido")
        finally:
            holder.cleanup()

    def test_path_level_servers_and_metadata_preserved(self):
        """Path con servers, summary o description propios conserva esa configuración al reconstruirse."""
        state = self._base_state(api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_file.parent.mkdir(parents=True, exist_ok=True)
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "API with Servers", "version": "1.0.0"},
                "paths": {
                    "/upload": {
                        "summary": "Resumen del path",
                        "description": "Descripción detallada del endpoint de subida",
                        "servers": [
                            {"url": "https://upload.example.com/v1", "description": "Servidor dedicado de subida"}
                        ],
                        "post": {
                            "tags": ["M1"],
                            "responses": {"200": {"description": "Subida completada"}},
                        },
                    }
                },
            }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "PASS")

            api_items = [item["content"] for item in result["data"]["items"] if item.get("selector", "").startswith("module_api_contract")]
            api_delivered = yaml.safe_load("".join(api_items))
            path_item = api_delivered.get("paths", {}).get("/upload", {})
            self.assertIn("servers", path_item, "servers a nivel de path debe conservarse")
            self.assertEqual(path_item["servers"][0]["url"], "https://upload.example.com/v1")
            self.assertEqual(path_item.get("summary"), "Resumen del path")
            self.assertEqual(path_item.get("description"), "Descripción detallada del endpoint de subida")
        finally:
            holder.cleanup()

    def test_multipage_complete_reconstruction(self):
        """Reconstruye fielmente un spec extenso distribuido a través de múltiples páginas de contexto."""
        state = self._base_state(api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_file.parent.mkdir(parents=True, exist_ok=True)
            # Construir un contrato OpenAPI con 80 endpoints para exceder el límite de página (65536 bytes)
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "Large Multi-Page API", "version": "1.0.0"},
                "paths": {},
                "components": {
                    "schemas": {
                        f"ResourceModel_{i}": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "created_at": {"type": "string", "format": "date-time"},
                            },
                        }
                        for i in range(80)
                    }
                },
            }
            for i in range(80):
                api_content["paths"][f"/api/v1/resource_{i}"] = {
                    "get": {
                        "tags": ["M1"],
                        "summary": f"Resumen para recurso {i}",
                        "description": f"Detalle extenso del recurso número {i} con documentación exhaustiva " * 15,
                        "responses": {
                            "200": {
                                "description": "Respuesta exitosa",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": f"#/components/schemas/ResourceModel_{i}"}
                                    }
                                },
                            }
                        },
                    }
                }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            # Travesía completa a través de páginas mediante cursors
            all_fragments: list[dict] = []
            cursor = None
            page_count = 0
            while True:
                process = run_cli_query(root, "implementation", "M1", cursor=cursor)
                self.assertEqual(process.returncode, 0, process.stderr)
                page_data = json.loads(process.stdout)
                page_count += 1

                for item in page_data["data"]["items"]:
                    if item.get("selector", "").startswith("module_api_contract"):
                        all_fragments.append(item)

                if page_data["complete"]:
                    break
                cursor = page_data["next_cursor"]
                self.assertIsNotNone(cursor, "next_cursor debe estar presente cuando complete es False")

            self.assertGreater(page_count, 1, "La prueba debe abarcar al menos 2 páginas para validar paginación")

            # Reconstruir el spec a partir de los fragmentos ordenados
            all_fragments.sort(key=lambda it: it.get("fragment_index", 0))
            reconstructed_yaml = "".join(it["content"] for it in all_fragments)

            # Validar que el YAML reconstruido es válido y contiene todos los paths y schemas
            parsed = yaml.safe_load(reconstructed_yaml)
            self.assertEqual(len(parsed["paths"]), 80, "Todos los 80 endpoints deben estar presentes en el spec reconstruido")
            self.assertEqual(
                len(parsed.get("components", {}).get("schemas", {})),
                80,
                "Todos los 80 esquemas transitivos deben estar presentes",
            )
            for i in range(80):
                self.assertIn(f"/api/v1/resource_{i}", parsed["paths"])
                self.assertIn(f"ResourceModel_{i}", parsed["components"]["schemas"])
        finally:
            holder.cleanup()

    def test_nested_contracts_subsections_do_not_leak_to_sibling_modules(self):
        """Bajo encabezado padre '## Interfaces', subsección M2 con /private no se atribuye al padre ni a M1."""
        state = self._base_state(api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            contracts_file = root / "_project" / "03_contracts.md"
            contracts_file.write_text(
                "# Contratos — Interfaces del Sistema\n\n"
                "## Interfaces\n"
                "Descripción general del contenedor de interfaces...\n\n"
                "### Módulo M1\n"
                "- **Módulo propietario:** M1\n"
                "- Puntero: `api_contract.yaml#paths./public`\n\n"
                "### Módulo M2\n"
                "- **Módulo propietario:** M2\n"
                "- Puntero: `api_contract.yaml#paths./private`\n",
                encoding="utf-8",
            )
            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_file.parent.mkdir(parents=True, exist_ok=True)
            # Operaciones sin tags para verificar aislamiento estricto desde contratos
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "Nested API", "version": "1.0.0"},
                "paths": {
                    "/public": {
                        "get": {"responses": {"200": {"description": "OK"}}}
                    },
                    "/private": {
                        "get": {"responses": {"200": {"description": "OK"}}}
                    },
                },
            }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "PASS")

            api_items = [item["content"] for item in result["data"]["items"] if item.get("selector", "").startswith("module_api_contract")]
            api_delivered = yaml.safe_load("".join(api_items))
            self.assertIn("/public", api_delivered["paths"])
            self.assertNotIn("/private", api_delivered["paths"], "Subsección M2 bajo encabezado padre no debe atribuirse a M1")
        finally:
            holder.cleanup()

    def test_foreign_module_scope_not_consumed_by_active_module(self):
        """Scope perteneciente a otro módulo (## Módulo actual: M2) no es consumido por M1."""
        sources = {
            "_project/04_active_scope.md": (
                "# Alcance Activo\n\n"
                "## Módulo actual: M2\n"
                "- Puntero: `api_contract.yaml#paths./private`\n"
                "- GET /secret\n"
                "- Entidad: Secret\n"
            ),
            "_project/03_contracts.md": (
                "# Contratos — Proyecto\n\n"
                "## M1 — Core\n"
                "- Puntero: `api_contract.yaml#paths./public`\n"
            ),
        }
        endpoints_m1 = context_sources._declared_endpoints_from_sources("M1", sources)
        entities_m1 = context_sources._declared_entities_from_sources("M1", sources)

        # Verificación directa de los extractores:
        self.assertNotIn(("*", "/private"), endpoints_m1, "/private proviene de scope de M2 y debe ignorarse")
        self.assertNotIn(("get", "/secret"), endpoints_m1, "/secret proviene de scope de M2 y debe ignorarse")
        self.assertNotIn("Secret", entities_m1, "Entidad Secret proviene de scope de M2 y debe ignorarse")
        self.assertIn(("*", "/public"), endpoints_m1, "/public proviene de M1 y debe conservarse")

        # Verificación integrada a través de CLI:
        state = self._base_state(data_model="required", api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            # Sobrescribir scope con M2
            scope_file = root / "_project" / "04_active_scope.md"
            scope_file.write_text(sources["_project/04_active_scope.md"], encoding="utf-8")

            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_file.parent.mkdir(parents=True, exist_ok=True)
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "API", "version": "1.0.0"},
                "paths": {
                    "/public": {"get": {"responses": {"200": {"description": "OK"}}}},
                    "/private": {"get": {"responses": {"200": {"description": "OK"}}}},
                    "/secret": {"get": {"responses": {"200": {"description": "OK"}}}},
                },
            }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)

            # El gate debe detectar conflicto de scope y bloquear en NEEDS_INPUT
            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            scope_ref = next((ref for ref in result["refs"] if ref.get("section") == "scope"), None)
            self.assertIsNotNone(scope_ref)
            self.assertEqual(scope_ref.get("reason"), "scope_conflict")

            # Ningún ítem entregado a M1 debe contener /private o /secret
            api_items = [item["content"] for item in result["data"]["items"] if item.get("selector", "").startswith("module_api_contract")]
            api_delivered_str = "".join(api_items)
            self.assertNotIn("/private", api_delivered_str)
            self.assertNotIn("/secret", api_delivered_str)
        finally:
            holder.cleanup()

    def test_explicit_discrepancy_non_m_digit_module_scope_and_dependencies_discarded(self):
        """Discrepancia explícita con módulos no 'M+dígito' (Billing, F6_fundamental_screener) descarta contenido en endpoints y entidades."""
        # Caso 1: Scope y dependencias declarados para 'Billing' consultados desde 'M1'
        sources_billing = {
            "_project/04_active_scope.md": (
                "# Alcance Activo\n\n"
                "## Módulo actual: Billing\n"
                "GET /private\n"
                "Entidad: Secret\n"
            ),
            "_project/11_file_dependencies.md": (
                "# Dependencias de Archivos\n\n"
                "## Módulo actual: Billing\n"
                "api_contract: POST /billing/charge\n"
                "data_model: Invoice\n"
            ),
        }
        endpoints_m1 = context_sources._declared_endpoints_from_sources("M1", sources_billing)
        entities_m1 = context_sources._declared_entities_from_sources("M1", sources_billing)

        self.assertNotIn(("get", "/private"), endpoints_m1, "/private no debe ser consumido por M1 desde scope de Billing")
        self.assertNotIn(("post", "/billing/charge"), endpoints_m1, "/billing/charge no debe ser consumido por M1 desde dep de Billing")
        self.assertNotIn("Secret", entities_m1, "Entidad Secret no debe ser consumida por M1 desde scope de Billing")
        self.assertNotIn("Invoice", entities_m1, "Entidad Invoice no debe ser consumida por M1 desde dep de Billing")

        # Verificación positiva cuando se consulta directamente para Billing
        endpoints_billing = context_sources._declared_endpoints_from_sources("Billing", sources_billing)
        entities_billing = context_sources._declared_entities_from_sources("Billing", sources_billing)
        self.assertIn(("get", "/private"), endpoints_billing)
        self.assertIn(("post", "/billing/charge"), endpoints_billing)
        self.assertIn("Secret", entities_billing)
        self.assertIn("Invoice", entities_billing)

        # Caso 2: Scope y dependencias declarados para 'F6_fundamental_screener' consultados desde 'M1'
        sources_f6 = {
            "_project/04_active_scope.md": (
                "# Alcance Activo\n\n"
                "## Módulo actual: F6_fundamental_screener\n"
                "GET /screener_data\n"
                "Entidad: ScreenerResult\n"
            ),
            "_project/11_file_dependencies.md": (
                "# Dependencias de Archivos\n\n"
                "## Módulo actual: F6_fundamental_screener\n"
                "api_contract: GET /screener/filters\n"
                "data_model: FilterConfig\n"
            ),
        }
        endpoints_f6_m1 = context_sources._declared_endpoints_from_sources("M1", sources_f6)
        entities_f6_m1 = context_sources._declared_entities_from_sources("M1", sources_f6)

        self.assertNotIn(("get", "/screener_data"), endpoints_f6_m1, "/screener_data no debe ser consumido por M1 desde scope de F6")
        self.assertNotIn(("get", "/screener/filters"), endpoints_f6_m1, "/screener/filters no debe ser consumido por M1 desde dep de F6")
        self.assertNotIn("ScreenerResult", entities_f6_m1, "ScreenerResult no debe ser consumido por M1 desde scope de F6")
        self.assertNotIn("FilterConfig", entities_f6_m1, "FilterConfig no debe ser consumido por M1 desde dep de F6")

        # Verificación positiva para F6_fundamental_screener
        endpoints_f6 = context_sources._declared_endpoints_from_sources("F6_fundamental_screener", sources_f6)
        entities_f6 = context_sources._declared_entities_from_sources("F6_fundamental_screener", sources_f6)
        self.assertIn(("get", "/screener_data"), endpoints_f6)
        self.assertIn(("get", "/screener/filters"), endpoints_f6)
        self.assertIn("ScreenerResult", entities_f6)
        self.assertIn("FilterConfig", entities_f6)

        # Caso 3: Integrado end-to-end con CLI para M1 frente a scope de Billing
        state = self._base_state(data_model="required", api_contract="required")
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            scope_file = root / "_project" / "04_active_scope.md"
            scope_file.write_text(sources_billing["_project/04_active_scope.md"], encoding="utf-8")
            dep_file = root / "_project" / "11_file_dependencies.md"
            dep_file.write_text(sources_billing["_project/11_file_dependencies.md"], encoding="utf-8")

            api_file = root / "_project" / "02_technical_plan" / "api_contract.yaml"
            api_file.parent.mkdir(parents=True, exist_ok=True)
            api_content = {
                "openapi": "3.1.0",
                "info": {"title": "API", "version": "1.0.0"},
                "paths": {
                    "/private": {"get": {"responses": {"200": {"description": "OK"}}}},
                    "/billing/charge": {"post": {"responses": {"200": {"description": "OK"}}}},
                },
            }
            api_file.write_text(yaml.safe_dump(api_content, sort_keys=False), encoding="utf-8")

            dm_file = root / "_project" / "02_technical_plan" / "data_model.md"
            dm_file.write_text(
                "# Modelo de Datos\n\n"
                "## Entidades\n\n"
                "### Secret\n"
                "- **Módulo propietario:** Billing\n"
                "- Campos: id, key\n\n"
                "### Invoice\n"
                "- **Módulo propietario:** Billing\n"
                "- Campos: id, amount\n",
                encoding="utf-8",
            )

            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)

            # Conflicto de scope detectado
            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            scope_ref = next((ref for ref in result["refs"] if ref.get("section") == "scope"), None)
            self.assertIsNotNone(scope_ref)
            self.assertEqual(scope_ref.get("reason"), "scope_conflict")

            # Ningún ítem entregado debe incorporar endpoints o entidades de Billing
            api_items = [item["content"] for item in result["data"]["items"] if item.get("selector", "").startswith("module_api_contract")]
            api_delivered_str = "".join(api_items)
            self.assertNotIn("/private", api_delivered_str)
            self.assertNotIn("/billing/charge", api_delivered_str)

            dm_items = [item["content"] for item in result["data"]["items"] if item.get("selector", "").startswith("module_data_model")]
            dm_delivered_str = "".join(dm_items)
            self.assertNotIn("### Secret", dm_delivered_str)
            self.assertNotIn("### Invoice", dm_delivered_str)
        finally:
            holder.cleanup()


if __name__ == "__main__":
    unittest.main()
