Markdown
# Scraper de Perfiles Públicos de Instagram

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)
![Playwright](https://img.shields.io/badge/Playwright-1.40%2B-green)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-orange)

Herramienta modular de extracción y automatización web desarrollada en Python con **Playwright** e intercepción de red para la asignatura de **Arquitectura de Software**. Permite extraer metadatos completos de un perfil público de Instagram y sus publicaciones recientes sin requerir API Keys ni inicio de sesión por formulario web.

---

##  Características Principales

- **Inyección de Cookies Local:** Evita el login por formulario (y sus bloqueos o desafíos CAPTCHA) reutilizando tokens de sesión activos (`sessionid`, `csrftoken`, `ds_user_id`).
- **Intercepción de Red (Network Interception):** Escucha las respuestas de la API interna (`/api/v1/users/web_profile_info/` y GraphQL) en segundo plano, obteniendo métricas numéricas exactas en lugar de depender de selectores CSS inestables del DOM.
- **Evasión Antibot (Stealth Layer):**
  - Ocultamiento de banderas de automatización (`navigator.webdriver = undefined`).
  - Parámetros de Chromium para deshabilitar alertas de control remoto (`--disable-blink-features=AutomationControlled`).
  - Pausas aleatorias (*jittering*) entre desplazamientos y navegaciones.
- **Metadatos Enriquecidos por Publicación:**
  - **Identificadores y Métricas:** Shortcode, ID, conteo exacto de *likes* y comentarios, fecha de publicación.
  - **Tipo de Publicación (`post_type`):** Clasificación automática en `Image`, `Video` o `Carousel`.
  - **Comentarios Destacados (`top_comments`):** Extracción de los primeros comentarios con usuario y texto.
  - **Análisis de Hashtags (`hashtags`):** Detección e higienización automática de hashtags dentro del pie de foto (*caption*).
- **Ejecución Flexible:** Soporta ejecución directa por IDE/botón *Run* (leyendo la configuración por defecto de `config.py`) o mediante argumentos por consola (CLI).

---

## Estructura del Proyecto


deber_instagram/
├── config.py             # Configuración central (rutas, pausas, banderas anti-bot)
├── main.py               # Punto de entrada principal y procesamiento de CLI
├── models.py             # Definición y validación de esquemas de datos con Pydantic
├── exceptions.py         # Jerarquía de excepciones personalizadas (SessionExpired, RateLimit, etc.)
├── scraper/
│   ├── __init__.py
│   └── instagram.py      # Lógica del scraper, escucha de eventos HTTP y scroll
├── utils/
│   ├── __init__.py
│   ├── cookie_manager.py # Carga, validación y formateo de cookies
│   └── humanize.py       # Algoritmos de retardo aleatorio y scroll progresivo
├── cookies.json.example  # Formato de plantilla para colocar las cookies de sesión
├── requirements.txt      # Dependencias del entorno
└── .gitignore            # Reglas de exclusión para credenciales y temporales
Requisitos e Instalación
Prerrequisitos
Python 3.10 o superior

Google Chrome o Chromium instalado

Pasos
Clonar el repositorio:

Bash
git clone [https://github.com/DavidJostin/arquitectura-software.git](https://github.com/DavidJostin/arquitectura-software.git)
cd arquitectura-software
Instalar dependencias y los binarios del navegador:

Bash
pip install -r requirements.txt
python -m playwright install chromium
Configurar las cookies de sesión:

Exporta las cookies de tu sesión activa de Instagram (usando extensiones como Cookie-Editor).

Crea un archivo llamado cookies.json en la raíz del proyecto con la estructura de la lista de objetos JSON.

Formas de Uso
1. Ejecución Directa (IDE / Botón Run)
Configura el usuario objetivo en config.py:

Python
INSTAGRAM_URL = "[https://www.instagram.com/nomeodiesquemeduele/](https://www.instagram.com/nomeodiesquemeduele/)"
Y ejecuta simplemente:

Bash
python main.py
2. Ejecución mediante Consola (CLI)
Puedes especificar un nombre de usuario, un @usuario o una URL directa, junto con parámetros opcionales:

Bash
python main.py nomeodiesquemeduele --max-posts 25
Opciones disponibles en CLI:

username: Nombre de usuario o URL (opcional, usa config.py si se omite).

--max-posts: Límite máximo de publicaciones a extraer.

--headless: Ejecuta el navegador en modo oculto.

--output: Ruta personalizada para el archivo de salida (por defecto output.json).

Estructura de Salida (output.json)
Los resultados se consolidan y guardan en formato JSON estructurado:

JSON
{
  "profile": {
    "username": "nomeodiesquemeduele",
    "full_name": "No me odies que me duele",
    "followers_count": 53762,
    "following_count": 1,
    "posts_count": 40,
    "biography": "",
    "is_private": false,
    "is_verified": false
  },
  "posts": [
    {
      "id": "34750123456789",
      "shortcode": "C_x123abc",
      "url": "[https://www.instagram.com/p/C_x123abc/](https://www.instagram.com/p/C_x123abc/)",
      "caption": "Ejemplo de publicación #Queto #Ecuador",
      "likes_count": 1420,
      "comments_count": 35,
      "post_type": "Carousel",
      "hashtags": ["#Queto", "#Ecuador"],
      "top_comments": [
        {
          "username": "usuario1",
          "text": "Excelente contenido!"
        }
      ],
      "timestamp": "2026-10-11T14:30:00Z"
    }
  ]
}
🛡️ Consideraciones de Seguridad
El archivo cookies.json contiene credenciales de autenticación activas (sessionid).

El repositorio incluye una regla explícita en .gitignore para garantizar que cookies.json y output.json nunca se suban al control de versiones.
