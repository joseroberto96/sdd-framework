---
description: Skill core. Se activa ante señales de deploy, CI/CD, secretos por entorno, "subir a producción" o al diseñar la operación de un sistema. Heurística, no automatización; nunca ejecuta deploys ni scripts de SO.
core: true
---

# Deployment / Operación (cierre L5)

**Activación:** el builder menciona deploy, CI/CD, pipeline,
"subir a producción", "entorno", "infra", "operación",
"release", "rollback", "secretos por entorno", "Docker",
"systemd", "PaaS", "crash recovery", o el proyecto entra en
fase de release (cierre del MVP). También se activa si el
módulo toca orden de raíz, web server, procesos, señales,
o interacción con un sistema externo persistente.

## Precedencia

Esta skill es una **heurística de operación**. Nunca supera:

1. Correctitud verificable.
2. Preservación de contratos y anti-regresión.
3. Seguridad y datos (secretos, PII, permisos).
4. Confiabilidad y observabilidad.
5. Peticiones explícitas del usuario.

Si choca con `GUARDRAILS_MASTER.md`,
`.agent/rules/architecture_constraints.md`, `CLAUDE.md`,
`AGENTS.md`, contratos o ACs, pierde esta skill.

## Lo que esta skill **NO** es

- **NO** ejecuta deploys. No corre `docker push`, `fly deploy`,
  `railway up`, `systemctl restart`, ni ningún comando que
  toque infraestructura productiva. El framework no
  automatiza operaciones destructivas; el operador decide
  fuera del loop del agente.
- **NO** instala dependencias de deploy (CLIs de PaaS,
  plugins de CI, helm charts externos) sin aprobación
  explícita. La skill razona; el operador ejecuta.
- **NO** es un subagente, no es un perfil de ejecutor, no
  es un área del modo foco. La ruta `devops` en `/foco`
  (3.12.0) **rutea a esta skill**, no crea un árbol de
  profiles ni un subagente dedicado. La paralelización con
  escritura está prohibida (regla `AUTO_SUGGEST`, no
  `AUTO_MUTATE`, de preflight-reviewer 3.9.0) y se difiere
  (Fase 4b vetó la ejecución paralela con escritura; la
  paralelización operativa queda para Fase 4c+ con
  aislamiento verificado).
- **NO** sustituye a `/publicar-github` ni a las políticas
  de release del proyecto. El gate de secretos,
  `.gitignore`, `.gitattributes` y `_project/16_release_profile.md`
  viven en `/publicar-github`; esta skill se engarza
  cuando el release **toca** deploy/operación.

## Huecos L5 que cubre

L5 = "operación/despliegue". Los huecos reales documentados
(example deployment, audit-residual-debt) son:

1. **Separación dev / prod.** Las credenciales, URLs,
   secrets y datos de prueba son distintos por entorno.
   Un `.env.development` y un `.env.production` (o el
   equivalente del stack: `staging.yaml`/`production.yaml`,
   `appsettings.Development.json`/`appsettings.Production.json`,
   perfiles de Spring, etc.) **nunca** se commitean poblados.
   `.env.example` documenta las claves sin valores.

2. **Secretos por entorno.** Los secretos se cargan de
   un mecanismo externo al repo: vault, secret manager,
   variables de entorno del SO, o el provisto por el PaaS
   (Fly secrets, Railway variables, GitHub Actions secrets).
   El código lee vía el loader estándar del stack; nunca
   hardcodea.

3. **Escenarios de deploy — plantillas, no prescripciones.**
   El builder puede ignorar las plantillas si su stack
   es distinto. La skill ofrece:

   - **Contenedor (Docker + env vars):** el `Dockerfile`
     respeta el orden de raíz del app (entrypoint,
     WORKDIR, COPY estratificado para caché), las env
     vars vienen del entorno, el proceso principal
     corre como usuario no-root cuando sea posible,
     y el `HEALTHCHECK` declara el endpoint de liveness
     cuando exista. Multi-stage build para no llevar
     toolchain al runtime.
   - **systemd unit (Linux/VM/bare-metal):** unit file
     mínimo con `Restart=always`, `EnvironmentFile=`
     apuntando a un path fuera del repo, `User=` no-root
     cuando aplique, `LimitNOFILE` razonable, y `ExecStart`
     que logue a journald. `WantedBy=multi-user.target`.
   - **PaaS tipo Fly/Railway/Render:** la app respeta
     el contrato del PaaS (puerto leído de `PORT`,
     `fly.toml`/`railway.json`/`render.yaml` versionado
     con valores no-secretos, el `release_command` o
     equivalente declarado en el manifiesto), y la
     migración de BD corre antes de servir tráfico (a
     través del release command, no del start command).

4. **Orden de raíz en app desplegada.** El path raíz
   dentro del contenedor o del chroot del proceso
   coincide con el declarado en el manifest (Dockerfile
   `WORKDIR`, systemd `WorkingDirectory=` o
   `RootDirectory=`). Las rutas en config son relativas
   a esa raíz; no se asumen absolutas ni dependencias de
   `cwd` del proceso.

5. **Observabilidad mínima.** Liveness y readiness
   probes distintas cuando el stack las distinga (k8s,
   Fly, Render); logs a stdout/stderr en formato
   estructurado (JSON cuando el agregador lo pida);
   métricas de error rate y latencia p95/p99 cuando
   haya agregador. Errores no se loguean con `print` ni
   se tragan; van al mecanismo de logging estándar del
   stack.

6. **Recuperación ante crash.** Política explícita de
   restart (systemd `Restart=always`, k8s `restartPolicy:
   Always`, PaaS equivalente). Si el estado es
   persistente, el último estado conocido se recupera
   del storage externo (DB, volumen) en el siguiente
   arranque, **no** del proceso. La app no asume que
   sus datos en memoria sobreviven al restart.

7. **Sync dev↔producción.** Las migraciones de esquema
   y los seeds idempotentes se aplican antes de servir
   tráfico. La promoción dev→staging→prod es un
   pipeline explícito (no un "deploy a mano" del
   operador). El log de qué versión está en cada
   entorno vive en `_project/16_release_profile.md` o
   el equivalente del stack.

## Activación por señal

La skill se activa cuando:

- El builder dice "deploy", "subir a producción", "release",
  "operación", "infraestructura", "PaaS", "Docker",
  "systemd", "rollback", o equivalente.
- El módulo toca un endpoint público, un worker, un
  proceso de larga vida, o interacción con storage
  persistente.
- El cierre toca release y el módulo tiene un
  `_project/16_release_profile.md` activo.
- El foco activo es `devops` / `deploy` / `infra` (ruta
  `/foco` 3.17.0).

## Reglas

- Variables de entorno **nunca** se hardcodean, **nunca**
  se commitean pobladas. `.env` está en `.gitignore`; el
  ejemplo (sin valores) está versionado.
- El proceso principal del app **escucha en el puerto
  declarado por el entorno** (`PORT` para PaaS,
  `8080` por convención para systemd/k8s, etc.). El
  bind es `0.0.0.0` cuando el contenedor lo requiera; no
  se asume `127.0.0.1`.
- El primer comando de un release es la **migración
  idempotente** (ver `area-data` 3.16.0, criterio 1), no
  el start del servicio. La migración corre como
  `release_command` o pre-start hook; **nunca** se
  confunde con el start.
- El path de logs, el path de PID y el path de estado
  efímero son configurables por entorno, no
  hardcodeados a `/tmp/<app>`.
- `HEALTHCHECK` (Docker) y los probes (k8s/PaaS)
  distinguen liveness de readiness cuando el stack lo
  permita: liveness = el proceso responde, readiness =
  el proceso está listo para servir tráfico (DB
  migrada, cache caliente).
- Rollback es un primer paso del release, no una
  ocurrencia tardía. El plan de rollback está en
  `_project/16_release_profile.md` antes del primer
  deploy a prod.

## Anti-patrones (NO)

- **NO** correr `docker push`, `fly deploy`, `railway
  up`, `kubectl apply`, `systemctl restart` desde el
  workflow. El operador decide fuera del loop.
- **NO** commitear `.env`, `secrets.yaml`, `*.pem`,
  `*.key`, dumps de BD, ni credenciales pobladas.
- **NO** asumir que el puerto es fijo; leerlo del
  entorno.
- **NO** ejecutar el seed/migración en el start
  command; va en el release command.
- **NO** usar el mismo secret para dev y prod.
- **NO** afirmar "deploy hecho" sin que el operador
  confirme; la skill **sugiere**, el operador
  **ejecuta**.

## Verificación

Antes de un release, la skill exige que el operador
verifique (checklist, no script que el agente corra):

- `git status` limpio o diferencia intencional
  documentada.
- `.env.example` documenta todas las variables
  requeridas; `.env` (si existe) está en `.gitignore`.
- Migración idempotente escrita y probada contra
  una BD de staging.
- `HEALTHCHECK` o probes declarados; logs a stdout.
- Plan de rollback en `_project/16_release_profile.md`
  o equivalente.
- Operador declara el entorno destino y la
  visibilidad (privado / público) antes del primer
  push.

## Diferido explícitamente (no en esta skill)

- **Subagente `area-devops`** (Fase 4b vetó; entra
  solo con aislamiento verificado + worktrees).
- **Ejecución paralela con escritura** en dev/prod
  (vetada por la regla `AUTO_SUGGEST` 3.9.0).
- **Auto-rollback en runtime** (el operador decide).
