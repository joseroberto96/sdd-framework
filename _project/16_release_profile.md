# Perfil de Publicación y Distribución

> Define cómo se publica el proyecto y cómo otras personas reciben actualizaciones.
> Se usa con `/publicar-github` y durante cierres de fase.

## Repositorio

- Plataforma: [GitHub u otro host]
- URL remota: [URL del proyecto]
- Rama principal: [rama]
- Visibilidad: [público o privado]
- Responsable de publicar: [responsable]

## Fase 0 — Esqueleto publicable

La fase 0 existe para subir temprano el esqueleto mínimo del proyecto, antes de tener todo el producto construido.
Si se activa, `03_project_state.json.current_phase` puede iniciar como `phase_00` hasta completar este checklist y publicar el primer commit/tag.

### Criterios mínimos

- [ ] Framework inicializado (`CLAUDE.md`, `AGENTS.md`, `_project/`, `.agent/`)
- [ ] `.gitignore` revisado para excluir secretos, caches, builds y datos locales
- [ ] `.gitattributes` con `* text=auto`
- [ ] README con nombre, objetivo, instalación y cómo actualizar
- [ ] Sin secretos en archivos versionados
- [ ] Tests/checks mínimos documentados, aunque sean manuales
- [ ] Instrucciones para usuarios: `git clone` inicial y `git pull` para actualizaciones

## Distribución para usuarios

Audiencia principal:
- [describir quién recibirá el proyecto]

Modo recomendado:
- `git clone <URL-del-repositorio>` para primera instalación
- `git pull --ff-only` para bajar actualizaciones
- Releases/tags para hitos estables: `phase-0`, `mvp-0.1`, etc.

## Checklist antes de push

- [ ] `git status` revisado
- [ ] No hay `.env`, tokens, claves, dumps o datos privados versionados
- [ ] Checks focalizados ejecutados o justificación registrada
- [ ] `03_project_state.json` y `04_active_scope.md` reflejan el estado actual
- [ ] `13_session_journal.md` no tiene pendientes no persistidos
- [ ] Commit message describe el cambio en español o inglés consistente

## Historial de publicaciones

| Fecha | Rama | Commit/tag | Tipo | Qué se publicó | Verificación |
|---|---|---|---|---|---|
| — | — | — | — | — | — |
