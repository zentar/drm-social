# DRM Social

Aplicación web para proteger libros PDF con DRM social, login privado e historial de licencias.

## Qué hace

- Login con usuario y contraseña.
- Creación del usuario inicial.
- Subida de libros PDF.
- Asignación de nombre y email al comprador.
- Configuración de contraseña de apertura del PDF.
- Configuración manual o automática de contraseña de propietario.
- Historial de libros protegidos con descarga posterior.
- Persistencia local con SQLite en la carpeta `data`.

## Ejecutar localmente

Requisitos:

- Python 3.12

Comandos:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Abrir en el navegador:

```text
http://127.0.0.1:8000
```

## Ejecutar con Docker en otra máquina

Requisitos:

- Docker
- Docker Compose

Preparación:

```bash
cp .env.example .env
```

Edita `.env` y cambia al menos:

- `SECRET_KEY`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`

Construcción y arranque:

```bash
docker-compose up --build
```

Abrir en el navegador:

```text
http://127.0.0.1:8100
```

## Persistencia

Los datos quedan en la carpeta `data` del proyecto:

- `data/app.db`: base de datos SQLite.
- `data/uploads`: PDFs originales cargados.
- `data/protected`: PDFs protegidos generados.

## Despliegue en otra máquina

La forma más simple es copiar todo el proyecto completo:

```bash
scp -r drm-social usuario@servidor:/ruta/destino/
```

Luego, en la otra máquina:

```bash
cd drm-social
cp .env.example .env
docker-compose up --build -d
```

## Notas

- Si la carpeta `data` ya existe en la otra máquina, conservará usuarios, historial y archivos protegidos.
- Si quieres empezar desde cero, elimina la carpeta `data` antes de arrancar.