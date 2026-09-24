# Despliegue con Coolify en un servidor Hetzner

> Read in English: [deploy-coolify.md](../en/deploy-coolify.md)

Coolify ejecuta el stack desde `docker-compose.coolify.yml`. Su proxy termina el TLS y llega a la app a través del servicio `proxy`; nada publica un puerto del servidor, los secretos vienen de las variables de entorno de Coolify y un despliegue al que le falta un secreto se detiene antes de arrancar.

## Antes de empezar

- **Servidor:** al menos 4 GB de RAM y 2 vCPU (por ejemplo un Hetzner CX22/CPX21). El compose pone límites de memoria (base de datos 768 MB, API 1 GB, web 512 MB, Evolution 768 MB y pequeños para el resto); súbelos con `DB_MEMORY`, `API_MEMORY`, `WEB_MEMORY` y `EVOLUTION_MEMORY` en un servidor mayor.
- **Dominio:** un registro `A` (`app.ejemplo.com`) que apunte a la IP del servidor. Los portales de clientes con dominio propio son opcionales y necesitan el mismo registro más `docs/es/client-portal.md`.
- **Imágenes:** GitHub Actions ("Publish images", `.github/workflows/publish-images.yml`) construye `ghcr.io/<dueño>/openlivery-api` y `-web` en cada push a `main`, así el servidor nunca compila. En la pestaña **Actions** del repositorio comprueba que la ejecución está en verde y que existen los dos paquetes. Si los paquetes son privados, hazlos públicos o ejecuta una vez en el servidor `docker login ghcr.io -u <usuario> -p <token con read:packages>`.
- Si tus imágenes están bajo otro dueño, define `OPENLIVERY_IMAGE_PREFIX` (por defecto `ghcr.io/dgpro1/openlivery`).

## Pasos

1. **Genera el entorno** en tu computador y compruébalo:
   ```bash
   ./scripts/generate-coolify-env.sh app.ejemplo.com > coolify.env
   python scripts/check-production-env.py coolify.env
   ```
   Guarda `ENCRYPTION_KEY` en un gestor de contraseñas: descifra las claves de IA y las sesiones de WhatsApp guardadas y no debe cambiar nunca. Borra `coolify.env` después de pegarlo.
2. **Crea el recurso** en Coolify: *New resource → Docker Compose → tu repositorio Git*, rama **`production`**, archivo `/docker-compose.coolify.yml`. (`production` la crea y la avanza GitHub solo cuando las pruebas están en verde; mira *Actualizar*.) (Usa el repositorio y no "compose vacío": el gateway lee `docker/Caddyfile` de él.)
3. **Variables de entorno:** abre *Environment variables → Developer view* y pega el bloque. `FRONTEND_URL` debe ser la dirección pública con `https`.
4. **Dominio:** en el servicio `proxy` pon `https://app.ejemplo.com` (puerto 80 por dentro). Coolify emite el certificado. Deja los demás servicios sin dominio.
5. **Despliega.** Espera a que todos los servicios estén sanos. La API aplica las migraciones de la base de datos al arrancar.
6. **Primer uso:** abre el dominio; la configuración inicial crea la agencia y su dueño, y después el registro público se cierra para siempre.
7. **Servicios opcionales:**
   - *Google Calendar:* registra `https://app.ejemplo.com/api/calendar/oauth/callback` como URI de redirección autorizada en Google Cloud y llena `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
   - *Canales de mensajería (WhatsApp API, Instagram, Messenger):* llena `MESSAGING_PROVIDER_API_KEY`; la dirección del webhook se arma desde `FRONTEND_URL`, así que debe ser pública.

## Copias de seguridad

El servicio `db-backup` guarda cada día un volcado comprimido de la base de datos principal en el volumen `db_backups` (7 diarios, 4 semanales, 6 mensuales). Una copia en el mismo servidor no es una copia de seguridad: activa las copias o snapshots de Hetzner y saca ese volumen del servidor. Para restaurar:
```bash
docker exec -i <contenedor db> psql -U openlivery openlivery < volcado.sql   # tras descomprimir
```
Los archivos subidos (`backend_storage`) y las sesiones de WhatsApp (volúmenes `evolution_*`) también conviene incluirlos en los snapshots del servidor.

## Actualizar

El trabajo aterriza en `main`, pero el servidor nunca lo ve directamente. En cada push a `main`, GitHub ejecuta *Tests*; solo si salen en verde, *Publish images* construye las imágenes (con las etiquetas `latest` y `sha-<7 caracteres>`) y después avanza la rama **`production`** a ese commit. Coolify sigue `production`, así que con *Auto Deploy* activado solo recibe commits que pasaron las pruebas y ya tienen sus imágenes. Una ejecución en rojo no publica nada y `production` se queda donde estaba. Comprueba un commit con `python scripts/ci-status.py <sha>`.

Las migraciones corren al arrancar. Antes de un despliegue que agregue una (el changelog lo indica), toma un snapshot de Hetzner o confirma que existe el volcado de anoche de `db-backup`.

## Volver atrás

Si un despliegue falla, vuelves atrás en minutos: en Coolify pon `OPENLIVERY_VERSION=sha-<primeros 7 caracteres de un commit bueno>` (las imágenes de cada commit en verde se conservan en el registro) y vuelve a desplegar. Si la versión mala aplicó una migración, restaura el snapshot o el volcado tomado antes. Cuando haya un commit corregido en `production`, vuelve la variable a `latest` (o bórrala).

## No cambiar

`ENCRYPTION_KEY` (los secretos guardados quedarían ilegibles) y `POSTGRES_PASSWORD` después del primer despliegue (la base conserva la primera; cámbiala antes dentro de Postgres). Cambiar `SECRET_KEY` solo cierra la sesión de todos.

## Si algo falla

- **502 en el dominio:** el servicio `proxy` debe ser el que tiene el dominio, en el puerto 80; comprueba que `web` y `api` aparezcan sanos.
- **Un despliegue se detiene con "Set ...":** una variable obligatoria está vacía; el mensaje la nombra.
- **Inicias sesión pero se pierden las cookies:** `COOKIE_SECURE=true` necesita que el sitio se sirva por `https`; revisa el certificado del dominio.
- **El QR nunca aparece:** los contenedores de Evolution deben estar sanos; su base de datos y su Redis son internos y exclusivos.
