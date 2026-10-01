# Backend de la agencia

> Read in English: [agency-backend.md](../en/agency-backend.md)

Por defecto los datos de un cliente viven en la base central de la instalación y sus archivos en un bucket que el cliente conecta. Con el **backend de la agencia**, una agencia puede encargarse de ambas cosas para todos sus clientes, en su propio proyecto de Supabase y su propio bucket de Cloudflare R2.

El dueño de la plataforma lo activa por agencia (módulo `agency_backend`, apagado por defecto, en la pestaña Plan de la agencia). Sin eso la agencia no ve nada de esto.

## Conectar una vez

En **Configuración → Backend de la agencia**:

- **Supabase.** Autoriza tu proyecto en la pantalla de consentimiento de Supabase y elígelo de la lista. HunterAI nunca necesita la contraseña del proyecto: crea un rol de base de datos por cliente a través de la API de gestión. Usa un plan que no pause el proyecto por inactividad y una región cercana a los servidores de HunterAI (Configuración avisa si no lo está).
- **Cloudflare R2.** Escribe el ID de cuenta, un bucket y un token de la API S3. El bucket se prueba con una escritura, una lectura y un borrado antes de guardar nada, y la clave secreta no se vuelve a mostrar.

## Elegir dónde vive un cliente

En la pestaña **Base de datos** del cliente hay tres lugares para sus datos:

| Lugar | Qué es |
|---|---|
| **HunterAI** | La base central de la instalación. Donde empieza todo cliente. |
| **Tu agencia** | Un esquema de tu proyecto de Supabase, con un rol de base de datos que solo puede usar ese esquema, así que la conexión de un cliente nunca puede leer las filas de otro. |
| **Propio del cliente** | El proyecto de Supabase del cliente, conectado con su consentimiento. |

Mover copia todas las tablas al destino y compara el número de filas tabla por tabla antes de cambiar nada. Los canales del cliente se pausan unos segundos; lo que llega mientras tanto se guarda y se procesa justo después. Un fallo deja al cliente exactamente donde estaba. Mover entre dos lugares que no son HunterAI pasa por él, en una sola petición.

Cuando un cliente sale de tu proyecto, en su esquema queda una **copia de seguridad** de sus datos (la pestaña dice desde cuándo) hasta que la borres.

## Archivos

Los archivos de un cliente pueden estar en su propio bucket o en el de la agencia, en una carpeta propia (toda clave de objeto ya empieza con los ids de la agencia y del cliente). Moverlos copia cada objeto, lo comprueba por tamaño y solo entonces cambia. Los originales se quedan en el bucket de donde salieron. Entrar al bucket de la agencia necesita el módulo; sacar los archivos nunca.

Los archivos acompañan a los datos hacia y desde el bucket de la agencia cuando eliges un lugar, y la pestaña tiene un control aparte para ellos por si ese segundo paso necesita un reintento.

## Apagar el módulo

Nadie se queda sin acceso. Los clientes que ya están en el proyecto o el bucket de la agencia siguen funcionando y se pueden sacar; solo se rechazan las mudanzas nuevas hacia adentro. El panel de la plataforma lista quién está dentro antes de apagarlo.

## Para el dueño de la plataforma

En **Infraestructura** la plataforma ve dónde viven los datos y los archivos de cada cliente y puede moverlos ella misma. Cada movimiento queda en el registro de auditoría con de dónde salió, a dónde fue y cuántas filas o archivos.
