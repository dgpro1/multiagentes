# API pública

> Read in English: [api.md](../en/api.md)

Los terceros construyen sobre los mismos recursos que el panel, bajo un prefijo versionado con contrato estable: **`/api/v1`**. El esquema interactivo vive en `/docs` del backend (puerto 8000 en local). Qué está cubierto, y qué puerta responde, se sigue en [api-coverage.md](../api-coverage.md): una pantalla del panel sin ruta API es un bug.

## Autenticación

Dos puertas, una agencia. Una sesión de cookie (persona firmada) tiene todos los scopes; una credencial API queda sujeta a su agencia, sus scopes y — si está confinada — su cliente.

**Tokens de larga vida.** Una *integración* (Ajustes → integraciones API, o la pestaña API del cliente) es una conexión: nombre, scopes y opcionalmente un solo cliente. Emitir un token devuelve el secreto **una sola vez** (`ol_…`, no vuelve a mostrarse; solo se guardan prefijo y digest SHA-256). Envíalo como `Authorization: Bearer ol_…` o `X-API-Key: ol_…`. Vencen entre 1 día y 5 años y son revocables; un token revocado o vencido responde `401`.

**OAuth 2.0.** Una integración también es un cliente OAuth: la agencia registra sus URLs de redirección (https; http solo para loopback) y toma un secreto de cliente, una persona aprueba scopes en `/oauth/authorize`, y el tercero intercambia el código de un solo uso (10 minutos) por un access token de 1 hora más un refresh token rotativo de 30 días:

| Paso | Ruta |
| --- | --- |
| Consultar el cliente | `GET /api/oauth/client?client_id=` |
| Aprobar o denegar (persona firmada) | `POST /api/oauth/authorize` |
| Intercambiar código, refrescar | `POST /api/oauth/token` |
| Revocar | `POST /api/oauth/revoke` |

Un subconjunto otorgado más estrecho que la integración viaja con el grant. Los códigos nunca sirven como bearers.

**Confinamiento.** Un token limitado a un cliente solo alcanza rutas que nombran ese cliente; lo demás responde `404`. Una ruta se abre a tokens solo declarando un scope — lo que no declara nada sigue cerrado — y una ruta guardada solo por scopes `.read` siempre es `GET`, así que una credencial de solo lectura es exactamente eso.

## Scopes

| Scope | Qué abre |
| --- | --- |
| `clients.read` | Ver los clientes y su configuración. |
| `clients.write` | Crear y editar clientes. |
| `agents.read` | Ver los agentes y su configuración. |
| `agents.write` | Crear, editar y eliminar agentes. |
| `agents.knowledge` | Leer y cambiar la base de conocimiento de un agente. |
| `agents.tools` | Herramientas y MCP de un agente (hoy solo panel). |
| `channels.read` | Ver los canales y si están conectados. |
| `channels.manage` | Conectar, configurar y quitar canales (hoy solo panel). |
| `inbox.read` | Leer conversaciones y sus mensajes. |
| `inbox.reply` | Responder como persona. |
| `inbox.manage` | Tomar el control de la IA y devolverlo, resolver y reabrir. |
| `contacts.read` | Ver los contactos. |
| `contacts.manage` | Crear y editar contactos. |
| `tags.read` | Ver las etiquetas. |
| `tags.manage` | Crear, renombrar, recolorear y eliminar etiquetas. |
| `teams.read`, `teams.manage` | Ver / gestionar equipos (hoy solo panel). |
| `templates.read`, `templates.manage` | Plantillas de WhatsApp (hoy solo panel). |
| `canned.manage` | Respuestas guardadas (hoy solo panel). |
| `pipeline.read` | Ver etapas y tablero. |
| `pipeline.manage` | Gestionar etapas, mover tratos, crear leads. |
| `calendar.read` | Ver el calendario de un cliente. |
| `calendar.manage` | Añadir y quitar miembros del calendario. |
| `reports.read` | Ver los reportes. |
| `integrations.manage` | Crear y revocar credenciales API (API del panel, sin versionar). |
| `webhooks.manage` | Suscripciones de webhooks (API del panel, sin versionar). |

Presets: `read_only` (todo `.read`), `operator` (lecturas más respuesta, gestión e inbox), `full` (todo). `GET /api/integrations/scopes` lista el catálogo con presets.

## Convenciones

**Envelope.** Todo recurso trae `_links.self`. Los errores visten `{title, type, status, detail}` con `type` apuntando a `/api/v1/docs/errors#<slug>`; los fallos de validación añaden `validation-errors: [{field, message}]`:

```json
{"title": "Not found", "type": "/api/v1/docs/errors#not-found", "status": 404, "detail": "Client not found"}
```

Slugs conocidos: `bad-request`, `unauthorized`, `forbidden`, `not-found`, `conflict`, `validation-failed`, `rate-limited`. Sin credencial el envelope igual responde (`401`, `"You are not signed in"`); un token sin el scope responde `403` nombrándolo (`"This API token does not hold: contacts.manage"`).

**Paginación.** Las listas toman `page` (desde 1) y `limit`, con tope de **250** elementos. Responden `data`, `page`, `limit`, `total` y `_links` con `self`/`prev`/`next`. Las fechas son ISO-8601 con offset.

## Recursos

Cada ruta vive bajo `/api/v1` y nombra su cliente, salvo la colección de clientes que es de la agencia.

**Clientes** (`clients.read`, escrituras `clients.write`)

| Método y ruta | Qué hace |
| --- | --- |
| `GET /clients` | Lista los clientes de la agencia, paginado. |
| `POST /clients` | Crea un cliente (idempotente, ver abajo). |
| `GET /clients/{id}` | Un cliente. |
| `PATCH /clients/{id}` | Edita nombre, industria, zona horaria y demás. Cambiar la industria suelta un tipo de negocio que ya no pertenece. |

**Contactos** (`contacts.read`, escrituras `contacts.manage`)

| Método y ruta | Qué hace |
| --- | --- |
| `GET /clients/{id}/contacts?search=` | Busca por nombre o teléfono, paginado. |
| `POST /clients/{id}/contacts` | Crea (teléfono con código de país; duplicado es `409`; idempotente). |
| `GET /clients/{id}/contacts/{contact_id}` | Un contacto. |
| `PATCH /clients/{id}/contacts/{contact_id}` | Edita nombre, teléfono, email, notas. |

**Conversaciones** (`inbox.read`, responder `inbox.reply`, control `inbox.manage`)

| Método y ruta | Qué hace |
| --- | --- |
| `GET /clients/{id}/conversations?status=` | `open` o `resolved`, paginado. |
| `GET /clients/{id}/conversations/{conversation_id}` | Una conversación. |
| `GET /clients/{id}/conversations/{conversation_id}/messages` | El hilo de más viejo a más nuevo, paginado. `kind` distingue un `message` de una nota `activity`. |
| `POST /clients/{id}/conversations/{conversation_id}/reply` | Responde como operador (`{"content"}`). El caso debe estar en manos humanas (`PATCH …/mode` antes) y abierto; las líneas sociales encolan por el outbox durable. Idempotente. |
| `PATCH /clients/{id}/conversations/{conversation_id}/mode` | `{"mode": "human"}` toma el control, `{"mode": "ai"}` lo devuelve, con la traza del panel en el hilo. |
| `PATCH /clients/{id}/conversations/{conversation_id}/status` | `{"status": "resolved"}` resuelve, `"open"` reabre. |

Cada conversación (un *lead* en el panel) trae `number`, su número corto dentro del cliente (#1, #2, #3…, nunca se reutiliza y es único por cliente), y `_links.html`, la dirección de su pantalla en el panel (`/clients/{id}/inbox/{number}`). El número es el que usan las URL de las pantallas; el UUID sigue siendo el identificador de las rutas de la API. Los webhooks `conversation.resolved`, `deal.moved` y `message.received` también llevan `number`.

**Pipeline** (`pipeline.read`, escrituras `pipeline.manage`)

| Método y ruta | Qué hace |
| --- | --- |
| `GET /clients/{id}/pipeline/board` | Etapas, tarjetas y conteo sin asignar. |
| `GET /clients/{id}/pipeline/stages` | Las etapas. |
| `POST /clients/{id}/pipeline/stages` | Crea una etapa (idempotente). |
| `PATCH /clients/{id}/pipeline/stages/{stage_id}` | Renombra o recolorea. |
| `POST /clients/{id}/pipeline/stages/reorder` | `{"stage_ids": [...]}` en el nuevo orden. |
| `DELETE /clients/{id}/pipeline/stages/{stage_id}` | Elimina una etapa. |
| `PATCH /clients/{id}/conversations/{conversation_id}/pipeline` | Mueve un trato: `{"pipeline_stage_id", "deal_value"}`. |
| `POST /clients/{id}/pipeline/leads` | Un lead rápido: contacto más un caso abierto en manos humanas en una etapa. Nada se envía. Idempotente. |

**Etiquetas** (`tags.read`, escrituras `tags.manage`)

| Método y ruta | Qué hace |
| --- | --- |
| `GET /clients/{id}/tags` | El catálogo con conteos de contactos. |
| `POST /clients/{id}/tags` | Crea (idempotente; nombre duplicado es `409`). |
| `PATCH /clients/{id}/tags/{tag_id}` | Renombra o recolorea. Rutear una etiqueta a un equipo o persona sigue siendo gesto del panel. |
| `DELETE /clients/{id}/tags/{tag_id}` | Elimina; los enlaces van con ella. |

**Agentes** (`agents.read`, escrituras `agents.write`)

| Método y ruta | Qué hace |
| --- | --- |
| `GET /clients/{id}/agents` | Los agentes del cliente, paginado. |
| `POST /clients/{id}/agents` | Crea (el `client_id` del body debe coincidir con el path; idempotente). |
| `GET /clients/{id}/agents/{agent_id}` | Un agente con su configuración. |
| `PATCH /clients/{id}/agents/{agent_id}` | Edita; los agentes no cambian de cliente por esta API (`409`). |
| `DELETE /clients/{id}/agents/{agent_id}` | Borra configuración y conocimiento, conserva conversaciones. `409` mientras responda un canal. |
| `GET /clients/{id}/agents/{agent_id}/prompt` | Lo que el modelo recibe en cada mensaje, menos el conocimiento por mensaje. |

**Conocimiento** (listar `agents.read`, escrituras `agents.knowledge`)

| Método y ruta | Qué hace |
| --- | --- |
| `GET /clients/{id}/agents/{agent_id}/documents` | PDFs subidos con estado, tamaño y estado de índice. |
| `POST /clients/{id}/agents/{agent_id}/documents` | `file` multipart (solo PDF, 20 MB). Indexado best-effort, nunca una subida fallida. |
| `DELETE /clients/{id}/agents/{agent_id}/documents/{document_id}` | Elimina con sus chunks. |
| `POST /clients/{id}/agents/{agent_id}/documents/reindex` | Reincrusta todo con el modelo actual del agente (`502` sin clave útil). |
| `GET /clients/{id}/agents/{agent_id}/qa` | Pares de preguntas y respuestas. |
| `POST /clients/{id}/agents/{agent_id}/qa` | Añade un par (idempotente). |
| `DELETE /clients/{id}/agents/{agent_id}/qa/{qa_id}` | Elimina un par. |

**Canales** (`channels.read`)

| Método y ruta | Qué hace |
| --- | --- |
| `GET /clients/{id}/channels` | Todas las líneas en un lugar — números WhatsApp QR y API, cuentas Instagram/Messenger, chat web — normalizadas a `type/label/status/connected`. Solo lectura: conectar una línea sigue siendo gesto del panel. |

**Calendario** (leer `calendar.read`, miembros `calendar.manage`; los eventos viven en Google, se leen en vivo)

| Método y ruta | Qué hace |
| --- | --- |
| `GET /clients/{id}/calendar` | Estado de conexión y miembros con enlaces frescos. |
| `GET /clients/{id}/calendar/events?start=&end=` | Eventos del rango (máximo 62 días), con `errors` por miembro. |
| `POST /clients/{id}/calendar/members` | Añade un miembro (idempotente). |
| `DELETE /clients/{id}/calendar/members/{member_id}` | Quita un miembro. |

**Reportes** (`reports.read`, todo acotado a los números del cliente)

| Método y ruta | Qué hace |
| --- | --- |
| `GET /clients/{id}/reports/costs?from=&to=` | Totales más por agente, modelo y día. `agent_id` opcional (debe ser del cliente), `model`, `tz`. |
| `GET /clients/{id}/reports/replies?from=&to=` | Una línea por respuesta, de más nueva a más vieja, paginado. La exportación CSV queda en el panel. |
| `GET /clients/{id}/reports/operations?from=&to=` | Totales de atención, tiempos y volumen. |

## Gestión de credenciales (API del panel, sin versionar)

Integraciones, tokens, clientes OAuth y suscripciones viven en la API del panel bajo `/api/integrations` — misma cookie o un token con `integrations.manage` / `webhooks.manage` — porque las credenciales las gestionan personas, no integraciones:

| Método y ruta | Qué hace |
| --- | --- |
| `GET /api/integrations/scopes` | El catálogo de scopes con presets. |
| `GET /api/integrations`, `POST /api/integrations` | Lista / crea (nombre, preset o scopes, cliente opcional). |
| `PATCH /api/integrations/{id}`, `DELETE /api/integrations/{id}` | Edita / elimina (los tokens mueren con ella). |
| `GET /api/integrations/{id}/tokens`, `POST /api/integrations/{id}/tokens` | Lista / emite (`expires_in_days` 1–1825, secreto una vez). |
| `DELETE /api/integrations/{id}/tokens/{token_id}` | Revoca. |
| `POST /api/integrations/{id}/oauth-client` | Registra/rota el cliente OAuth (secreto una vez). |
| `GET /api/integrations/{id}/webhooks`, `POST /api/integrations/{id}/webhooks` | Lista / suscribe (`url` https, `events`; secreto una vez). |
| `DELETE /api/integrations/{id}/webhooks/{subscription_id}` | Desuscribe; las entregas pendientes se descartan. |
| `GET /api/integrations/{id}/webhooks/{subscription_id}/deliveries?status_filter=` | El log: `pending`, `sent`, `failed`. |
| `POST /api/integrations/{id}/webhooks/{subscription_id}/deliveries/{delivery_id}/replay` | Reencola desde cero. |

## Webhooks

Una suscripción reenvía `message.received`, `conversation.resolved` y `deal.moved` a tu URL. Cada entrega publica el evento con `id`, `event`, `occurred_at` y `data`, y firma el body crudo con HMAC-SHA256 en `X-Signature`:

```python
hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest() == request.headers["X-Signature"]
```

La entrega es at-least-once y sin orden. Un fallo sube la escalera 5/15/15/60 minutos y luego descansa como `failed` en el log para reintento manual. Eliminar o revocar la integración detiene sus entregas. La UI vive en la fila de cada integración (Ajustes → integraciones API, o la pestaña API del cliente).

## Idempotencia

Las escrituras que crean filas aceptan `Idempotency-Key: <uuid>`: la primera guarda su respuesta 24 horas, un reintento con el mismo body la repite marcada `"api_replay": true` en vez de actuar dos veces, la misma clave con otro body es `422`, y una clave aún en vuelo es `409`. Las claves viven por credencial, así que las integraciones nunca se repiten entre sí.

## Límites

- `page`/`limit` con tope de 250 elementos; los tokens tienen 7 peticiones por segundo (`429` con `retry_after`).
- Subidas PDF: 20 MB; rangos de eventos: 62 días; los reportes se acotan a un año.
- Los códigos OAuth viven 10 minutos, los access tokens 1 hora, los refresh de 30 días rotativos.

## Siguientes pasos

- [api-coverage.md](../api-coverage.md) — cada pantalla del panel y la ruta API detrás, vigilado por un test.
- [Bandeja de entrada](inbox.md) — el lado panel de las conversaciones de arriba.
- [Reportes](reports.md) — el lado panel de costos, respuestas y operaciones.
