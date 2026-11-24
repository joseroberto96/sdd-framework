---
name: "security-core"
description: "Skill core. Se activa al tocar auth, permisos, inputs externos, datos sensibles, archivos, red, comandos, DB o publicación."
---

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# Seguridad Core — Skill Core

**Activación:** auth, sesiones, roles, permisos, endpoints, formularios, inputs externos, DB, filesystem, logs, secretos, GitHub/publicación o dependencias.

## Principios

- Validar input antes de usarlo en DB, filesystem, red, templates o comandos.
- Verificar autenticación y autorización antes de cualquier acción de escritura.
- No confiar en IDs enviados por el cliente; validar pertenencia/rol.
- No exponer secretos, PII, tokens, rutas internas ni stack traces.
- No instalar dependencias ni skills externas sin procedencia y aprobación.
- Seguridad por defecto: si hay duda, bloquear y preguntar.

## Checklist

- [ ] ¿Hay input externo? ¿Tiene schema/tipos explícitos?
- [ ] ¿Hay escritura? ¿Verifica auth y autorización?
- [ ] ¿Hay acceso por ID? ¿Valida pertenencia o rol?
- [ ] ¿Hay logs? ¿Excluyen tokens, passwords y PII?
- [ ] ¿Hay errores visibles? ¿Evitan detalles internos?
- [ ] ¿Hay nuevos secrets? ¿Están fuera de Git y documentados en `.env.example`?
- [ ] ¿Hay dependencia nueva? ¿Está aprobada y registrada?
- [ ] ¿Hay publicación GitHub? ¿Pasó gate de secretos?

## Alto Riesgo

Bloquear y pedir decisión si aparece:
- Comandos shell con input del usuario.
- SQL o queries construidos por concatenación.
- `innerHTML` con contenido no confiable.
- Tokens en URL, logs o localStorage sin justificación.
- Cambios de permisos, roles o ownership sin AC explícito.
- Repo público con datos reales o secretos.

## Evidencia Esperada

Antes de cerrar:
- Test o check manual del flujo seguro.
- Nota en `06_bug_fixes.md` si se corrigió una vulnerabilidad.
- Gate de seguridad en `/cierre-modulo`.
