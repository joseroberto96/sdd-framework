---
description: Inicializa o actualiza publicación del proyecto en GitHub desde fase 0, con checks de seguridad y distribución
---

# Publicar en GitHub

Usar cuando el usuario quiera subir el proyecto a GitHub, crear el repositorio, publicar la fase 0 o compartir actualizaciones.

## Objetivo

<!-- CONTEXT_SOURCE: publish_gate -->
Publicar solo después de doctor, tests, gate de secretos, identidad de repo,
rollback y aprobación explícita. `unavailable`, timeout o evidencia no
ejecutada se conservan como pendientes y no se convierten en PASS.
<!-- END_CONTEXT_SOURCE: publish_gate -->

Tener un repositorio como fuente distribuible del proyecto desde temprano:
- Fase 0: esqueleto mínimo publicable.
- MVP: versiones compartibles para usuarios.
- Actualizaciones: `git pull` desde otros equipos o ubicaciones.

## Pasos

0. CONTEXTO Y ESTADO
   Leer:
   - `_project/03_project_state.json`
   - `_project/04_active_scope.md`
   - `_project/13_session_journal.md`
   - `_project/16_release_profile.md` si existe
   - `.gitignore`, `.gitattributes`, `README.md` si existen

1. DETECTAR ESTADO GIT
   Ejecutar checks locales:
   - `git rev-parse --is-inside-work-tree`
   - `git status --short`
   - `git remote -v`
   - `git branch --show-current`

   Si no hay repo Git:
   - Activar decisión explícita de `git init`:
     1. Inicializar Git local ahora.
     2. Diferir Git y trabajar sin publicación.
     3. Trabajar sin Git con checks manuales reducidos.
   - Crear o revisar `.gitignore` y `.gitattributes` antes del primer commit.
   - Usar `.agent/templates/gitignore.base` y `.agent/templates/gitattributes.base` como base si faltan.
   - No ejecutar comandos destructivos.

1.5. GATE VERDE LOCAL ANTES DE RED
   Antes de pedir permisos de red, `gh auth`, crear `.mcp.json`, clonar skills o tocar remotos:
   - Ejecutar `python3 .agent/scripts/framework_doctor.py`.
   - Ejecutar tests relevantes disponibles (`python3 -m unittest discover -s tests` si existe `tests/`).
   - Si hay módulo activo, ejecutar `python3 .agent/scripts/validate_acceptance.py "[module]"` y `python3 .agent/scripts/validate_closure.py "[module]"` solo cuando el módulo esté en cierre.
   - Si doctor/tests fallan, NO pedir acciones de red hasta resolver o registrar una excepción explícita.
   - Resultado requerido: **verde local** o excepción documentada en `_project/16_release_profile.md`.

2. DEFINIR PUBLICACIÓN Y REPO IDENTITY
   Si faltan datos, preguntar en un bloque corto:
   ```markdown
   Para publicar en GitHub necesito confirmar:
   1. Nombre del repo:
   2. Visibilidad: privado o público:
   3. Rama principal: main u otra:
   4. ¿Publicamos fase 0 ahora? [sí/no]
   5. ¿Usaremos GitHub CLI (`gh`) o crearás el repo manualmente y me pasas la URL?
   ```

   Gate **repo identity** antes de `git remote add origin` o `gh repo create`:
   - Owner/cuenta GitHub.
   - Repo name exacto (capitalización, guiones/underscores).
   - Visibilidad.
   - URL canónica esperada.
   - Decisión si el usuario ya tiene un repo con otro nombre: renombrar repo externo o adaptar artefactos (`README.md`, `_project/16_release_profile.md`, `core.md`, `04_active_scope.md`) antes de publicar.

3. GATE DE SEGURIDAD ANTES DE COMMIT/PUSH
   Bloquear publicación si aparece cualquiera de estos riesgos:
   - `.env` real, tokens, API keys, passwords, certificados, dumps o datos privados.
   - Archivos grandes generados, caches, builds o dependencias vendorizadas sin razón.
   - `13_session_journal.md` con pendientes que deberían persistirse primero.
   - Cambios destructivos no aprobados.

   Checks mínimos:
   - Revisar `.gitignore`.
   - Buscar patrones obvios de secretos (`API_KEY`, `SECRET`, `TOKEN`, `PASSWORD`, `PRIVATE KEY`).
   - Confirmar que `_project/03_project_state.json` representa el estado actual.

   **PUNTERO — cierre de L5 (3.17.0):** si el release toca
   deploy/operación (Dockerfile nuevo, `fly.toml`,
   `railway.json`, systemd unit, k8s manifests, etc.),
   aplicar también `.agent/skills/deployment_core.md`
   antes de aceptar el release. La skill cubre: dev/prod,
   secretos por entorno, orden de raíz en app desplegada,
   observabilidad mínima, recovery ante crash, sync
   dev↔producción, y el contrato del PaaS (puerto leído
   del entorno, migraciones como `release_command`).
   El gate de secretos + `.gitignore` + `_project/16_release_profile.md`
   de este workflow **NO** se tocan — la skill los
   engarza, no los sustituye. La skill **NO** ejecuta
   deploys; el operador decide fuera del loop.

4. FASE 0 — ESQUELETO PUBLICABLE
   Si el usuario aprueba publicar fase 0:
   - Asegurar que `_project/16_release_profile.md` existe.
   - Completar checklist de fase 0.
   - Asegurar `.gitignore` y `.gitattributes` desde las plantillas base si faltan.
   - Crear o actualizar README con:
     - Qué es el proyecto.
     - Requisitos.
     - Instalación.
     - Cómo actualizar: `git pull --ff-only`.
     - Estado actual: fase 0 / MVP / en desarrollo.
   - Crear tag opcional `phase-0` solo si el usuario lo aprueba.

5. CREAR O CONECTAR REMOTO
   Requisito previo: gate **verde local** aprobado en paso 1.5.

   Si el usuario quiere usar `gh`:
   - Verificar `gh auth status`.
   - Pedir aprobación antes de cualquier comando de red.
   - Crear repo con visibilidad confirmada.

   Si el repo ya existe:
   - Añadir remote `origin` con la URL aprobada.
   - Verificar que no sobreescribe un remote existente distinto sin aprobación.

5.5. GATE DE ROLLBACK
   Antes del commit, verificar en `_project/16_release_profile.md`:
   - ¿Existe un procedimiento de rollback documentado para esta versión?
   - Si hubo cambio de esquema de BD: ¿el rollback incluye cómo revertir el esquema?
   Si el rollback no está documentado:
   > ⚠️ Sin procedimiento de rollback. Documenta en 16_release_profile.md (1-2 pasos) antes de hacer push.
   > Si el rollback es genuinamente no reversible (e.g. migración destructiva), declararlo explícitamente con aprobación del usuario.
   Bloquear push hasta que el rollback esté documentado o el usuario apruebe la excepción.

6. COMMIT Y PUSH
   Antes de commitear:
   - Gate **Git identity**: mostrar `git config user.name` y `git config user.email`.
   - Preguntar si usar identidad global, configurar identidad repo-local o usar email GitHub no-reply antes del primer commit.
   - Mostrar resumen de archivos a incluir.
   - Separar commits si hay cambios no relacionados.
   - Pedir confirmación si hay archivos dudosos.

   Luego:
   - `git add` solo de archivos aprobados.
   - `git commit -m "[mensaje]"`
   - `git push -u origin [rama]`

7. REGISTRAR PUBLICACIÓN
   Actualizar `_project/16_release_profile.md`:
   - URL remota.
   - Rama principal.
   - Modo de distribución.
   - Entrada en historial con fecha, rama, commit/tag y verificación.

   Actualizar `_project/03_project_state.json`:
   - `last_completed_step`: publicación realizada y próximo paso.
   - Reconciliar `current_phase` con el scope activo. Publicar o pushear no cambia de fase por sí solo.
   - No marcar módulos como completed por publicar; eso sigue perteneciendo a `/cierre-modulo`.

7.5. TAGS DE FASE
   Crear tags de fase (por ejemplo `phase-0`) solo si:
   - Los ACs/documentos de la fase ya tienen evidencia actualizada.
   - `module_statuses[module]` no contradice el estado narrativo.
   - El commit que recibirá el tag representa el hito documentalmente cerrado.
   - El usuario aprueba el tag.

   Si la evidencia del tag debe registrarse después de crear el tag, hacer dos pasos:
   1. Tag sobre el commit de hito correcto.
   2. Commit posterior que documenta el tag y mantiene `current_phase` / scope reconciliados.

8. INSTRUCCIONES PARA EL EQUIPO
   Entregar instrucciones breves:
   ```markdown
   Primera vez:
   git clone [URL]

   Actualizar:
   git pull --ff-only
   ```

## Reglas

- No hacer push sin aprobación explícita.
- No publicar secretos ni datos privados.
- No usar `git push --force` salvo petición explícita y justificación.
- No crear repos públicos por defecto; si falta visibilidad, usar privado.
- Si el entorno no tiene red o GitHub CLI, preparar los comandos y pedir al usuario crear el repo manualmente.
- Cerrar con:

  Siguiente: /gycd Guardar el estado publicado.
  Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

La interfaz, los logs y los textos para el usuario van en español.
