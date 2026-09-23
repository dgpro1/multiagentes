# WhatsApp

> Read in English: [whatsapp.md](../en/whatsapp.md)

OpenLivery conecta un número real de WhatsApp a cada cliente para que su agente responda las conversaciones automáticamente. Cada cliente tiene su propia sesión, vinculada al escanear un código QR desde la app móvil de WhatsApp, sin necesidad de una cuenta de la WhatsApp Business API.

## El driver de Evolution API

Las líneas de WhatsApp QR corren a través de una instancia propia de [Evolution API](https://docs.evolutionfoundation.com.br) (Node, construida sobre [Baileys](https://github.com/WhiskeySockets/Baileys)), levantada por Docker Compose junto al resto de la app. OpenLivery crea una instancia determinista de Evolution por línea (llamada `openlivery-{channel_id}`), cada una con su propio webhook de eventos, de modo que el emparejamiento, las reconexiones, los mensajes entrantes y los mensajes reflejados del teléfono fluyen por el mismo pipeline.

Evolution guarda las sesiones en su propia base de datos (su propio PostgreSQL y Redis, levantados junto a la app; no expuestos fuera de la red de Docker). El backend guarda un pequeño marcador cifrado por canal. WhatsApp QR necesita `EVOLUTION_API_URL` y `EVOLUTION_API_KEY` configurados — consulta [Configuración](configuration.md); sin ellos, WhatsApp QR simplemente no está disponible (los demás canales no se ven afectados).

## Conectar un número

Una sesión de WhatsApp pertenece a un solo cliente, y un cliente puede vincular varios números, cada uno atendido por el agente que elijas (el mismo agente puede atender más de uno). Para conectar uno:

1. Abre un **cliente**, ve a su canal de **WhatsApp** y elige el agente que responderá los mensajes entrantes.
2. Pulsa conectar. El backend crea (o reutiliza) la instancia de Evolution de la línea y pide un **código QR**.
3. En el teléfono dueño del número, abre WhatsApp y ve a **Ajustes → Dispositivos vinculados → Vincular un dispositivo**.
4. Escanea el código QR. Cuando el teléfono lo confirme, el canal cambia a **conectado** y muestra el número vinculado.

A partir de ahí la sesión sobrevive a los reinicios. Si el número se desvincula desde el teléfono (o la sesión se invalida), el canal vuelve a desconectado. También puedes desconectar desde la misma página, lo que cierra la sesión del dispositivo (la instancia de Evolution solo se elimina al borrar la línea).

## Cómo fluyen los mensajes

Cuando un contacto escribe al número, Evolution envía el evento a `POST /api/public/whatsapp/evolution/webhook`, que:

1. Registra el mensaje en la conversación del cliente, recupera el conocimiento del agente y genera una respuesta con el agente asignado.
2. Envía la respuesta de vuelta al contacto en WhatsApp a través de la misma instancia de Evolution.

Las imágenes, notas de voz, documentos y stickers también se reenvían; cuando el agente tiene habilitada la comprensión de imagen o audio, las imágenes y el audio se describen o transcriben antes de llegar al modelo. Consulta [Base de conocimiento](knowledge-base.md). El panel de cada línea también tiene interruptores por línea para **grupos** (cada grupo se convierte en su propia conversación; el agente responde solo si lo mencionan o responden a uno de sus mensajes) y **llamadas** (siempre se rechazan, con un mensaje de explicación opcional). La ubicación funciona en ambos sentidos: un pin entrante se muestra como una tarjeta de mapa, y un operador puede enviar uno desde el compositor de la bandeja de entrada.

## Intervención humana

Cada conversación tiene un `mode`, que es `ai` (el valor por defecto) o `human`. Cuando cambias una conversación al modo human, la IA deja de responderla —el backend sigue registrando los mensajes entrantes, pero no genera ninguna respuesta automática— para que una persona pueda tomar el control y responder directamente desde la [bandeja de entrada](inbox.md) o el [portal del cliente](client-portal.md). Vuelve a `ai` para devolver la conversación al agente. Responder desde el teléfono vinculado también se refleja en la conversación de la misma forma.

## Otros canales

Consulta [WhatsApp Cloud API](whatsapp-cloud-api.md) para la alternativa con la API oficial de Meta, y el [widget web](web-widget.md) para un canal que no necesita ningún número de teléfono. Instagram y Facebook Messenger están en la hoja de ruta.

Siguiente: gestiona las conversaciones en vivo en la [bandeja de entrada](inbox.md) o deja que los clientes gestionen las suyas en el [portal del cliente](client-portal.md).
