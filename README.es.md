# WCA Translation Studio

Software local para traducir Locales (Ruby YAML), Payload CMS (JSON de mensajes) y Regulations (PO) de WCA del inglés a **cualquier idioma de destino disponible para ese componente en Weblate**, con propuestas de ChatGPT y revisión humana.

**Para traducir:** seguí la [guía simple de uso](USER_GUIDE.es.md). [English](README.md) · [User guide](USER_GUIDE.md).

Este README explica instalación, almacenamiento y desarrollo. La guía de uso explica qué botones tocar en la aplicación y en Weblate.

## Instalación e inicio

Linux y Python 3.10 o posterior:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

Abrí **http://127.0.0.1:8765**. Las próximas veces alcanza con el último comando. Ctrl+C detiene el servidor. Después de actualizar el código, reiniciá el servidor y recargá la página. Opciones: `--port 8768`, `--data-dir /ruta/absoluta`.

El servidor escucha en `127.0.0.1`. Es una aplicación local con un perfil de trabajo, no un servicio público multiusuario.

## Configuración y credenciales

La interfaz usa inglés por defecto, con **🇺🇸 EN / 🇦🇷 ES** para inglés y español argentino. El navegador recuerda el idioma de la interfaz y el modelo elegido. El idioma de la interfaz es independiente del idioma al que se traduce.

Los tokens de Weblate ingresados en la interfaz quedan en memoria hasta reiniciar. Para conservarlos, configurá `.env` con tu editor:

```dotenv
WEBLATE_TOKEN=tu_token
```

`.env.example` no contiene secretos. Las variables de entorno tienen prioridad. El archivo se interpreta sin ejecutar shell ni expandir variables. No lo compartas ni agregues comentarios en la línea del token. `.env`, `data/`, entornos virtuales y configuraciones del editor están excluidos de Git.

ChatGPT usa el flujo local oficial: PKCE, state, nonce, verificación de firma/emisor/audiencia/vigencia/identidad del ID token y autorización para usar el plan y modelo de la cuenta. No accede a conversaciones ni reutiliza credenciales de Codex. Referencias: [inicio de sesión](https://developers.openai.com/siwc/token-sharing-open-source/sign-in) y [modelos e inferencia](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).

Las credenciales se guardan por separado en `data/auth/chatgpt.json`, con permisos `0600`, y nunca se sirven por HTTP. Desconectar elimina las credenciales locales e intenta revocar el acceso remoto; la interfaz avisa si no puede confirmar la revocación. Los registros anteriores se conservan por compatibilidad, mientras la interfaz usa una cuenta activa.

## Datos y funcionamiento

Las sesiones se separan por componente, idioma y modo real/demo/simulación. Se guardan en `data/sessions/<fechaUTC>-<id>/`, con referencias inmutables y hashes, mapeos de formatos nativos, `glossary.tbx` opcional, `work.json`, `PROMPT.txt`, progreso y propuestas. La revisión y las exportaciones quedan en la carpeta `review/` de cada sesión. Los nombres internos `decisiones.json` y `resumen_exportacion.json` se conservan por compatibilidad. Protegé las credenciales al respaldar `data/`.

La sincronización detecta traducciones faltantes, plurales incompletos y entradas marcadas para revisión. Las sincronizaciones siguientes también detectan cambios en el inglés por contexto único. Se descargan referencias actuales y se conservan sesiones anteriores. El progreso representa etapas reales, no bytes ni tiempo estimado. Las horas visibles usan formato de 24 h y la zona horaria del navegador.

La generación requiere una acción explícita y nunca aprueba traducciones. Los lotes adaptativos tienen hasta 32 entradas, un presupuesto aproximado de 8.000 tokens de entrada/salida/contexto y hasta 40 entradas por ejecución. Una entrada demasiado grande se procesa sola, sin truncarla. Los lotes validados se conservan para continuar; se rechazan respuestas interrumpidas, rechazadas o incompletas.

Las solicitudes incluyen IDs temporales cortos, claves solicitadas, contexto, glosario y hasta ocho ejemplos relacionados del corpus (máximo 6.000 caracteres). El glosario completo se incluye hasta aproximadamente 2.000 tokens; si es mayor, se seleccionan términos relevantes y esenciales de WCA. Las variantes gramaticales explícitas están en `app/glossary.py`, para contexto y validación; no se deducen traducciones ni variantes para otros idiomas. Se usa Responses API con `store: false`, streaming y JSON estructurado, bajo los límites de la cuenta conectada. El contador mide caracteres del JSON recibido, no del pedido.

Los borradores se guardan tras 500 ms sin escribir y requieren aprobación antes de exportar. Se respetan códigos y alias de idioma. Para Locales se contrastan Babel/CLDR, YAML nativo y API para identificar `zero`, `one`, `two`, `few`, `many`, `other`; un esquema ambiguo bloquea la importación. Los editores admiten dirección automática para idiomas RTL.

## Validación y exportaciones

La descarga normal contiene propuestas aceptadas y ediciones aprobadas. Se omiten pendientes y rechazadas, y debe existir al menos un cambio aprobado. Cargar una descarga reciente de Weblate permite detectar cambios concurrentes y usar ese archivo como base de exportación. Exportar no consulta Weblate.

| Componente | Nombre del archivo |
| --- | --- |
| Locales | `<idioma>_locales_reviewed.yaml` |
| Payload CMS | `<idioma>_payload_reviewed.json` |
| Regulations | `<idioma>_regulations_reviewed.po` |

Los códigos de idioma usan guiones en los nombres descargados. Locales conserva plurales y listas necesarias para mantener índices. Payload conserva metadatos y campos `message`. Regulations incluye encabezado PO. Los PO de sesiones Locales y Payload son referencias internas. Los modos antiguos de exportación completa/sugerencias siguen en el backend; los archivos de sugerencias también incluyen el componente.

Son archivos para **fusionar cambios aprobados**, no para reemplazar archivos completos del repositorio. Las instrucciones de carga están en la [guía de uso](USER_GUIDE.es.md). Weblate documenta sus [métodos de importación y permisos](https://docs.weblate.org/en/latest/user/files.html#uploading-translations).

Los controles heurísticos de placeholders, enlaces, etiquetas y terminología pueden descartarse individualmente. Cada descarte se asocia a control, forma y huella de referencia/texto, deja de aplicar cuando cambia el texto y se registra con fecha y hora en decisiones y resúmenes de exportación. No hay un bypass global. Conflictos, textos vacíos, cantidades de plurales incorrectas, caracteres inválidos y errores estructurales requieren corrección. Los controles técnicos no garantizan calidad lingüística.

## Red y acceso

Weblate recibe únicamente solicitudes GET. El token conserva los permisos de la cuenta y nunca se envía a ChatGPT. Si no se puede descargar el archivo nativo de Locales, la base puede reconstruirse desde unidades accesibles; se advierte que faltan comentarios y otros datos del original. Regulations puede reconstruir la referencia inglesa desde unidades fuente y descargar el PO destino nativo.

401/403 requieren revisar acceso; 429 requiere esperar. Los reintentos son limitados y respetan `Retry-After`. Si las referencias remotas cambian durante la descarga, se reintenta la descarga completa una vez. Un segundo cambio falla sin reemplazar la sesión anterior. El servidor verifica Host y origen, permite la navegación de regreso OAuth y bloquea solicitudes externas a la API. Las consultas de idiomas/modelos no bloquean las decisiones de revisión local. Los conteos globales pueden incluir componentes privados o no admitidos.

## Pruebas manuales

```bash
.venv/bin/python run.py --demo
.venv/bin/python run.py --simulate
```

Ejecutá un modo por vez. Demo usa **http://127.0.0.1:8766**, datos y propuestas ficticias, sin red ni credenciales, en `data/demo/`. Simulación usa **http://127.0.0.1:8767**, una descarga real de solo lectura y tres huecos artificiales en una copia local de `data/simulation/`. Usa la conexión de IA elegida explícitamente al solicitar traducción. Se conserva la descarga original; la traducción anterior sirve para comparación humana y no se envía como contexto a la generación automática.

Las descargas de prueba llevan prefijo `DEMO_` o `SIMULATION_` y nunca se suben a Weblate. Los modos real, demo y simulación no pueden compartir carpeta de datos.

## Flujo con agente externo

El flujo manual opcional produce solo `proposals.json`; el agente no cambia bases ni aprueba entradas. Seguí `PROMPT.txt` de la sesión elegida y `AGENTS.md`.

```json
{"version":1,"session_id":"ID","base_sha256":"HASH","entries":[{"key":"CLAVE_EXACTA","target":["Una cadena por forma"],"note":"Motivo o duda"}]}
```

Validá con el comando de la sesión:

```bash
.venv/bin/python run.py validate-proposals ID --data-dir /ruta/a/data
# Agregar --demo o --simulate según el modo.
```

## Desarrollo y despliegue

```bash
.venv/bin/python -m unittest discover -s tests -v
RUN_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

Las pruebas usan proveedores/cuentas ficticias, JWT de prueba, localhost y carpetas temporales, sin llamadas de IA facturables. Las de navegador necesitan Chrome/Chromium y Node >=22; no descargan navegadores. GitHub Actions ejecuta pruebas Python en pushes y pull requests sin secretos. Se versionan código y pruebas, no credenciales, sesiones ni exportaciones de prueba.

Un despliegue público requiere un punto de entrada de producción separado, origen público y callback OAuth HTTPS autorizado, autorización para aplicaciones alojadas, aislamiento de credenciales/sesiones/trabajos/archivos por usuario, almacenamiento durable y pruebas con cuentas separadas. `python run.py` no proporciona eso. Referencias: [requisitos de OpenAI para aplicaciones alojadas](https://developers.openai.com/siwc/token-sharing-open-source), [servicios web de Render](https://render.com/docs/web-services), [discos persistentes](https://render.com/docs/disks), [funciones de Vercel](https://vercel.com/docs/functions/runtimes). WCA OAuth requeriría una [aplicación registrada](https://docs.worldcubeassociation.org/knowledge_base/v0_api.html) y no sustituiría permisos de Weblate. El repositorio no publica ni despliega automáticamente.
