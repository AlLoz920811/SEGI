# PDF Split & Extract API (FastAPI)

API para **dividir PDFs por páginas**, **extraer contenido** (Agentic Document Extraction), **generar tablas estructuradas con LLM** y **cargar resultados a PostgreSQL**.  
Proyecto compuesto por:

- `main.py` → API con FastAPI
- `helpers.py` → utilidades (validación de archivos, limpieza, funciones LLM, etc.)

> Nota: si en tu repo el archivo se llama `helper.py` en singular, renómbralo a `helpers.py` o ajusta los `import`.

---

## 🚀 Características

- **/split**: valida PDF y separa por páginas en `./pages`.
- **/extract**: analiza una página PDF (`pages/<base>_page_<N>.pdf`), construye un DataFrame con *chunks* y lo guarda como Excel en `./results`.
- **/generate**: toma un Excel de `./results`, resume/estructura con LLM y guarda la tabla final en `./tables`.
- **/insert**: lee un Excel de `./tables` y lo inserta en PostgreSQL (`tbl_captura_ia`) con `pg8000`.

Estructura de carpetas (se crean al iniciar la app):
```
files/    # PDFs de entrada
pages/    # PDFs paginados (<base>_page_<N>.pdf)
results/  # extracción cruda a Excel
tables/   # tablas generadas (listas "aplanadas") a Excel
```

---

## 🧩 Endpoints

| Método | Ruta        | Descripción |
|---|---|---|
| GET | `/` | Mensaje de bienvenida. |
| GET | `/split?filename=<archivo.pdf>` | Valida y separa el PDF por páginas en `pages/`. |
| GET | `/extract?filename=<base>_page_<N>.pdf` | Extrae contenido (chunks) y guarda Excel en `results/`. |
| GET | `/generate?filename=<base>_page_<N>.xlsx` | LLM → JSON → DataFrame; enriquece metadatos y guarda Excel en `tables/`. |
| GET | `/insert?filename=<base>_page_<N>_generated.xlsx` | Inserta el Excel de `tables/` a PostgreSQL (`tbl_captura_ia`). |

### Ejemplos rápidos

- Split:  
  `GET http://localhost:8000/split?filename=covalca_3.pdf`
- Extract:  
  `GET http://localhost:8000/extract?filename=covalca_3_page_16.pdf`
- Generate:  
  `GET http://localhost:8000/generate?filename=covalca_1_page_1.xlsx`
- Insert:  
  `GET http://localhost:8000/insert?filename=covalca_1_page_1_generated.xlsx`

> Abre `http://localhost:8000/docs` para probar con Swagger.

---

## ⚙️ Configuración

La API requiere ciertas variables de entorno para funcionar correctamente. Estas se pueden definir en un archivo `.env` en la raíz del proyecto. El archivo `.env` está ignorado por Git para proteger las credenciales.

Un ejemplo de `.env`:

```shell
# Azure Application Insights (opcional)
APPINSIGHTS_CONNECTION_STRING="tu_connection_string_de_app_insights"

# Credenciales de servicios
VISION_AGENT_API_KEY="tu_vision_agent_api_key"
OPENAI_API_KEY="tu_openai_api_key"

# Configuración de la base de datos PostgreSQL
DB_HOST="tu_db_host"
DB_PORT="5432"
DB_NAME="tu_db_name"
DB_USER="tu_db_user"
DB_PASSWORD="tu_db_password"
```

### Logging

La aplicación integra logging con **Azure Application Insights**. Si se provee la variable `APPINSIGHTS_CONNECTION_STRING`, los logs de la aplicación (eventos de ciclo de vida, peticiones a endpoints y errores) serán enviados a Azure. Si la variable no se encuentra, el logging se realizará en la consola, permitiendo el desarrollo y la depuración en local sin necesidad de una conexión a Azure.

---

## 🛠️ Dependencias

- **FastAPI**, **Uvicorn** (ASGI)
- **pypdf** (o PyPDF2 como respaldo)
- **pandas**, **openpyxl**
- **pg8000** (PostgreSQL)
- **python-dotenv** (cargar `.env`)
- **beautifulsoup4** (parseo de tabla HTML)
- Paquete del extractor: `agentic_doc.parse` (tu framework de **Agentic Document Extraction**)
- **openai** (cliente Python para la etapa LLM)
- **opencensus-ext-azure** (logging en Azure Application Insights)

Instalación típica:

```bash
pip install fastapi uvicorn pypdf pandas openpyxl pg8000 python-dotenv beautifulsoup4 openai opencensus-ext-azure
# y tu paquete/SDK para agentic_doc.parse
```

Python recomendado: **3.10+**.

---

## 🔐 Variables de entorno

Crea un archivo `.env` en la raíz del proyecto:

```env
# OpenAI
OPENAI_API_KEY=tu_api_key
VISION_AGENT_API_KEY=tu_api_key_opcional

# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=mi_base
DB_USER=mi_usuario
DB_PASSWORD=mi_password

# Azure Application Insights (opcional)
APPINSIGHTS_CONNECTION_STRING=tu_connection_string_de_app_insights
```

`helpers.get_secret` carga estos valores con soporte para `.env`.

---

## ▶️ Cómo ejecutar

1) Clona e instala dependencias.  
2) Ejecuta la API:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- En el navegador usa **http://localhost:8000** (no `0.0.0.0`).
- Documentación interactiva: **http://localhost:8000/docs**

---

## 🧠 Flujo de trabajo

1. **/split**  
   - Verifica `.pdf` / `.PDF`.  
   - Normaliza extensión a `.pdf` (maneja *case-insensitive* de Windows).  
   - Divide por páginas: `pages/<base>_page_<N>.pdf`.

2. **/extract**  
   - Usa `agentic_doc.parse.parse(...)`.  
   - Construye un DataFrame con:
     - `chunk_id`, `chunk_type`, `text_html`  
     - Metadatos: `name_file`, `url_file`, `page`, `active`, `capture_log`, `subject_mail`  
     - `clean_text` (mediante `parse_table_replace`)  
   - Guarda como Excel en `results/`.

3. **/generate**  
   - Lee el Excel de `results`.  
   - Concatena `clean_text` → prompt a LLM:
     - `generate_invoice_json` → `_extract_json_from_text` → `balance_lists_by_item_id`  
   - Crea `gen_df` (una fila por item).  
   - `enrich_df(df, gen_df)` copia metadatos desde `df`.  
   - Guarda como Excel en `tables/` (`*_generated.xlsx`).

4. **/insert**  
   - Valida `.xlsx` (`ensure_xlsx_extension`).  
   - Lee `tables/*.xlsx` a `df` y reemplaza vacíos por `"NULL"` (literal).  
   - Renombra columnas si aplica (`item_id`→`item`, `page`→`page_number`).  
   - Inserta en **PostgreSQL** tabla `tbl_captura_ia` con `pg8000`.

---

## 🧰 Helpers destacados

- **Validación/normalización de archivos**
  - `ensure_xlsx_extension(path)`  
  - `_ensure_lowercase_pdf_extension(path)`  
  - `preprocess_filename(filename, files_dir)`
- **PDF**
  - `split_pdf_to_pages(input_pdf, output_dir)`
  - `extract_page_number(filename)`
  - `extract_original_pdf_name(file_name)`
- **Parsing/Limpieza**
  - `html_table_to_tuples(html)`  
  - `parse_table_replace(row)`  
  - `clean_filename(filename)`
- **LLM**
  - `generate_invoice_json(client, resume_markdown)`  
  - `_extract_json_from_text(text)`  
  - `balance_lists_by_item_id(dict)`  
  - `enrich_df(df, gen_df)`  
- **Secrets**
  - `get_secret(var_name, dotenv_path=None, ...)`

---

## ⚠️ Errores comunes y tips

- **422 (Unprocessable Content)**: parámetro incorrecto. Ej.: `/extract` requiere `filename` (no `file_name`).  
- **415 (Unsupported Media Type)**: extensión inválida.  
- **404**: archivo no encontrado en la carpeta esperada.  
- **0.0.0.0** no es navegable: usa `http://localhost:8000` o `http://127.0.0.1:8000`.  
- Al insertar en PostgreSQL, la cadena `"NULL"` es **texto**, no `NULL` SQL. Si necesitas `NULL` real, usa `None` en lugar de `"NULL"`.

---

## 📂 Estructura sugerida del repo

```
.
├─ main.py
├─ helpers.py
├─ files/
├─ pages/
├─ results/
├─ tables/
├─ .env.example
└─ README.md
```

`./.env.example` (opcional) con las variables de entorno vacías para guía.

---

## 🧪 Pruebas rápidas (curl)

```bash
# Split
curl "http://localhost:8000/split?filename=covalca_3.pdf"

# Extract
curl "http://localhost:8000/extract?filename=covalca_3_page_16.pdf"

# Generate
curl "http://localhost:8000/generate?filename=covalca_1_page_1.xlsx"

# Insert
curl "http://localhost:8000/insert?filename=covalca_1_page_1_generated.xlsx"
```

---

## 📜 Licencia

MIT (o la que prefieras). Añade `LICENSE` al repo.

---

## 🤝 Contribuciones

Issues y PRs son bienvenidos.  
Por favor incluye:
- Descripción clara del cambio
- Pasos para reproducir
- Ejemplos de entrada/salida si aplica
