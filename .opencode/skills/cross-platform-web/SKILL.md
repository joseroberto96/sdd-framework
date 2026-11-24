---
name: "cross-platform-web"
description: "Se activa al implementar módulos frontend o web — garantiza compatibilidad en Windows, Mac, Android e iOS"
---

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# Experto en Compatibilidad Web Cross-Platform

**Activación:** Cuando el módulo activo toca HTML, CSS, JavaScript, o cualquier interfaz de usuario web.

**Plataformas objetivo:** Windows (Chrome, Edge, Firefox) · macOS (Safari, Chrome) · Android (Chrome) · iOS (Safari)

---

<!-- CONTEXT_SOURCE: file_obligations -->
## Reglas de implementación

### CSS

- **Unidades de viewport en móvil**: usar `dvh` (dynamic viewport height) o el workaround `100vh` + `env(safe-area-inset-bottom)` para iOS Safari, donde la barra de dirección reduce el viewport.
- **Scroll momentum en iOS**: `-webkit-overflow-scrolling: touch` está deprecado — usar `overflow: auto` con `overscroll-behavior: contain`.
- **Fuentes**: declarar siempre `font-family` con fallbacks del sistema. Nunca asumir que una fuente está instalada.
- **Scrollbar styling**: `::-webkit-scrollbar` no funciona en Firefox ni iOS. Si se necesita scrollbar personalizado, usar la API estándar `scrollbar-width` + `scrollbar-color` con prefijo webkit como fallback.
- **Flexbox/Grid gaps**: `gap` en flexbox no funciona en Safari < 14.1 — usar `margin` como fallback si el soporte legacy importa.
- **`position: sticky`**: funciona en todos los targets modernos. En iOS requiere que el contenedor padre tenga `overflow: visible`, no `hidden`.

### JavaScript

- **Separadores de ruta**: nunca hardcodear `/` ni `\` — usar la API del entorno (en Node: `path.join`; en browser: siempre `/`).
- **Clipboard API**: `navigator.clipboard` requiere HTTPS y contexto seguro. Proveer fallback con `document.execCommand('copy')` para contextos sin HTTPS (desarrollo local en Windows con `http://localhost` ya es seguro — pero no en iOS con IP local).
- **Eventos de puntero**: usar `pointer events` (`pointerdown`, `pointermove`, `pointerup`) en lugar de mouse events para cubrir touch y stylus. Si se necesita distinción táctil explícita, detectar con `event.pointerType === 'touch'`.
- **Date parsing**: `new Date('2024-01-01')` parsea en UTC en la mayoría de browsers pero en hora local en algunos. Siempre usar ISO 8601 completo con zona horaria (`2024-01-01T00:00:00Z`) o una librería (date-fns, Temporal).
- **Scroll behavior**: `window.scrollTo({ behavior: 'smooth' })` no funciona en Safari < 15.4 — usar una librería o `requestAnimationFrame` si importa el soporte.
- **File System Access API**: no disponible en Firefox ni iOS Safari — no asumir disponibilidad, siempre verificar con `'showOpenFilePicker' in window`.

### HTML / Forms

- **Input types en iOS**: `type="date"`, `type="time"`, `type="datetime-local"` tienen UI nativa diferente en iOS. `type="number"` en iOS muestra teclado numérico sin signos. `inputmode="decimal"` es más predecible.
- **`autocomplete`**: iOS Safari ignora `autocomplete="off"` en passwords — es comportamiento del browser, no un bug a "corregir".
- **Zoom en inputs**: iOS hace zoom en inputs con `font-size < 16px`. Regla: `input, textarea, select { font-size: 16px; }` o usar `meta viewport` con `maximum-scale=1` (pero este último perjudica accesibilidad — preferir la solución de font-size).
- **Focus management**: en iOS, el teclado virtual desplaza la vista. Gestionar el scroll activo si hay elementos debajo del fold.

### Build / Tooling

- **Rutas en scripts npm**: usar `cross-env` para variables de entorno en Windows (el `NODE_ENV=production` en package.json falla en cmd/PowerShell). Usar `rimraf` en lugar de `rm -rf`. Usar `mkdirp` en lugar de `mkdir -p`.
- **Rutas relativas en imports**: usar siempre `/` — los bundlers modernos (Vite, Webpack) las normalizan. Nunca construir rutas con concatenación de strings.
- **Case sensitivity**: Windows y macOS (por defecto) son case-insensitive en el filesystem; Linux no. Un import `./Components/Button` que carga `./components/button` funciona en dev pero falla en producción Linux. Consistencia de mayúsculas es obligatoria.
- **Line endings**: configurar `.gitattributes` con `* text=auto` para evitar que Windows convierta LF a CRLF y rompa scripts bash.

---

## Anti-patrones frecuentes

| Anti-patrón | Plataforma afectada | Fix |
|---|---|---|
| `height: 100vh` en móvil sin safe-area | iOS Safari | Usar `dvh` o `min-height: -webkit-fill-available` |
| `rm -rf` en scripts npm | Windows | `rimraf` |
| `NODE_ENV=x npm run` | Windows (cmd) | `cross-env NODE_ENV=x npm run` |
| `new Date('2024-01-01')` | Safari, algunos Chrome | ISO 8601 completo con zona horaria |
| `import './Component'` (case inconsistente) | Producción Linux | Verificar mayúsculas exactas |
| `addEventListener('mouseover')` en touch | Android, iOS | `pointerover` o detección de tipo |
| `font-size: 14px` en inputs | iOS (zoom automático) | `font-size: 16px` en inputs |
| `window.open()` sin user interaction | iOS Safari | Solo invocar en handlers directos de click/tap |
<!-- END_CONTEXT_SOURCE: file_obligations -->

---

## Checklist de revisión (activar en `/cierre-modulo` para módulos frontend)

- [ ] ¿Los event listeners usan Pointer Events en lugar de solo Mouse Events?
- [ ] ¿Los scripts de npm son compatibles con Windows (sin `rm -rf`, sin `NODE_ENV=x` sin `cross-env`)?
- [ ] ¿Los imports respetan exactamente el casing del filesystem?
- [ ] ¿Los inputs tienen `font-size: 16px` o superior para evitar zoom en iOS?
- [ ] ¿Se usa `dvh` o safe-area-inset para elementos de altura full-screen en móvil?
- [ ] ¿Los parseos de fecha usan ISO 8601 completo con zona horaria?
- [ ] ¿Las rutas construidas en código usan `/` y no `\` ni concatenación de strings?
- [ ] ¿El repositorio tiene `.gitattributes` con `* text=auto`?
