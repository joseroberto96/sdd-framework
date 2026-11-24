#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""action_contracts.py - Catálogo descriptivo y contratos consultables del motor de estado.

Plan 4.5 Lote 2:
- Reutiliza metadatos existentes en state_core (_REQUIRED_PAYLOAD_KEYS, _OPTIONAL_PAYLOAD_KEYS, _STRICT_PAYLOAD_KEYS).
- required_keys y optional_keys se DERIVAN en tiempo de ejecución desde state_core, no se transcriben.
- Solo define la información descriptiva que no existe en el motor: descripciones, tipos, condiciones, precondiciones y ejemplos sintéticos.
- Representación compartida entre CLI (state_manager describe) y MCP (describe_action).
"""
from __future__ import annotations

from typing import Any
from pathlib import Path
import sys

# Asegurar import de state_core
sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_core

ACTION_DESCRIPTIONS: dict[str, dict[str, Any]] = {
    "add_active_mcp": {
        "description": "Registra un servidor MCP activo en la lista active_mcps del estado.",
        "fields": {
            "name": {"type": "string", "description": "Nombre identificador del servidor MCP."},
            "config": {"type": "object", "description": "Configuración opcional del servidor MCP."},
        },
        "conditions": [],
        "preconditions": [],
        "example": {"name": "state_manager", "config": {}},
    },
    "add_closure_rejection": {
        "description": "Registra un rechazo de cierre de módulo para auditoría de calidad (activando criterio de muerte si se acumulan >=2).",
        "fields": {
            "module_id": {"type": "string", "description": "ID del módulo rechazado."},
            "reason": {
                "type": "string",
                "allowed_values": sorted(state_core.VALID_CLOSURE_REASONS),
                "description": "Motivo del rechazo de cierre.",
            },
        },
        "conditions": [
            "'reason' debe ser uno de los motivos de rechazo válidos.",
            "Rechaza un módulo 'dropped' (abandonado, terminal): su rechazo no se podría resolver con resolve_closure_rejections.",
        ],
        "preconditions": ["El módulo debe existir en el estado."],
        "example": {"module_id": "mod-test", "reason": "ac_incomplete"},
    },
    "add_module": {
        "description": "Añade un módulo nuevo al roadmap del proyecto en estado not_started (dentro de modules.pending).",
        "fields": {
            "id": {"type": "string", "description": "Identificador único del módulo (slug sin espacios)."},
            "stabilization": {"type": "boolean", "default": False, "description": "Indica si el módulo es de estabilización, permitiendo arrancarlo bajo criterio de parada activo (N4)."},
        },
        "conditions": ["'id' no debe estar previamente declarado en modules."],
        "preconditions": [],
        "example": {"id": "mod-autenticacion"},
    },
    "add_work_tier": {
        "description": "Asigna o clasifica el work tier de rigor (tier_0, tier_1, tier_2) para un módulo o tarea.",
        "fields": {
            "id": {"type": "string", "description": "ID del módulo o tarea ('bug:...' / 'task:...')."},
            "tier": {
                "type": "string",
                "allowed_values": ["tier_0", "tier_1", "tier_2"],
                "description": "Nivel de rigor según blast radius y reversibilidad.",
            },
            "rationale": {"type": "string", "description": "Justificación de la clasificación de rigor."},
            "classified_by": {"type": "string", "default": "manual", "description": "Agente o proceso que clasifica."},
            "requires_second_review": {"type": "boolean", "default": False, "description": "Obliga a segunda revisión independiente."},
            "handles_real_data": {"type": "boolean", "default": False, "description": "Indica si maneja datos reales o de producción."},
        },
        "conditions": ["Si 'id' no tiene prefijo 'task:' o 'bug:', debe ser un módulo declarado en modules."],
        "preconditions": ["El módulo debe existir declarado en el estado."],
        "example": {"id": "mod-test", "tier": "tier_1", "rationale": "Módulo con blast radius medio y reversibilidad alta."},
    },
    "amend_bug": {
        "description": "Enmienda un dato erróneo de un bug existente conservando el historial append-only.",
        "fields": {
            "id": {"type": "string", "description": "Identificador del bug (ej. 'BUG-001')."},
            "reason": {"type": "string", "description": "Motivo sustantivo de la enmienda (mínimo 20 caracteres)."},
            "description": {"type": "string", "description": "Nueva descripción enmendada."},
            "fix": {"type": "string", "description": "Nueva solución enmendada."},
            "affected_files": {"type": "array", "description": "Lista de rutas de archivos afectados enmendada."},
        },
        "conditions": [
            "Se debe proporcionar al menos uno de los campos enmendables: description, fix, affected_files.",
            "No se pueden enmendar campos que mueven gates como severity o status.",
            "module_id no se enmienda aquí: usa reassign_bug_module.",
        ],
        "preconditions": ["El bug debe existir en _bugs."],
        "example": {
            "id": "BUG-001",
            "reason": "Corrección de conteo de tests tras verificación en entorno aislado.",
            "description": "La suite de pruebas contiene 12 tests en lugar de 23.",
        },
    },
    "append_to_list_field": {
        "description": "Añade un valor a un campo de lista permitido del estado (como dependencies o architecture_constraints).",
        "fields": {
            "field": {
                "type": "string",
                "allowed_values": ["dependencies", "architecture_constraints", "active_mcps"],
                "description": "Nombre del campo de lista en el estado.",
            },
            "value": {"type": "any", "description": "Valor o elemento a añadir a la lista."},
            "unique": {"type": "boolean", "default": True, "description": "Si es True, omite valores ya presentes en la lista."},
        },
        "conditions": ["'field' debe ser un campo de lista reconocido en el estado."],
        "preconditions": [],
        "example": {"field": "dependencies", "value": "pytest>=7.0.0"},
    },
    "backfill_closure_rejection_provenance": {
        "description": "Registra evidencia de procedencia para resoluciones históricas de rechazos de cierre.",
        "fields": {
            "module_id": {"type": "string", "description": "ID del módulo."},
            "resolved_ids": {"type": "array", "description": "Lista de IDs de rechazos resueltos vinculados."},
            "resolution_event_ts": {"type": "string", "description": "Timestamp ISO del evento de resolución original."},
            "legacy_refs": {"type": "array", "description": "Referencias legacy asociadas."},
            "provenance_ids": {"type": "array", "description": "IDs de procedencia durante replay."},
        },
        "conditions": [],
        "preconditions": ["El módulo debe existir en el estado."],
        "example": {
            "module_id": "mod-test",
            "resolved_ids": ["CRR-0001"],
            "resolution_event_ts": "2026-09-15T12:00:00Z",
        },
    },
    "ciclo_finished": {
        "description": "Telemetría de finalización de un ciclo de trabajo de módulo.",
        "fields": {
            "module": {"type": "string", "description": "ID del módulo completado en el ciclo."},
            "result": {
                "type": "string",
                "allowed_values": ["ok", "paused", "blocked"],
                "description": "Resultado del ciclo.",
            },
            "mode": {
                "type": "string",
                "allowed_values": ["modulo", "fase", "paralelo"],
                "description": "Modo del driver /ciclo (opcional).",
            },
        },
        "conditions": ["'result' debe ser uno de: ok, paused, blocked.", "'mode', si se envía, debe ser uno de: modulo, fase, paralelo."],
        "preconditions": [],
        "example": {"module": "mod-test", "result": "ok"},
    },
    "ciclo_paused": {
        "description": "Telemetría de pausa de un ciclo de trabajo.",
        "fields": {
            "reason": {"type": "string", "default": "", "description": "Razón por la que se pausó el ciclo."},
        },
        "conditions": [],
        "preconditions": [],
        "example": {"reason": "Esperando aclaración de requisitos del builder."},
    },
    "acknowledge_state_delta": {
        "description": "Escape auditado del gate de estado respaldado (pre-commit): deja en la bitácora quién admite qué divergencia del estado y por qué. No muta el estado.",
        "fields": {
            "reason": {"type": "string", "description": "Por qué la divergencia es legítima (p. ej. migración)."},
            "approver": {"type": "string", "description": "Quién la aprueba (builder o rol)."},
            "paths": {"type": "array", "items": "string", "description": "Rutas LITERALES del estado que se admiten (sin comodines), tal como las imprime el gate."},
        },
        "conditions": ["'paths' no admite comodines (* ni ?).", "Solo cubre divergencias de estado; no excusa una bitácora truncada o reescrita."],
        "preconditions": [],
        "example": {"reason": "Migración de esquema sin eventos históricos.", "approver": "builder", "paths": ["module_statuses.mod-a"]},
    },
    "seed_snapshot": {
        "description": "Siembra las entidades anteriores al log (bugs, clarificaciones, decisiones, módulos, tiers, specs, dependencias, stack) con el commit de origen. Solo añade lo ausente; nunca sobrescribe.",
        "fields": {
            "source_commit": {"type": "string", "description": "Commit del que se toma el estado sembrado."},
            "reason": {"type": "string", "description": "Por qué se siembra (p. ej. ancla FW-252)."},
            "entities": {"type": "object", "description": "Forma en línea (compatible): entidades a sembrar, por colección. Omitir si se usa state_hash."},
            "state_hash": {"type": "string", "description": "Forma por referencia (preferida): sha256 del estado canónico en source_commit; el replay carga la base desde git y verifica el hash."},
        },
        "conditions": ["Exige 'entities' (en línea) o 'state_hash' (por referencia).", "Por referencia, si el clon no alcanza source_commit el replay lo dice y no siembra.", "'entities' solo admite colecciones sembrables (_bugs, _clarifications, _decisions, modules, module_statuses, work_tiers, module_specs, dependencies, architecture_constraints, stack).", "Un módulo ya presente en cualquier bucket de modules, 'dropped' incluido, no se vuelve a sembrar."],
        "preconditions": [],
        "example": {"source_commit": "abc1234", "reason": "ancla FW-252", "state_hash": "<sha256 de state_hash_at_commit>"},
    },
    "ciclo_started": {
        "description": "Telemetría de inicio de un ciclo de trabajo para un módulo.",
        "fields": {
            "module": {"type": "string", "description": "ID del módulo cuyo ciclo arranca."},
            "mode": {
                "type": "string",
                "allowed_values": ["modulo", "fase", "paralelo"],
                "description": "Modo del driver /ciclo (opcional).",
            },
        },
        "conditions": ["'mode', si se envía, debe ser uno de: modulo, fase, paralelo."],
        "preconditions": [],
        "example": {"module": "mod-test"},
    },
    "classify_module_spec": {
        "description": "Clasifica las especificaciones de interfaz (data_model, api_contract, integration_real) para un módulo.",
        "fields": {
            "id": {"type": "string", "description": "ID del módulo a clasificar."},
            "data_model": {
                "type": "string",
                "allowed_values": ["required", "n/a", "done"],
                "description": "Estado de la spec de modelo de datos.",
            },
            "api_contract": {
                "type": "string",
                "allowed_values": ["required", "n/a", "done"],
                "description": "Estado de la spec de contrato API.",
            },
            "classified_by": {
                "type": "string",
                "allowed_values": ["scope-siguiente", "enriquecer-ticket", "migrar-proyecto", "manual"],
                "description": "Flujo o agente que clasifica la spec.",
            },
            "justification": {"type": "string", "description": "Justificación obligatoria si alguna spec es 'n/a'."},
            "integration_real": {
                "type": "string",
                "allowed_values": ["required", "n/a", "done"],
                "description": "Estado de la spec de integración real.",
            },
        },
        "conditions": ["Si data_model, api_contract o integration_real es 'n/a', 'justification' es obligatoria."],
        "preconditions": [],
        "example": {
            "id": "mod-test",
            "data_model": "n/a",
            "api_contract": "n/a",
            "classified_by": "manual",
            "justification": "Módulo puramente interno sin persistencia ni API pública.",
        },
    },
    "close_change": {
        "description": "Cierra un cambio ligero en modo mantenimiento tras verificar criterios y tests.",
        "fields": {
            "id": {"type": "string", "description": "Identificador del cambio (CHG-NNN)."},
            "evidence": {"type": "object", "description": "Diccionario mapeando cada criterio a su texto de evidencia."},
            "tests": {"type": "string", "description": "Comando y resultado citando 'rc=0'."},
            "changelog": {"type": "string", "description": "Entrada de changelog describiendo la modificación."},
        },
        "conditions": ["tests debe contener 'rc=0'.", "evidence debe cubrir cada criterio declarado en el cambio."],
        "preconditions": ["El proyecto debe estar en modo mantenimiento.", "El cambio debe estar abierto."],
        "example": {
            "id": "CHG-001",
            "evidence": {
                "Respuestas HTTP incluyen Access-Control-Allow-Origin": "Cabecera verificada en tests/test_change.py rc=0",
            },
            "tests": "pytest tests/test_change.py (rc=0)",
            "changelog": "Ajuste de timeout de conexión a 15s.",
        },
    },
    "complete_module": {
        "description": "Completa y cierra formalmente un módulo tras validar todos los gates de cierre.",
        "fields": {
            "id": {"type": "string", "description": "ID del módulo a completar."},
        },
        "conditions": [
            "El módulo debe estar en estado 'ready_for_validation'.",
            "Todos los ACs de 08_module_acceptance.md deben tener evidencia válida.",
            "0 bugs críticos abiertos asociados al módulo.",
            "Receipts de aprobación, revisión y ejecución válidos y vigentes.",
        ],
        "preconditions": ["Módulo en estado 'ready_for_validation'."],
        "example": {"id": "mod-test"},
    },
    "defer_bug_to_tech_debt": {
        "description": "Traslada un bug abierto a Deuda Técnica, desbloqueando el módulo si no quedan otros bloqueantes.",
        "fields": {
            "id": {"type": "string", "description": "Identificador del bug (ej. 'BUG-001')."},
        },
        "conditions": [],
        "preconditions": ["El bug debe existir en _bugs con estado 'Abierto'."],
        "example": {"id": "BUG-001"},
    },
    "drop_module": {
        "description": "Retira del roadmap un módulo que nunca fue iniciado (estado not_started en pending).",
        "fields": {
            "id": {"type": "string", "description": "ID del módulo a retirar."},
            "reason": {"type": "string", "description": "Justificación sustantiva de la retirada (mínimo 20 caracteres)."},
        },
        "conditions": ["El módulo debe estar en modules.pending y no haber comenzado (estado 'not_started')."],
        "preconditions": ["Módulo no iniciado."],
        "example": {
            "id": "mod-pending",
            "reason": "Módulo absorbido dentro de la arquitectura de mod-core tras refactorización.",
        },
    },
    "abandon_module": {
        "description": (
            "Abandona un módulo ya arrancado y obsoleto (duplicado, absorbido) llevándolo al estado "
            "terminal 'dropped', distinto de 'completed': no cuenta como completado ni como pendiente "
            "(PA-FW-037). Para un módulo que nunca arrancó basta drop_module."
        ),
        "fields": {
            "id": {"type": "string", "description": "ID del módulo a abandonar."},
            "reason": {"type": "string", "description": "Por qué se abandona (mínimo 20 caracteres)."},
            "evidence": {
                "type": "string",
                "description": "Dónde consta la decisión: inventario, aprobación del builder, ADR (mínimo 10 caracteres).",
            },
        },
        "conditions": [
            "El módulo debe estar declarado y en un estado no terminal: not_started, in_progress, blocked o ready_for_validation.",
            "Rechaza 'completed' (si hay que rehacerlo, reopen_module) y 'dropped' (terminal: no se reabre ni se abandona de nuevo).",
            "No puede tener bugs 'Abierto' apuntando a él: muévelos antes con reassign_bug_module o resuélvelos.",
            "No puede tener clarificaciones 'Abierta' con blocks_start, blocks_implementation o blocks_closure sobre él (seguirían bloqueando el proyecto): resuélvelas antes con resolve_clarification.",
            "Deja rastro en modules.dropped y module_abandonments.<id> (from_status, reason, evidence, abandoned_at).",
            "Archiva en closure_rejection_archive todos sus rechazos de cierre (los activos, resueltos por el abandono), así que dejan de contar para el criterio de parada.",
            "Si session.next_module apuntaba al módulo, lo limpia (no elige otro).",
            "Después, register_bug, register_clarification, add_closure_rejection, restore_closure_rejections, update_module_status, set_active_module y escalate_change rechazan el módulo, y reassign_bug_module lo rechaza como destino.",
        ],
        "preconditions": [
            "'evidence' (mínimo 10 caracteres) debe citar dónde consta la aprobación del builder; el motor solo comprueba la longitud.",
        ],
        "example": {
            "id": "mod-pending",
            "reason": "Duplicado de mod-core tras el inventario de módulos; el builder aprobó el cierre.",
            "evidence": "Inventario de higiene §2, aprobación del builder del 2026-10-29.",
        },
    },
    "reassign_bug_module": {
        "description": (
            "Mueve un bug 'Abierto' a otro módulo vivo conservando el module_id previo en amendments "
            "(PA-FW-037). No cambia el estado de ningún módulo."
        ),
        "fields": {
            "id": {"type": "string", "description": "Identificador del bug (ej. 'BUG-001')."},
            "module_id": {"type": "string", "description": "Módulo destino, declarado, abierto (ni 'completed' ni 'dropped')."},
            "reason": {"type": "string", "description": "Por qué el bug pertenece al destino (mínimo 20 caracteres)."},
            "evidence": {
                "type": "string",
                "description": (
                    "Aprobación del builder para mover el bug (mínimo 10 caracteres). Obligatoria si el bug "
                    "es Crítico o Mayor con affects_ac; opcional en otro caso."
                ),
            },
        },
        "conditions": [
            "El bug debe estar 'Abierto' y el destino ser distinto del módulo actual.",
            "Rechaza un destino 'completed': reabre el destino con reopen_module (motivo) o elige un módulo abierto.",
            "Un bug Crítico, o Mayor con affects_ac, bloquea el cierre de su módulo: moverlo exige 'evidence' (mínimo 10 caracteres) con la aprobación del builder.",
            "Si el destino debe quedar bloqueado por el bug, transítalo aparte con update_module_status.",
        ],
        "preconditions": ["El bug debe existir en _bugs."],
        "example": {
            "id": "BUG-001",
            "module_id": "mod-pending",
            "reason": "Residuo real que sigue vivo y pertenece al módulo que absorbe la funcionalidad.",
        },
    },
    "escalate_change": {
        "description": "Escala un cambio ligero de mantenimiento a módulo completo por superar el umbral de complejidad.",
        "fields": {
            "id": {"type": "string", "description": "ID del cambio a escalar (CHG-NNN)."},
            "module_id": {"type": "string", "description": "ID del nuevo módulo completo resultante."},
            "reason": {"type": "string", "description": "Motivo del escalado a módulo formal."},
        },
        "conditions": [],
        "preconditions": ["El cambio debe existir y estar abierto en lifecycle.changes."],
        "example": {
            "id": "CHG-001",
            "module_id": "mod-refactor",
            "reason": "El cambio requiere migración de esquema y toca múltiples componentes acoplados.",
        },
    },
    "invalidate_stale_review": {
        "description": "Invalida un recibo de revisión independiente pendiente cuyo hash de workspace ha quedado stale.",
        "fields": {
            "module": {"type": "string", "description": "ID del módulo cuya revisión pendiente se invalida."},
            "approved_by": {"type": "string", "allowed_values": ["builder"], "description": "Autorizador de la invalidación (exige 'builder')."},
            "reason": {"type": "string", "description": "Motivo de la invalidación (mínimo 20 caracteres)."},
        },
        "conditions": [
            "approved_by debe ser exactamente 'builder'.",
            "reason debe tener al menos 20 caracteres.",
            "Debe existir una revisión previa en estado 'pending' cuyo diff_hash sea stale.",
        ],
        "preconditions": [
            "Debe existir una ronda de revisión previa en estado 'pending' para el módulo.",
        ],
        "example": {
            "module": "mod-test",
            "approved_by": "builder",
            "reason": "La ronda quedó stale tras la reparación canónica autorizada.",
        },
    },
    "open_change": {
        "description": "Abre un cambio ligero dentro de una ventana de mantenimiento (lifecycle: production).",
        "fields": {
            "summary": {"type": "string", "description": "Resumen claro del cambio."},
            "affected_modules": {"type": "array", "description": "Lista de IDs de módulos afectados."},
            "tier": {
                "type": "string",
                "allowed_values": ["tier_0", "tier_1"],
                "description": "Tier del cambio (tier_0 o tier_1; tier_2 exige escalado a módulo).",
            },
            "criteria": {"type": "array", "description": "Lista de 1 a 3 criterios de aceptación no vacíos."},
            "id": {"type": "string", "description": "ID opcional del cambio (autoasignado si se omite)."},
        },
        "conditions": ["tier debe ser 'tier_0' o 'tier_1'.", "criteria debe tener entre 1 y 3 criterios no vacíos."],
        "preconditions": ["El proyecto debe estar en lifecycle stage 'production' o 'maintenance'."],
        "example": {
            "summary": "Ajuste de cabecera CORS en API",
            "affected_modules": ["mod-test"],
            "tier": "tier_0",
            "criteria": ["Respuestas HTTP incluyen Access-Control-Allow-Origin"],
        },
    },
    "record_approval_receipt": {
        "description": "Registra el recibo mecánico de aprobación explícita del builder para un módulo antes de implementar.",
        "fields": {
            "module": {"type": "string", "description": "ID del módulo aprobado."},
            "approved_by": {"type": "string", "allowed_values": ["builder"], "description": "Debe ser 'builder'."},
            "evidence": {"type": "string", "description": "Cita textual de la confirmación del builder."},
            "risk_summary": {"type": "string", "description": "Resumen de riesgos evaluados y mitigaciones."},
            "adr_refs": {"type": "array", "description": "Lista de IDs de ADRs vinculadas (obligatorio si tier es tier_2)."},
            "covers_dependencies": {"type": "array", "description": "Lista opcional de dependencias externas cubiertas por la aprobación."},
            "covers_changes": {"type": "array", "description": "Lista opcional de lo que el plan prevé tocar sin caducar el recibo: 'decisions:aditivas', 'module_spec:clasificar', 'prd|data_model|contracts:## <encabezado>' o 'api_contract:/<ruta>'."},
        },
        "conditions": [
            "approved_by debe ser 'builder'.",
            "Si el módulo es tier_2, adr_refs no puede estar vacío y debe referenciar ADRs válidas.",
            "covers_changes no admite el scope ni los ACs del módulo, y cada encabezado o ruta debe existir una sola vez.",
        ],
        "preconditions": ["El módulo debe tener un work_tier clasificado."],
        "example": {
            "module": "mod-test",
            "approved_by": "builder",
            "evidence": "Builder autorizó en chat: 'Aprobado el plan de mod-test'",
            "risk_summary": "Riesgo bajo acotado a lógica interna.",
            "adr_refs": [],
        },
    },
    "record_checkpoint": {
        "description": "Sella mecánicamente un checkpoint de verificación de etapa (subdominios, experto, preflight).",
        "fields": {
            "kind": {
                "type": "string",
                "allowed_values": ["domain_sweep", "expert_audit"],
                "description": "Tipo de checkpoint.",
            },
            "scope_id": {"type": "string", "description": "Módulo o alcance evaluado."},
            "evidence": {"type": "string", "description": "Evidencia detallada de la revisión (mínimo 20 caracteres)."},
            "module": {"type": "string", "description": "Módulo trazable de la invocación."},
            "reference": {"type": "string", "description": "Referencia o puntero documental."},
            "recorded_by": {"type": "string", "default": "agent", "description": "Agente que registra el checkpoint."},
        },
        "conditions": ["kind debe ser uno de los permitidos.", "evidence debe tener >= 20 caracteres."],
        "preconditions": [],
        "example": {
            "kind": "domain_sweep",
            "scope_id": "mod-test",
            "evidence": "Barrido exhaustivo de dependencias y tipos completado sin advertencias.",
            "reference": "09_domain_profile.md: fila 3",
        },
    },
    "record_data_safety_check": {
        "description": "Sella el control de seguridad de datos y respaldo antes de operaciones destructivas o de persistencia.",
        "fields": {
            "module_id": {"type": "string", "description": "ID del módulo evaluado."},
            "target_env": {
                "type": "string",
                "allowed_values": ["dev", "staging", "prod"],
                "description": "Entorno destino del cambio.",
            },
            "backup_verified": {"type": "boolean", "description": "Indica si el backup fue verificado y restaurable."},
            "evidence": {"type": "string", "description": "Procedimiento de backup y dónde se verificó la restauración (mínimo 20 caracteres)."},
            "recorded_by": {"type": "string", "default": "builder", "description": "Responsable que registra el check."},
        },
        "conditions": ["evidence debe tener >= 20 caracteres."],
        "preconditions": ["El módulo debe tener un work_tier clasificado."],
        "example": {
            "module_id": "mod-test",
            "target_env": "dev",
            "backup_verified": True,
            "evidence": "Snapshot SQLite copiado a .backup/test.db y comprobado con sqlite3 integrity_check.",
        },
    },
    "record_execution_receipt": {
        "description": "Registra el recibo mecánico de verificación ejecutable de tests o comandos antes de cerrar un módulo.",
        "fields": {
            "module": {"type": "string", "description": "ID del módulo verificado."},
            "kind": {
                "type": "string",
                "allowed_values": ["ok_automatizada", "ok_manual", "no_aplica"],
                "description": "Modalidad de verificación ejecutable.",
            },
            "evidence": {"type": "string", "description": "Evidencia de la ejecución y salida de tests (mínimo 20 caracteres)."},
            "command": {"type": "string", "description": "Comando ejecutado."},
            "exit_code": {"type": "integer", "description": "Código de retorno del comando."},
            "tests_run": {"type": "integer", "description": "FW-299: cuántos tests corrieron (opcional; 0 no cierra el módulo)."},
            "test_changes": {"type": "array", "description": "FW-250: [{path, reason, authorized_by}] por cada test existente modificado o borrado cuando el diff contra la base también toca código de producción."},
            "base_ref": {"type": "string", "description": "Base explícita del diff para el gate FW-250. Normalmente NO hace falta: si la aprobación del módulo guardó el mapa de identidades de tests, el mapa manda (solo ve lo cambiado desde la aprobación, commiteado o no); sin mapa, se usa el commit de apertura guardado al aprobar (head_at_approval); solo en aprobaciones anteriores a 5.2.2 se cae al merge-base con origin/HEAD, main o master, que puede incluir trabajo de otros módulos (FW-277)."},
        },
        "conditions": ["kind debe ser uno de: ok_automatizada, ok_manual, no_aplica.", "evidence debe tener >= 20 caracteres.", "Si el diff contra la base modifica o borra tests existentes y toca código de producción, test_changes debe cubrir cada test (FW-250); start_independent_review lo vuelve a exigir."],
        "preconditions": ["El módulo debe tener un work_tier clasificado."],
        "example": {
            "module": "mod-test",
            "kind": "ok_automatizada",
            "command": "pytest tests/test_mod.py",
            "exit_code": 0,
            "evidence": "Ran 12 tests in 0.12s. All passed without warnings.",
        },
    },
    "record_harvest": {
        "description": "Registra la cosecha obligatoria de fricción y aprendizajes del módulo para mejora continua.",
        "fields": {
            "module_id": {"type": "string", "description": "ID del módulo cosechado."},
            "classes": {"type": "string_or_array", "description": "Literal 'none' si no hubo hallazgos, o lista de slugs de fricción."},
        },
        "conditions": ["classes debe ser el literal 'none' o una lista no vacía de strings."],
        "preconditions": ["El módulo debe tener un work_tier clasificado."],
        "example": {"module_id": "mod-test", "classes": "none"},
    },
    "record_review_receipt": {
        "description": "Sella el recibo de revisión independiente de código para un módulo.",
        "fields": {
            "module": {"type": "string", "description": "ID del módulo revisado."},
            "reviewer_id": {"type": "string", "description": "Identificador del revisor (debe coincidir con la ronda iniciada)."},
            "result": {
                "type": "string",
                "allowed_values": ["pass", "fail"],
                "description": "Veredicto de la revisión.",
            },
            "evidence": {"type": "string", "description": "Evidencia y observaciones de la revisión (mínimo 20 caracteres)."},
        },
        "conditions": ["result debe ser 'pass' o 'fail'.", "evidence no puede estar vacía."],
        "preconditions": ["Debe haber una ronda de revisión pendiente iniciada con start_independent_review."],
        "example": {
            "module": "mod-test",
            "reviewer_id": "reviewer-agent",
            "result": "pass",
            "evidence": "Revisión completa de contratos y pruebas: ACs cubiertos sin regresiones detectadas.",
        },
    },
    "refresh_approval_receipt": {
        "description": "Actualiza el APPROVE_RECEIPT ante cambios puramente aditivos en superficies derivables sin cambiar decisiones.",
        "fields": {
            "module": {"type": "string", "description": "ID del módulo."},
            "parent_fingerprint": {"type": "string", "description": "Fingerprint del APPROVE_RECEIPT anterior a refrescar."},
            "evidence": {"type": "string", "description": "Evidencia de la modificación puramente aditiva."},
            "additive_rationale": {"type": "string", "description": "Justificación de que la modificación es puramente aditiva o narrativa."},
            "derived_from": {"type": "array", "description": "Obligatoria, no vacía: superficies o fuentes de la derivación (p. ej. [\"data_model\", \"ADR-007\"])."},
        },
        "conditions": ["Si hay cambios no aditivos en superficies derivables, additive_rationale es obligatoria."],
        "preconditions": ["El módulo debe tener un APPROVE_RECEIPT previo aprobado."],
        "example": {
            "module": "mod-test",
            "parent_fingerprint": "0000000000000000000000000000000000000000000000000000000000000000",
            "evidence": "Ampliación de especificación técnica con detalles narrativos no disruptivos.",
            "derived_from": ["data_model"],
            "additive_rationale": "Ampliación de especificación técnica con detalles narrativos no disruptivos.",
        },
    },
    "register_bug": {
        "description": "Registra un nuevo bug en _bugs. Si es Crítico o Mayor con affects_ac, bloquea el módulo correspondiente; un módulo completed sigue completed (reabrirlo es reopen_module con motivo, FW-300).",
        "fields": {
            "module_id": {"type": "string", "description": "ID del módulo afectado por el bug."},
            "severity": {
                "type": "string",
                "allowed_values": ["Crítico", "Mayor", "Menor"],
                "description": "Severidad del error.",
            },
            "description": {"type": "string", "description": "Descripción clara del comportamiento anómalo y reproducción."},
            "affects_ac": {"type": "boolean", "default": False, "description": "Indica si el bug incumple un criterio de aceptación del módulo."},
            "id": {"type": "string", "description": "ID opcional del bug (autoasignado como BUG-NNN si se omite)."},
        },
        "conditions": ["severity debe ser 'Crítico', 'Mayor' o 'Menor'."],
        "preconditions": [],
        "example": {
            "module_id": "mod-test",
            "severity": "Menor",
            "description": "El banner de ayuda muestra un espacio tipográfico sobrante.",
            "affects_ac": False,
        },
    },
    "register_clarification": {
        "description": "Registra una duda o clarificación técnica o de producto que puede bloquear etapas del módulo.",
        "fields": {
            "blocked_scope": {"type": "string", "description": "ID del módulo o alcance bloqueado por la duda."},
            "question": {"type": "string", "description": "Pregunta detallada dirigida al builder."},
            "title": {"type": "string", "default": "Pregunta", "description": "Título descriptivo de la clarificación."},
            "blocks_start": {"type": "boolean", "default": False, "description": "Bloquea el inicio del módulo."},
            "blocks_implementation": {"type": "boolean", "default": False, "description": "Bloquea la implementación del módulo."},
            "blocks_closure": {"type": "boolean", "default": False, "description": "Bloquea el cierre del módulo."},
            "blocking_condition": {"type": "string", "description": "Condición bajo la cual bloquea."},
            "options": {"type": "string", "description": "Opciones o alternativas propuestas."},
            "impact": {"type": "string", "description": "Impacto si no se clarifica."},
            "id": {"type": "string", "description": "ID opcional (autoasignado como CLAR-NNN)."},
        },
        "conditions": [],
        "preconditions": [],
        "example": {
            "blocked_scope": "mod-test",
            "question": "¿Debemos permitir reintentos automáticos en caso de fallo de red?",
            "title": "Política de reintentos",
        },
    },
    "register_decision": {
        "description": "Registra una Decisión Arquitectural (ADR) formal en _decisions y regenera 03_arch_decisions.md.",
        "fields": {
            "title": {"type": "string", "description": "Título claro de la decisión arquitectónica."},
            "context": {"type": "string", "description": "Contexto técnico y fuerzas en tensión que motivan la decisión."},
            "decision": {"type": "string", "description": "Decisión concreta adoptada y alcance."},
            "module_id": {"type": "string_or_array", "default": "framework", "description": "ID del módulo al que aplica, lista de módulos o 'framework'."},
            "alternatives": {"type": "string", "description": "Alternativas descartadas y razón del descarte."},
            "consequences": {"type": "string", "description": "Consecuencias positivas y negativas de la decisión."},
            "expertise": {"type": "string_or_array", "description": "Referencia a la skill core o experto de dominio que fundó la decisión."},
            "id": {"type": "string", "description": "ID opcional de la ADR (autoasignado como ADR-NNN)."},
            "documents_decision": {"type": "string", "description": "ID opcional de ADR previa que esta decisión documenta o detalla aditivamente."},
        },
        "conditions": [],
        "preconditions": [],
        "example": {
            "title": "Uso de SQLite para almacenamiento local",
            "context": "Se requiere persistencia relacional embebida sin servidor externo.",
            "decision": "Adoptar SQLite con WAL mode y transacciones estrictas.",
            "module_id": "mod-test",
        },
    },
    "reopen_module": {
        "description": "Reabre formalmente un módulo que estaba 'completed', invalidando evidencia de cierre previa para corrección.",
        "fields": {
            "id": {"type": "string", "description": "ID del módulo a reabrir."},
            "reason": {"type": "string", "description": "Motivo sustantivo de la reapertura (mínimo 20 caracteres)."},
        },
        "conditions": ["reason debe tener al menos 20 caracteres."],
        "preconditions": ["El módulo debe estar en estado 'completed'."],
        "example": {
            "id": "mod-done",
            "reason": "Reapertura para adaptar interfaz a cambio de contrato en servicio dependiente.",
        },
    },
    "resolve_bug": {
        "description": "Marca un bug como 'Resuelto' y registra la solución implementada. Desbloquea el módulo si no hay otros bloqueantes.",
        "fields": {
            "id": {"type": "string", "description": "ID del bug resuelto (ej. 'BUG-001')."},
            "fix": {"type": "string", "description": "Explicación de la solución aplicada."},
            "affected_files": {"type": "array", "description": "Lista de rutas de archivos modificados para corregir el bug."},
        },
        "conditions": [],
        "preconditions": ["El bug debe existir en _bugs con estado 'Abierto'."],
        "example": {
            "id": "BUG-001",
            "fix": "Ajuste de expresión regular en validador de entradas.",
            "affected_files": ["src/validator.py"],
        },
    },
    "resolve_clarification": {
        "description": "Resuelve una clarificación abierta registrando la respuesta del builder y desbloquea el módulo si procede.",
        "fields": {
            "id": {"type": "string", "description": "ID de la clarificación (ej. 'CLAR-001')."},
            "answer": {"type": "string", "description": "Respuesta o decisión del builder."},
        },
        "conditions": [],
        "preconditions": ["La clarificación debe existir en _clarifications con estado 'Abierta'."],
        "example": {
            "id": "CLAR-001",
            "answer": "Se permiten 3 reintentos con retroceso exponencial.",
        },
    },
    "resolve_closure_rejections": {
        "description": "Registra la resolución formal de los rechazos de cierre activos para un módulo antes de reintentar complete_module.",
        "fields": {
            "module_id": {"type": "string", "description": "ID del módulo con rechazos de cierre activos."},
            "resolution": {"type": "string", "description": "Explicación de las acciones correctivas aplicadas."},
            "evidence": {"type": "string", "description": "Evidencia verificable de la subsanación (tests, diff, logs)."},
            "resolved_ids": {"type": "array", "description": "Lista opcional de IDs de rechazos específicos a resolver."},
        },
        "conditions": [],
        "preconditions": [
            "El módulo debe estar en estado 'ready_for_validation'.",
            "Deben existir rechazos de cierre activos para el módulo.",
        ],
        "example": {
            "module_id": "mod-test",
            "resolution": "Se añadieron tests de integración para cubrir los ACs faltantes.",
            "evidence": "pytest tests/test_integration.py pasando al 100%.",
        },
    },
    "restore_closure_rejections": {
        "description": "Restaura rechazos de cierre previamente archivados si la resolución resultó insuficiente o falló.",
        "fields": {
            "module_id": {"type": "string", "description": "ID del módulo."},
            "archive_ids": {"type": "array", "description": "Lista de IDs de rechazos en archivo a restaurar."},
            "reason": {"type": "string", "description": "Motivo de la restauración."},
            "restored_ids": {"type": "array", "description": "IDs restaurados durante replay."},
        },
        "conditions": [],
        "preconditions": ["Los rechazos indicados deben existir en el archivo de rechazos."],
        "example": {
            "module_id": "mod-test",
            "archive_ids": ["CRR-0001"],
            "reason": "La corrección previa reintrodujo la regresión en tests concurrentes.",
        },
    },
    "set_active_module": {
        "description": "Mueve un módulo al principio de modules.in_progress para establecerlo como foco activo de la sesión.",
        "fields": {
            "id": {"type": "string", "description": "ID del módulo que pasa a ser activo."},
        },
        "conditions": ["El módulo debe estar en modules.in_progress."],
        "preconditions": ["Módulo en estado in_progress."],
        "example": {"id": "mod-test"},
    },
    "set_execution_profile": {
        "description": "Declara el modelo y nivel de capacidad del ejecutor (strong, standard, limited) para calibrar el rigor requerido.",
        "fields": {
            "capability_tier": {
                "type": "string",
                "allowed_values": ["strong", "standard", "limited"],
                "description": "Nivel de capacidad del modelo ejecutor.",
            },
            "model": {"type": "string", "description": "Nombre o identificador del modelo LLM utilizado."},
            "rationale": {"type": "string", "description": "Justificación de la clasificación de capacidad."},
        },
        "conditions": ["capability_tier debe ser 'strong', 'standard' o 'limited'."],
        "preconditions": [],
        "example": {
            "capability_tier": "strong",
            "model": "gemini-3.8-flash",
            "rationale": "Modelo avanzado con soporte nativo de herramientas y verificación.",
        },
    },
    "set_focus": {
        "description": "Fija el área de foco activa (frontend, backend, data, devops, etc.) o la limpia (null).",
        "fields": {
            "area": {"type": "string_or_null", "description": "Nombre del área de foco, o null para limpiar el foco."},
        },
        "conditions": [],
        "preconditions": [],
        "example": {"area": "backend"},
    },
    "set_framework_version": {
        "description": "Actualiza la versión declarada del framework tras una migración o upgrade de plantilla.",
        "fields": {
            "version": {"type": "string", "description": "Versión en formato semver (X.Y o X.Y.Z)."},
        },
        "conditions": ["'version' debe tener formato numérico X.Y o X.Y.Z."],
        "preconditions": [],
        "example": {"version": "4.5.0"},
    },
    "confirm_builder_use": {
        "description": (
            "Registra que el builder usó de verdad un módulo cerrado (FW-275). Solo tras una "
            "afirmación explícita suya: el agente no la infiere ni la propone. Admite registrarse "
            "después del cierre; una segunda confirmación añade evidencia, no sobrescribe."
        ),
        "fields": {
            "module": {"type": "string", "description": "Módulo completed o ready_for_validation."},
            "evidence": {"type": "string", "description": "Cita de lo que dijo el builder (mínimo 20 caracteres)."},
            "builder_confirmed": {"type": "boolean", "description": "Confirmación explícita del builder."},
        },
        "conditions": [
            "builder_confirmed debe ser True.",
            "evidence debe tener al menos 20 caracteres.",
            "El módulo debe estar completed o ready_for_validation y tener entrada en work_tiers.",
        ],
        "preconditions": [],
        "example": {
            "module": "C1",
            "evidence": "Builder: «usé el comparador toda la semana con mi cartera real y funciona»",
            "builder_confirmed": True,
        },
    },
    "set_lifecycle": {
        "description": "Fija la etapa del ciclo de vida del proyecto (development, active, maintenance, production).",
        "fields": {
            "stage": {
                "type": "string",
                "allowed_values": ["development", "production"],
                "description": "Etapa del ciclo de vida.",
            },
            "reason": {"type": "string", "description": "Motivo del cambio de ciclo de vida (mínimo 20 caracteres)."},
            "builder_confirmed": {"type": "boolean", "description": "Confirmación explícita del builder."},
            "escalation_patterns": {"type": "array", "description": "Patrones de escalado en mantenimiento."},
        },
        "conditions": ["builder_confirmed debe ser True.", "reason debe tener al menos 20 caracteres."],
        "preconditions": [],
        "example": {
            "stage": "development",
            "reason": "Reanudación del desarrollo activo tras cierre de ventana.",
            "builder_confirmed": True,
        },
    },
    "set_phase": {
        "description": "Actualiza la fase actual del proyecto (ej. 'phase_01', 'phase_02') y opcionalmente el paso.",
        "fields": {
            "current_phase": {"type": "string", "description": "Identificador de la fase en formato 'phase_XX'."},
            "current_step": {"type": "string", "description": "Paso actual dentro de la fase."},
        },
        "conditions": ["current_phase debe seguir el formato 'phase_[0-9]{2}'."],
        "preconditions": [],
        "example": {"current_phase": "phase_01", "current_step": "implementacion"},
    },
    "set_session_pointer": {
        "description": "Actualiza el puntero de continuación de sesión (próxima acción y comando recomendado para el builder).",
        "fields": {
            "next_action": {"type": "string", "description": "Instrucción autosuficiente de la siguiente acción a realizar."},
            "next_command": {"type": "string", "description": "Comando slash del builder correspondiente (ej. '/ciclo', '/nuevo-bug')."},
            "active_module_description": {"type": "string", "description": "Descripción del módulo activo en progreso."},
            "next_module": {"type": "string", "description": "ID del próximo módulo planificado."},
            "next_module_description": {"type": "string", "description": "Descripción del próximo módulo."},
        },
        "conditions": ["next_command debe ser un comando público del builder declarado en manifest.json (o sentinel de bloqueo)."],
        "preconditions": [],
        "example": {
            "next_action": "Ejecutar las pruebas unitarias de mod-test en tests/test_mod_test.py y, si pasan, cerrar el módulo con todos sus gates.",
            "next_command": "/ciclo",
        },
    },
    "start_independent_review": {
        "description": "Inicia una ronda formal de revisión independiente de código para un módulo antes de sellar su cierre.",
        "fields": {
            "module": {"type": "string", "description": "ID del módulo a revisar."},
            "reviewer_id": {"type": "string", "description": "Identificador del revisor (subagente o persona diferente del implementador)."},
            "implementer_id": {"type": "string", "description": "Identificador del implementador del código."},
            "mode": {
                "type": "string",
                "allowed_values": ["READ_ONLY_SUBAGENTS", "INDEPENDENT_SUBAGENT_NO_ISOLATION", "HUMAN_INDEPENDENT_REVIEW"],
                "description": "Modalidad de revisión independiente.",
            },
        },
        "conditions": [
            "reviewer_id e implementer_id deben ser distintos.",
            "mode debe ser uno de los modos de revisión permitidos.",
        ],
        "preconditions": [
            "El módulo debe tener un work_tier clasificado.",
            "No debe haber otra ronda de review pendiente sin finalizar.",
        ],
        "example": {
            "module": "mod-test",
            "reviewer_id": "reviewer-agent",
            "implementer_id": "coder-agent",
            "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
        },
    },
    "supersede_decision": {
        "description": "Marca una ADR existente como 'Superseded' por una nueva decisión, preservando la trazabilidad histórica.",
        "fields": {
            "id": {"type": "string", "description": "ID de la ADR anterior que queda obsoleta (ej. 'ADR-001')."},
            "superseded_by": {"type": "string", "description": "ID de la nueva ADR que la sustituye (ej. 'ADR-002')."},
            "reason": {"type": "string", "description": "Motivo sustantivo del reemplazo (mínimo 20 caracteres)."},
        },
        "conditions": [
            "'id' y 'superseded_by' deben ser diferentes.",
            "'reason' debe tener al menos 20 caracteres.",
        ],
        "preconditions": ["Ambas ADRs deben existir en _decisions."],
        "example": {
            "id": "ADR-001",
            "superseded_by": "ADR-002",
            "reason": "Sustitución de arquitectura monolith por microservicios desacoplados.",
        },
    },
    "update_last_step": {
        "description": "Actualiza el resumen del último paso completado en el estado del proyecto.",
        "fields": {
            "last_completed_step": {"type": "string", "description": "Descripción breve del último paso completado con éxito."},
        },
        "conditions": [],
        "preconditions": [],
        "example": {"last_completed_step": "Completada implementación de contratos y pruebas unitarias."},
    },
    "update_module_status": {
        "description": "Actualiza el estado de un módulo en la máquina de estados según transiciones legales permitidas.",
        "fields": {
            "id": {"type": "string", "description": "ID del módulo a transicionar."},
            "status": {
                "type": "string",
                "allowed_values": ["not_started", "in_progress", "ready_for_validation", "blocked"],
                "description": "Nuevo estado del módulo (para 'completed' usar complete_module).",
            },
        },
        "conditions": [
            "La transición debe ser legal según _transitions.is_legal.",
            "No puede transicionar directamente a 'completed' (usar complete_module).",
            "No puede salir de 'blocked' a 'in_progress' o 'not_started' si tiene bugs críticos/mayores o clarificaciones bloqueantes abiertas (N3).",
            "No puede transicionar de 'not_started' a 'in_progress' si el criterio de parada está activo (bugs críticos abiertos o >= 2 closure_rejections), salvo si el módulo tiene stabilization: true (N4).",
        ],
        "preconditions": [
            "El módulo debe existir en el estado.",
            "Para salir de blocked, resolver antes sus bloqueantes (resolve_bug, resolve_clarification o defer_bug_to_tech_debt).",
        ],
        "example": {"id": "mod-test", "status": "in_progress"},
    },
}


def describe_action(action: str) -> dict[str, Any] | None:
    """Devuelve el contrato descriptivo de una acción built-in.

    required_keys y optional_keys se derivan en tiempo de ejecución de las
    constantes de state_core, garantizando cero divergencia entre el validador
    del motor y la descripción pública.
    """
    if action not in state_core.BUILTIN_ACTIONS:
        return None
    meta = ACTION_DESCRIPTIONS.get(action, {})
    required = sorted(state_core._REQUIRED_PAYLOAD_KEYS.get(action, set()))
    optional = sorted(state_core._OPTIONAL_PAYLOAD_KEYS.get(action, set()))
    return {
        "action": action,
        "description": meta.get("description", ""),
        "required_keys": required,
        "optional_keys": optional,
        "fields": meta.get("fields", {}),
        "conditions": meta.get("conditions", []),
        "preconditions": meta.get("preconditions", []),
        "example": meta.get("example", {}),
    }


def list_actions() -> list[str]:
    """Lista todas las acciones públicas del motor que disponen de contrato."""
    return sorted(state_core.BUILTIN_ACTIONS)
