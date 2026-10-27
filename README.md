# WCA Translation Studio

Herramienta web local para traducir WCA Locales, Regulations y Payload CMS con ChatGPT y revisión humana. El flujo normal es **componente e idioma → sincronizar y traducir → revisar → descargar cambios aprobados**.

## Iniciar

Linux y Python 3.10 o posterior:

```bash
python3 -m venv .venv             # solo si no existe
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

Abrí **http://127.0.0.1:8765**. El servidor escucha únicamente en `127.0.0.1`; Ctrl+C lo detiene. Si actualizaste el código, reiniciá el servidor y recargá la página. Opciones: `--port 8768`, `--data-dir /ruta/absoluta`.

## Conexiones

En **Conexiones**:

- **ChatGPT:** conectar muestra el inicio de sesión oficial. Una vez conectado, se ve la cuenta actual y un único botón para desconectar. Para cambiar de cuenta, desconectá y conectá otra.
- **Weblate:** pegá tu token y pulsá **Guardar**. El campo vuelve a mostrar una máscara y el estado «Token guardado». No se devuelve el secreto al navegador. Los tokens ingresados desde la interfaz permanecen en la memoria del servidor hasta reiniciar.

Para conservar el token de Weblate entre reinicios, configurá `.env` con tu editor:

```dotenv
WEBLATE_TOKEN=tu_token
```

`.env.example` no contiene secretos. `.env` y `data/` están excluidos de Git. El archivo se lee sin ejecutar shell; las variables de entorno tienen prioridad. No compartas `.env` ni agregues comentarios en la misma línea del token.

La conexión con ChatGPT usa el flujo oficial local: PKCE, state y nonce; verificación de firma, emisor, audiencia, vigencia e identidad del ID token; y permisos para utilizar el plan. La cuenta debe autorizar ese uso y tener acceso al modelo elegido. No se accede a las conversaciones ni se reutilizan credenciales de Codex.

Las credenciales de ChatGPT se guardan en `data/auth/chatgpt.json`, con permisos `0600`, separadas de las traducciones. El archivo no se sirve por HTTP. Desconectar intenta revocar la sesión y elimina los tokens locales; si no se confirma la revocación remota, lo informa. Los registros de conexiones anteriores se conservan para compatibilidad, pero la interfaz trabaja con una cuenta activa.

Fuentes oficiales: [inicio de sesión](https://developers.openai.com/siwc/token-sharing-open-source/sign-in), [modelos e inferencia](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference), [aplicaciones locales y alojadas](https://developers.openai.com/siwc/token-sharing-open-source).

## Traducir y revisar

1. Elegí **Locales** —predeterminado, textos del sitio—, **Payload CMS** —contenido del sitio, JSON— o **Regulations**, y el idioma de destino.
2. Los idiomas y modelos se cargan automáticamente en segundo plano. Mientras se consultan idiomas, pendientes o modelos, se muestra un aviso de carga y se deshabilitan los selectores y las acciones de sincronización y traducción; las decisiones de revisión siguen disponibles. El selector de idioma oculta los conteos anteriores durante la consulta. También se reintenta al entrar al selector si hubo un error. El modelo se puede cambiar; el navegador recuerda la última elección, sin guardar credenciales.
3. Pulsá **Sync & Translate**. Se descargan las referencias vigentes en inglés y el idioma seleccionado, y se buscan claves vacías, plurales incompletos y textos marcados para revisión. Desde la segunda sincronización de ese componente e idioma también se detectan cambios en el inglés por contexto único.
4. ChatGPT prepara las propuestas. Un idioma sin trabajo pendiente no genera solicitudes de traducción. Si faltan lotes, aparece **Continuar traducción**; no hace falta crear otra sesión.
5. Revisá cada entrada. **Aceptar** aprueba lo visible y avanza. Si estás editando o tenés un borrador, el mismo botón guarda **tu edición** y avanza; no restaura la propuesta anterior.
6. Pulsá **Descargar traducción**, revisá las validaciones y descargá el archivo.

Los borradores se guardan tras 500 ms sin escribir, y quedan pendientes hasta aprobarlos. Esperá el indicador de guardado antes de cerrar. Atajos: A aceptar/guardar edición cuando no estás escribiendo, R rechazar, E editar, ←/→ navegar, Ctrl+Enter aprobar desde el editor.

La generación nunca aprueba propuestas automáticamente. Se guardan lotes adaptativos de hasta 32 entradas, con un presupuesto aproximado de 8.000 tokens entre entrada, salida y contexto, y hasta 40 entradas por ejecución; una entrada que exceda el presupuesto se procesa sola, sin truncarla. Se usan IDs temporales cortos, el glosario completo hasta aproximadamente 2.000 tokens; si es mayor, términos relevantes y esenciales de WCA. Se incluyen variantes explícitas en inglés y español, y hasta ocho ejemplos relacionados (máximo 6.000 caracteres); se puede continuar sin repetir los lotes completos. El contador de caracteres corresponde a la respuesta JSON recibida, no al tamaño del pedido. Las respuestas interrumpidas, rechazadas o incompletas no se usan como traducciones válidas.

Para mantener la consistencia, ChatGPT recibe las claves solicitadas, su contexto, glosario y ejemplos relevantes buscados en todo el corpus. Se seleccionan hasta ocho ejemplos relacionados, con un límite de 6.000 caracteres, en vez de reenviar siempre el archivo entero. Se usa Responses API con `store: false`, streaming y salida JSON estructurada. La generación utiliza el acceso y los límites de la cuenta conectada.

## Validaciones en la descarga

El diálogo muestra las entradas con controles pendientes. Allí podés ver el inglés, **corregir la traducción y guardar**, o **Descartar** un falso positivo. También podés restaurar un control descartado. Al pulsar **Cancelar** en el editor, se cierra esa corrección y se descartan sus cambios sin modificar la traducción guardada.

Los descartes se aplican al control y forma concretos, para una huella de la referencia y el texto revisado. No hay un botón para ignorar todos los errores. Si cambia el texto, ese descarte deja de aplicarse. Se registran en las decisiones y en `resumen_exportacion.json`, con fecha y hora.

Se pueden descartar comprobaciones heurísticas de variables, enlaces, etiquetas o terminología. Los conflictos sin resolver, textos vacíos, cantidades de plurales incorrectas, caracteres inválidos y errores estructurales requieren una corrección. Estos controles no garantizan calidad lingüística.

Si volvés desde el diálogo sin aprobar una corrección que escribiste ahí, se conserva como borrador pendiente. Los pendientes se omiten de la descarga y requieren confirmación. Las advertencias restantes también requieren confirmación.

## Un archivo para importar en Weblate

Hay una única descarga normal, con los cambios aceptados o editados y aprobados:

- **Locales:** `IDIOMA_reviewed.yml`, Ruby YAML con las claves modificadas y sus formas plurales. Si una traducción está en una lista, se conserva la lista necesaria para mantener sus índices.
- **Payload CMS:** `IDIOMA_reviewed.json`, JSON con las claves aprobadas y su campo `message`; conserva los metadatos de cada entrada. Los PO de la sesión son referencias internas.
- **Regulations:** `IDIOMA_reviewed.po`, PO con encabezado y entradas aprobadas.

El archivo se importa para **fusionar traducciones**, no para reemplazar el archivo completo del repositorio. En Weblate elegí **Files → Upload → Add as translation**, con la gestión de conflictos que corresponda. Para Locales, si preferís otra revisión allí, el mismo archivo sirve con **Add as suggestion**. Regulations no tiene habilitadas las sugerencias en este proyecto. La modalidad se elige al importar, sin descargar dos archivos distintos. Referencia: [documentación de importación de Weblate](https://docs.weblate.org/en/weblate-2026.8/user/files.html).

Las traducciones rechazadas o pendientes no se envían. No hay descarga si no existen cambios aprobados. Las bases originales y las sesiones anteriores se conservan. El estado «sin pendientes» corresponde al componente y a la sesión indicados, cuya fecha de descarga se muestra en el panel. El resumen global de Weblate puede incluir otros componentes, incluso privados; la herramienta avisa si detecta pendientes fuera de Locales, Regulations y Payload CMS. El backend mantiene los modos antiguos de exportación para compatibilidad; no se muestran en la interfaz.

Si hubo trabajo concurrente, cargá una descarga actual desde **Comparar con una descarga reciente**. Se detectan conflictos y esa descarga se usa como base. La aplicación no vuelve a consultar Weblate al exportar.

## Probar es_419 cuando está al 100 %

```bash
.venv/bin/python run.py --simulate
```

Abrí **http://127.0.0.1:8767**. Se descarga la traducción actual y se vacían tres entradas únicamente en una copia local. El resto sirve como referencia de consistencia. Usá ChatGPT, revisá y descargá para probar el flujo completo.

- Se guarda en `data/simulation/`, separado de las sesiones reales.
- Se conserva la descarga original. El contexto de revisión muestra la respuesta anterior para comparar; esa referencia no se envía a la generación automática.
- Las exportaciones llevan `SIMULATION_`. **No se suben a Weblate.**
- La conexión de ChatGPT sí se usa si pulsás traducir; sus límites siguen aplicando.

También podés probar otro idioma con pendientes, sin subir el resultado.

Para una demo sin red ni credenciales:

```bash
.venv/bin/python run.py --demo
```

Abrí **http://127.0.0.1:8766** y pulsá **Sync & Translate**. Las propuestas ficticias se cargan automáticamente. Se guarda en `data/demo/` y los archivos llevan `DEMO_`; no se suben. No se permite mezclar datos reales, simulados y demo en la misma carpeta.

## Más opciones

Las sesiones se guardan por componente e idioma y se recuperan al abrir la aplicación. **Más opciones** permite retomar una revisión anterior.

El modo **Usar un agente externo · modo local** conserva el flujo con un chat en este proyecto: sincronizá, copiá el prompt, pedile al agente que escriba `proposals.json`, y volvé para **Cargar propuestas**. El agente no aprueba decisiones ni modifica las bases.

Contrato:

```json
{"version":1,"session_id":"ID","base_sha256":"HASH","entries":[{"key":"CLAVE_EXACTA","target":["Una cadena por forma"],"note":"Motivo o duda"}]}
```

El prompt incluye el comando para validar esa sesión:

```bash
.venv/bin/python run.py validate-proposals ID --data-dir /ruta/a/data
# Agregar --demo o --simulate según el modo.
```

## Archivos, idiomas y acceso

Las sesiones viven en `data/sessions/<fechaUTC>-<id>/`, con bases inmutables y hashes, mapeos YAML, glosario si está disponible, `work.json`, `PROMPT.txt`, progreso de generación, propuestas y revisión. Las decisiones y las exportaciones se guardan en `review/`. Respaldá `data/` protegiendo las credenciales.

La sincronización muestra etapas reales, unidades descargadas, reintentos y tiempo transcurrido. La barra representa etapas, no bytes ni tiempo estimado. Las horas visibles usan formato de **24 h** y la zona horaria del navegador.

Se respetan los códigos y alias de idioma. Para Locales se contrastan Babel/CLDR, el YAML nativo y la API para identificar las formas `zero`, `one`, `two`, `few`, `many`, `other`. Si el esquema es ambiguo, se detiene la importación. Los textos y editores permiten dirección automática para idiomas RTL.

Weblate solo recibe solicitudes GET. El token conserva los permisos de su cuenta y nunca se envía a ChatGPT. Si el archivo no admite descarga pero sí las unidades, Locales puede reconstruir una base con las claves localizables y lo advierte; no contiene comentarios ni otros datos del original. Para Regulations, el inglés se puede reconstruir desde unidades; el PO destino se descarga nativo.

401/403 requieren revisar acceso; 429 requiere esperar. Los reintentos son limitados y respetan `Retry-After`. Si cambia una revisión remota durante la descarga, se repite automáticamente la descarga completa una vez. Si también cambia en el segundo intento, se informa el fallo y se conserva la sesión anterior. El aviso de error se borra al iniciar un nuevo intento. El servidor verifica Host y origen, admite la navegación de regreso OAuth y mantiene bloqueadas las solicitudes externas a la API.

## Subir a GitHub

El código, las pruebas y la configuración de CI se versionan. `.env`, `data/`, el entorno virtual, la configuración del editor y las exportaciones de simulación quedan excluidos. Las exportaciones de las pruebas manuales anteriores se conservaron en `data/manual-exports/`.

Creá un repositorio vacío en GitHub, por ejemplo `wca-translation-studio`, sin generar README, licencia ni `.gitignore`. Con el commit inicial local ya preparado, ejecutá desde esta carpeta, reemplazando `TU_USUARIO`:

```bash
git remote add origin https://github.com/TU_USUARIO/wca-translation-studio.git
git push -u origin main
```

GitHub Actions ejecuta las pruebas de Python en cada push y pull request, sin claves ni llamadas de IA. Las pruebas de navegador se ejecutan localmente con el comando de la sección siguiente.

## Pasar a una web alojada

**Esta versión funciona en localhost y todavía no está preparada para alojarse como servicio público.** Escucha en `127.0.0.1`, exige un origen local, usa un callback OAuth loopback y comparte un perfil y carpeta de trabajo. Subir este repositorio a un proveedor no cambia esas condiciones.

Recomendación para la siguiente versión: **Render**, con un servicio web Python y almacenamiento persistente. Su [servicio web](https://render.com/docs/web-services) admite procesos de servidor y publica HTTPS; los [discos persistentes](https://render.com/docs/disks) requieren un servicio de pago. Sin almacenamiento persistente, se pierden sesiones y credenciales al reiniciar o desplegar. Vercel necesitaría adaptar el servidor a funciones y mover los datos a almacenamiento externo: su [sistema de archivos de funciones](https://vercel.com/docs/functions/runtimes) es de solo lectura, salvo el espacio temporal de `/tmp`.

Antes del primer deploy:

1. **Habilitar ChatGPT para una aplicación alojada.** El flujo actual corresponde a aplicaciones locales. OpenAI indica que ofrecer uso del plan en una aplicación alojada requiere [solicitar acceso mediante su formulario](https://developers.openai.com/siwc/token-sharing-open-source). El inicio de sesión por sí solo no garantiza permiso para generar traducciones. La versión web debe usar un cliente registrado y una URL de callback HTTPS autorizada.
2. **Adaptar el servidor.** Agregar un punto de entrada de producción que escuche en `0.0.0.0` y el puerto `PORT` del proveedor, configurar el origen público y el callback HTTPS, y separar sesiones, credenciales, trabajos y archivos por usuario. Conservar la revisión humana y la exportación manual. El servidor local actual no proporciona ese punto de entrada.
3. **Crear el servicio en Render.** Vincular el repositorio de GitHub → **New → Web Service → Python**. El build instala `pip install -r requirements.txt`. El comando de inicio se define con el nuevo punto de entrada del paso anterior; `python run.py` todavía no sirve como comando público. Montar un disco persistente para los datos y configurar allí la carpeta de trabajo. Guardar secretos en la configuración privada del servicio, nunca en GitHub ni en la imagen.
4. **Probar con dos cuentas distintas.** Confirmar que ninguna pueda ver sesiones, credenciales ni descargas de la otra, que los trabajos sobrevivan a las condiciones previstas de despliegue y que desconectar revoque la conexión correspondiente.

WCA OAuth, si se agrega, requiere una aplicación registrada y no sustituye los permisos de Weblate: [documentación de WCA](https://docs.worldcubeassociation.org/knowledge_base/v0_api.html). El repositorio no publica ni despliega automáticamente.

## Pruebas

```bash
.venv/bin/python -m unittest discover -s tests -v
RUN_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

Las pruebas usan proveedores y cuentas ficticias, JWT de prueba, localhost y carpetas temporales. No consumen IA. Las de navegador requieren Chrome/Chromium y Node >=22.

Las variantes gramaticales explícitas del glosario están en `app/glossary.py`. Se usan tanto al preparar el contexto como al validar términos preferidos y prohibidos. No se deducen traducciones ni variantes para otros idiomas. Las preferencias se obtienen del glosario de Weblate; la revisión humana sigue siendo necesaria.
