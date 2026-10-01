# Sidekick

**Un agente de IA local que se ejecuta en tu dispositivo Android.** Un solo archivo Python, cero dependencias de terceros, el navegador como interfaz.

No solo conversa — **realmente opera tu dispositivo**: lee y escribe archivos, ejecuta comandos, edita código, navega por la web, controla tu teléfono, crea hojas de cálculo, procesa imágenes.

> `Sidekick` es solo el nombre predeterminado de fábrica. Cámbialo por el que quieras en la página «Identidad».

**Idiomas:** [English](README.en.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | **Español** | [Deutsch](README.de.md) | [Français](README.fr.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

---

## Qué lo hace diferente

### 1. Instalación a prueba de torpes — dos comandos, sin un tercer paso

No hace falta saber Python. Ni configurar el entorno. Ni instalar una base de datos. Instala Termux, pega dos líneas, abre el navegador y listo.

```bash
pkg install -y python curl
bash install.sh
```

Sin Docker, sin Node, sin conflictos de dependencias. Usa **únicamente la biblioteca estándar de Python**, así que las actualizaciones externas no pueden romperlo: si funciona hoy, seguirá funcionando dentro de tres años.

### 2. Evoluciona por sí solo cada día — sin mantenimiento y sin actualizaciones remotas

Esto es lo más singular: **cada día se vuelve un poco mejor, y nadie necesita enviarte una actualización.**

Cada día revisa su propio desempeño, encuentra algo que vale la pena mejorar, aplica el cambio y lo escribe en un registro. Este es trabajo real que hizo por su cuenta:

| Fecha | Lo que cambió por sí solo |
|---|---|
| 09-28 | Añadió sugerencias de «archivar por tipo» a `list_dir`: al listar archivos, ahora te dice que las imágenes van en `Pictures/` y los documentos en `Documents/` |
| 09-28 | Cuando un argumento de herramienta está mal escrito, descarta en silencio los argumentos no admitidos y sugiere el nombre correcto más cercano (p. ej. `cwd` → `command`) |
| 09-29 | `read_file` ya no vomita basura con archivos binarios; en su lugar informa del tipo y tamaño del archivo |
| 10-01 | Fijó la altura de la tarjeta de consola, porque las pruebas revelaron que el botón Guardar se desplazaba 447 px al cambiar de pestaña y provocaba pulsaciones erróneas |

> No son eslóganes de marketing. Son extractos de su propio registro de evolución. Cuando se ejecute en tu máquina, desarrollará mejoras **relevantes para ti**.

**Qué significa esto:** tu agente y el de otra persona divergirán tras un mes de uso. Si le pides a menudo que organice álbumes de fotos, evolucionará un manejo de imágenes más potente. Si lo usas para informes, evolucionará un flujo de trabajo documental más fluido. **Cada dispositivo cría un agente único.**

### 3. Arregla sus propios errores — y puedes verlo diagnosticar

Cuando el programa falla, no deja solo un volcado de pila. Tiene un mecanismo de **autorreparación** integrado: detectar el fallo → leer sus propios registros para localizarlo → corregir el código → verificar → registrar el resultado.

Un registro real (de `selfheal.json`):

```
10-01 14:22  Fallo detectado   registro del watchdog · huella f99407371b41
10-01 14:23  Reparación lista  Confirmado: la línea 2809 es código de lectura
                               de registros no relacionado, y tool_wx_auto está
                               definido. No se necesitan más cambios.
```

Incluso **revierte su propia conclusión errónea**: en otra autorreparación concluyó que «esto no es un error que haya que arreglar, el archivo ya se reparó solo» y expuso las pruebas en una tabla. No cambia cosas a ciegas.

Antes de modificarse a sí mismo **hace una copia de seguridad**, después **ejecuta una comprobación de sintaxis** y **revierte automáticamente** si no arranca. Así que «se arregla solo» no significa «se destruye solo».

### 4. Retoma donde lo dejó — las tareas largas sobreviven a las interrupciones

¿Una tarea de media hora y Android mata Termux? Tras reiniciar, **se reanuda automáticamente**, sin que tengas que explicarle todo otra vez.

```bash
$ tail evolve.log
[autorreparación] Marcador de reanudación explícito encontrado; continuando automáticamente
[autorreparación] Reanudando 20260928-034008-1058 (boot), intento 2
```

Reinicio del teléfono, cambio de red, Termux cerrado por el sistema: recuerda en qué punto estaba.

---

:::tip En una frase

**Fácil de instalar, crece con el uso, se arregla solo cuando falla y se reanuda cuando lo interrumpes.**

:::

### Compruébalo tú mismo — no me creas por fe

Tras instalarlo, mira sus propios registros de evolución:

```bash
cat ~/.termux-agent/evolve.json     # qué mejoró para sí mismo y por qué
cat ~/.termux-agent/selfheal.json   # qué fallos encontró y arregló
tail ~/.termux-agent/evolve.log     # registro en vivo de evolución y autorreparación
```

Estos archivos los **escribe el agente en tiempo de ejecución**, no son material de marketing preempaquetado. Déjalo funcionar un tiempo y se llenarán de registros que te pertenecen.

---

## Índice

- [Qué lo hace diferente](#qué-lo-hace-diferente)
  - [Instalación a prueba de torpes](#1-instalación-a-prueba-de-torpes--dos-comandos-sin-un-tercer-paso)
  - [Evolución diaria](#2-evoluciona-por-sí-solo-cada-día--sin-mantenimiento-y-sin-actualizaciones-remotas)
  - [Autorreparación](#3-arregla-sus-propios-errores--y-puedes-verlo-diagnosticar)
  - [Reanudación tras interrupción](#4-retoma-donde-lo-dejó--las-tareas-largas-sobreviven-a-las-interrupciones)
- [Qué es](#qué-es)
- [Instalación](#instalación)
- [Configurar la clave de API](#configurar-la-clave-de-api)
- [Uso diario](#uso-diario)
- [Resumen de capacidades](#resumen-de-capacidades)
- [Referencia de herramientas (20)](#referencia-de-herramientas-20)
- [Biblioteca de skills (36)](#biblioteca-de-skills-36)
- [Estructura de directorios](#estructura-de-directorios)
- [Notas de seguridad](#notas-de-seguridad)
- [Preguntas frecuentes](#preguntas-frecuentes)

---

## Qué es

Un asistente de IA que vive en tu teléfono o tablet y se usa a través de una interfaz de navegador.

**Decisiones de diseño**

| Principio | Explicación |
|---|---|
| **Cero dependencias de terceros** | Solo usa la biblioteca estándar de Python; basta con `pkg install python`. No toca módulos nativos que requieran compilación, así que las actualizaciones externas nunca podrán romperlo |
| **Almacenamiento local** | Conversaciones, memoria y claves permanecen en `~/.termux-agent/`, en tu dispositivo. Nada se sube a la nube |
| **Puede modificarse a sí mismo** | Puede editar su propio código fuente para añadir funciones y revierte automáticamente si el cambio lo rompe |
| **Incluye un watchdog** | Si el servicio muere, se reinicia automáticamente, sin systemd (que Android no tiene) |
| **Sin actualizaciones remotas** | Sin servidor, sin envío de actualizaciones. Su evolución es autónoma; las nuevas capacidades no dependen de que el autor publique una versión |

---

## Instalación

Requisitos: tener [Termux](https://f-droid.org/packages/com.termux/) instalado en el dispositivo Android (**se recomienda la versión de F-Droid**: los complementos deben compartir la firma de la app principal).

```bash
# 1. Instalar Python
pkg update && pkg install -y python curl

# 2. Desde este directorio, ejecutar el instalador
bash install.sh
```

El instalador hará, en orden: comprobar el entorno → respaldar cualquier instalación existente → copiar el programa y las skills → generar una configuración predeterminada (**sin sobrescribir una clave de API existente**) → iniciar el servicio y autoprobarse.

---

## Configurar la clave de API

Necesitas una clave de API en el primer arranque. Dos formas:

**Opción 1 (recomendada)**: abre `http://127.0.0.1:8765/` y complétala en «Ajustes», arriba a la derecha.

**Opción 2**: desde la línea de comandos
```bash
python3 ~/.termux-agent/agent.py config
```

Por defecto usa DeepSeek (`https://api.deepseek.com`), pero puedes apuntarlo a cualquier endpoint compatible con OpenAI, incluido un modelo local en tu red.

---

## Uso diario

```bash
bash ~/.termux-agent/start.sh              # restaurar el servicio tras reiniciar Termux
python3 ~/.termux-agent/agent.py doctor    # comprobación del entorno + prueba de conectividad
python3 ~/.termux-agent/agent.py restart   # reiniciar el servicio
python3 ~/.termux-agent/agent.py selfcheck # autoinspección (código/watchdog/servicio/copias)
```

Después abre **http://127.0.0.1:8765/** en el navegador.

### Uso directo desde la línea de comandos (sin interfaz)

```bash
python3 ~/.termux-agent/agent.py "mira el uso de disco"   # ejecutar una vez y salir
python3 ~/.termux-agent/agent.py -c                       # continuar la última conversación
python3 ~/.termux-agent/agent.py chat                     # conversar en la terminal (básico)
```

### Inicio automático al arrancar (opcional)

Instala [Termux:Boot](https://f-droid.org/packages/com.termux.boot/), ábrelo una vez manualmente y luego:

```bash
python3 ~/.termux-agent/agent.py autostart
```

---

## Resumen de capacidades

| Categoría | Capacidades |
|---|---|
| **Operaciones del sistema** | Ejecutar shell, leer ajustes del sistema con privilegios de shell, instalar APK, gestionar procesos |
| **Gestión de archivos** | Leer/escribir/editar archivos, parches por lotes, explorar directorios, búsqueda recursiva por regex, lectura paginada de archivos grandes |
| **Red** | Obtener páginas web como texto, búsqueda web sin clave, descarga en streaming de archivos grandes |
| **Alojamiento de código** | Flujo completo de GitHub (crear repo / clone / pull / commit / push / buscar código) |
| **Control de dispositivos** | Operar remotamente un teléfono Huawei (protocolo HDC), actuar en este dispositivo como adb shell |
| **Procesamiento de imágenes** | Eliminar texto o marcas de agua, restaurar fotos antiguas, mejorar calidad, retocar retratos, recortar fondos |
| **Generación de documentos** | Generar Word / Excel / PPT, convertir HTML a DOCX, plantillas de contratos, refinado de maquetación |
| **Gestión de tareas** | Listas de tareas, razonamiento paralelo con subagentes, consultarte decisiones |
| **Autoevolución** | Modificar su propio código para añadir funciones, con copia automática + comprobación de sintaxis + reversión si falla |
| **Skills de dominio** | 36 documentos de skills de dominio (redacción, diseño, legal, finanzas, depuración, …) |

---

## Referencia de herramientas (20)

### Sistema y archivos

| Herramienta | Función | Destacado |
|---|---|---|
| `bash` | Ejecutar comandos de shell | La herramienta principal: instalar paquetes, gestionar archivos, ejecutar programas |
| `read_file` | Leer archivos de texto (con números de línea) | Lectura paginada para archivos grandes; un `offset` negativo lee los registros desde el final |
| `write_file` | Escribir un archivo completo | Crea los directorios padre automáticamente |
| `edit_file` | Reemplazo exacto de cadenas | Exige coincidencia única; si hay varias, informa de sus números de línea |
| `apply_patch` | Parche multi-tramo / multi-archivo | **Atómico**: si alguna parte no aplica, no se escribe nada; no hay ediciones a medias |
| `list_dir` | Listar un directorio | Incluye tipo, tamaño y fecha de modificación |
| `grep` | Búsqueda recursiva por regex | Devuelve `archivo:línea: contenido` |

### Red

| Herramienta | Función | Notas |
|---|---|---|
| `fetch_url` | Obtener una página/API como texto plano | Solo para leer texto |
| `web_search` | Búsqueda web | **No requiere clave de API** |
| `download` | Descarga en streaming de archivos grandes | Sin truncamiento, con tiempo de espera y reintento automático. Ideal para APK e instaladores |

### Dispositivos y sistemas externos

| Herramienta | Función | Notas |
|---|---|---|
| `sysshell` | Ejecutar como shell (adb) | Privilegios superiores a una app normal: leer ajustes del sistema, `dumpsys`, `getprop`, `pm/am`. **No es root** |
| `hdcmate` | Controlar remotamente un teléfono Huawei | Usa el protocolo HDC (no adb). `exec` ejecuta un comando / `target` guarda la dirección / `test` comprueba la conexión. Permite automatización completa de la interfaz |
| `github` | Operar repositorios de GitHub | Nueve acciones: `list/repo/read/tree/clone/pull/push/create/search`. Requiere un token |
| `wps` | Generar Word/Excel/PPT | Implementado con un servicio MCP local, **sin necesidad de cuenta**. Los archivos quedan en `~/storage/shared/WPS_AI/` |
| `imgedit` | Procesamiento de imágenes con IA | Cinco operaciones: `erase` texto/marca de agua / `restore` fotos antiguas / `enhance` calidad / `beauty` retrato / `matting` recorte |

### Colaboración y autoevolución

| Herramienta | Función | Notas |
|---|---|---|
| `todo_write` | Mantener una lista de tareas | Divide tareas de 3+ pasos en 2–6 elementos, mostrados en vivo sobre tu cuadro de entrada; tras reconectar continúa desde ahí |
| `subagent` | Pensamiento independiente de un subagente | Entrega un subproblema aislado a un agente aparte (sin acceso a los archivos locales). Útil para paralelizar problemas difíciles |
| `ask_user` | Pedirte que decidas | Abre un panel de opciones. Se usa solo cuando tu decisión es imprescindible: gastar dinero, borrar datos, elegir entre enfoques |
| `selfupdate` | **Modificarse a sí mismo** | Copia automática + comprobación de sintaxis, reversión inmediata si falla; el servicio se reinicia solo al tener éxito |

### Archivado

| Herramienta | Notas |
|---|---|
| `wx_auto` | Acompañante de respuesta automática de WeChat (lectura de pantalla + respuesta automática; requiere autorización y conocimiento de la otra parte). Tres barreras de seguridad: las lecturas dudosas solo se registran, abandona si no encuentra el botón de enviar, y limita a 20 mensajes por hora |

---

## Biblioteca de skills (36)

Las skills son **documentos de metodología de dominio** que se entregan a la IA. Antes de una tarea relevante, lee la skill correspondiente y sigue las convenciones y lecciones aprendidas que contiene.

### Redacción (11)

| Skill | Propósito |
|---|---|
| `general-writer` | **Respaldo de redacción general (L1).** Documentos oficiales, informes semanales, propuestas, correos, copywriting, ensayos, nuevos medios; puntuación de calidad en 7 dimensiones + matriz de adaptación a 10 géneros |
| `academic-paper-expert` | Artículos académicos: diseño de estructura, revisión de literatura, resúmenes, reglas de cita APA/GB-T7714, pulido académico |
| `tech-blog-expert` | Blogs técnicos: tutoriales, análisis de arquitectura, análisis de código fuente, documentación de código abierto, README |
| `business-copy-expert` | Copy comercial: copy de marca, correos de marketing, descripciones de producto, eslóganes, cumplimiento publicitario (modelo AIDA) |
| `work-report-expert` | Informes laborales: resúmenes anuales, evaluaciones de desempeño, discursos de ascenso, informes semanales/mensuales (Principio de la Pirámide + STAR) |
| `science-writing-expert` | Divulgación científica: explicaciones, reseñas tecnológicas, reportajes extensos (técnica de Feynman) |
| `poetry-prose-expert` | Poesía y prosa: poesía moderna, verso clásico chino, ensayos, crítica literaria |
| `stock-research-report-expert` | **Investigación de valores (L2).** Análisis sectorial profundo, análisis de una acción, comentario de eventos, planes de negocio; cuatro niveles de extensión |
| `legal-contract-expert` | **Contratos legales (L2).** Redacción y revisión: exhaustividad de cláusulas obligatorias, simetría de derechos y obligaciones, prevención de riesgos altos |
| `humanizer-zh` | Eliminar el «aire de IA» (chino): detecta y corrige según la guía de Wikipedia sobre signos de escritura con IA |
| `humanizer` | Eliminar el «aire de IA» (inglés) |

### Generación de documentos (5)

| Skill | Propósito |
|---|---|
| `doc-typeset` | **Maquetación y refinado.** Consume design tokens + contenido y produce HTML pulido. Incluye 7 plantillas verticales (contratos / artículos académicos / documentos oficiales / informes de negocio / actas de reunión / informes de investigación / memorias anuales) |
| `html-to-docx` | HTML → Word de alta fidelidad. Preprocesa variables CSS, mapea con precisión 10+ tipos de elementos y 14 propiedades CSS |
| `format-extract` | Convierte .docx en HTML semántico + extrae imágenes incrustadas (conservando jerarquía de títulos, estilos de tabla, sangrías y colores) |
| `generate-fillable-contract-html` | Genera HTML rellenable de contratos, presupuestos y poderes notariales en chino |
| `underline-toolkit` | Documentos con espacios en blanco: `create` genera una plantilla vacía / `fill` vuelca datos en una plantilla existente (contratos, formularios, portadas de trabajos) |
| `html-review` | **Control de calidad de HTML.** Aplica comprobaciones en 5 dimensiones a la salida maquetada (cumplimiento de tokens, integridad estructural, sensatez del diseño, adecuación al género, moderación en la decoración) y la devuelve para correcciones específicas si falla |

### Diseño (8)

| Skill | Propósito |
|---|---|
| `design-router` | Distribuidor de tareas de diseño: decide qué familia de diseño aplica y luego la envía |
| `design-token` | Emite design tokens estandarizados por tipo de documento, guiando todas las decisiones de estilo de doc-typeset |
| `design-variables` | Vincula/desvincula variables de diseño (design tokens) a propiedades de nodo |
| `ardot-design-to-code` | Convierte maquetas de diseño en código front-end, o extrae un sistema de diseño / guía de estilo de un sitio web |
| `ardot-ui-design` | Diseño de UI: páginas web, paneles, landing pages, interfaces móviles |
| `ardot-poster` | Carteles visuales: carteles, folletos, vallas, banners, visuales clave de campaña |
| `ardot-slides` | Diseño de presentaciones (no archivos .pptx, sino maquetas de diseño) |
| `component-instance` | Gestión de instancias de componentes: crear/actualizar instancias, definir propiedades, cambiar variantes |
| `shared-styles` | Vincular/desvincular estilos compartidos (estilos de texto, rellenos, trazos, efectos) |

### Depuración y operaciones (5)

| Skill | Propósito |
|---|---|
| `termux-traps` | **Trampas del entorno Android Termux**: los peligros en este tipo de dispositivo y la forma correcta de manejarlos |
| `log-debug` | Guía de diagnóstico por registros: qué archivo revisar primero cuando algo falla |
| `selfupdate` | El flujo correcto de automodificación: la vía adecuada y las reglas de oro para editar `agent.py` |
| `ui-debug` | Depuración de la interfaz web: cambiar la UI y verificarla en el momento |
| `pc-debug` | Ejecutar comandos y leer/escribir archivos en ese PC con Windows mediante el «puente de depuración de PC» |

### Conectividad de dispositivos (2)

| Skill | Propósito |
|---|---|
| `hdcmate` | Depuración de teléfonos por HDC: notas del protocolo, comandos habituales de HarmonyOS, automatización de UI, dificultades encontradas |
| `chrome-cdp` | Depuración y scraping de Chrome: leer datos web con el protocolo DevTools sin tocar la pantalla |

### Gestión de skills (4)

| Skill | Propósito |
|---|---|
| `find-skills` | Te ayuda a descubrir e instalar skills |
| `skill-creator` | Guía para crear nuevas skills |
| `marketplace-skill-installer` | Buscar e instalar desde un mercado de skills |
| `underline-toolkit` | Ver «Generación de documentos» arriba |

---

## Estructura de directorios

```
~/.termux-agent/
├── agent.py          Programa principal (archivo único, solo biblioteca estándar)
├── cdp.py            Cliente CDP (depuración/scraping del navegador; usado por ui-debug / chrome-cdp)
├── chrome_read.py    Lector de datos web de Chrome (usado por chrome-cdp)
├── hdc.py            Implementación del protocolo HDC (usado por hdcmate; 447 líneas)
├── wps.py            Generación de documentos WPS
├── wps_mcp_server.py Servicio MCP local de WPS
├── mcp_call.py       Ayudante de llamadas MCP
├── config.json       Configuración (contiene la clave de API, modo 600)
├── skills/           Documentos de skills (36)
├── sessions/         Registros de conversaciones
├── memory.md         Memoria a largo plazo
├── evolve.json       Registro de evolución (qué cambió cada día y por qué)
├── evolve.log        Registro de ejecución de la evolución
├── selfheal.json     Historial de autorreparación (fallo detectado → reparación completada)
├── selfheal.py       Módulo de autorreparación y reanudación
├── uploads/          Imágenes subidas
├── outputs/          Artefactos generados
├── versions/         Copias de versiones del código (para la autoactualización)
├── logs/             Registros
├── tmp/              Archivos temporales
├── supervisor.sh     Watchdog (autogenerado)
└── start.sh          Script de arranque
```

---

## Notas de seguridad

- La interfaz **escucha solo en `127.0.0.1`** y no se expone a la red local (la interfaz tiene privilegios de ejecución de comandos)
- `config.json` y los archivos de token tienen modo `600`
- Las operaciones irreversibles —formatear, escribir directamente en dispositivos de bloque, borrar el directorio raíz— se interceptan automáticamente
- Sin privilegios de root, y no puede leer datos privados de otras apps
- Autocomprobación del entorno: `python3 ~/.termux-agent/agent.py doctor`

---

## Preguntas frecuentes

**¿El servicio no arranca?**
```bash
python3 ~/.termux-agent/agent.py selfcheck   # ver qué dice de sí mismo
tail -30 ~/.termux-agent/supervisor.log      # revisar el registro del watchdog
```

**¿De verdad evoluciona solo? ¿Qué ha cambiado?**
Mira `~/.termux-agent/evolve.json`: cada entrada registra qué cambió y por qué. Consulta rápida desde la línea de comandos:
```bash
python3 -c "import json;print('\n'.join(f'{e[\"time\"]}  {e[\"summary\"].splitlines()[0]}' for e in json.load(open('$HOME/.termux-agent/evolve.json'))['log']))"
```

**¿En qué se diferencia la autoevolución de una actualización remota?**
No descarga ningún paquete de actualización y ningún servidor le envía versiones. **Lee su propio código y sus registros y edita sobre la marcha.** Por eso las capacidades difieren entre dispositivos: depende de cómo lo uses.

**¿Y si se rompe al editar su propio código?**
La vía `selfupdate` **hace copia primero → comprueba la sintaxis tras la edición → restaura de inmediato si falla**. Aunque el nuevo código arranque pero se comporte mal, el watchdog revierte a la versión anterior. El propio proceso de autorreparación deja registro (`selfheal.json`).

**¿El sistema mató una tarea larga a mitad?**
Tras reiniciar se reanuda automáticamente; no hace falta repetir nada. Verás «reanudando» en el registro.

**Cambié el código y no surte efecto.**
El código nuevo solo surte efecto cuando el navegador lo carga una vez; basta con refrescar la página.

**¿Dónde puedo ver los problemas que ha encontrado?**
`skills/termux-traps.md` documenta las trampas del entorno en este tipo de dispositivo.

**¿Cómo añado nuevas capacidades?**
Usa la skill `skill-creator` para crear un nuevo `.md` en `skills/`, o deja que la IA añada una herramienta por sí misma con `selfupdate`.

---

## Licencia

Un proyecto personal. Tómalo y úsalo como quieras.
