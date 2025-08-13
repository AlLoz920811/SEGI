# uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# uvicorn main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive 300


# === API de FastAPI para separar PDFs por páginas, extraer contenido con Agentic Document Extraction,
#     generar tablas estructuradas con ayuda de OpenAI y finalmente insertar resultados a PostgreSQL. ===

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pathlib import Path
from openai import OpenAI
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import pg8000
from contextlib import asynccontextmanager
from agentic_doc.parse import parse
import logging
from opencensus.ext.azure.log_exporter import AzureLogHandler

# Import de utilidades y helpers:
# - Validaciones/normalizaciones de archivos (.pdf/.xlsx)
# - Funciones de pre/procesamiento (split, extract, parse_table_replace)
# - Acceso a secretos y funciones de post-proceso con LLM (generate_invoice_json, etc.)
# - enrich_df: función helper que agrega metadatos repetidos del df origen al gen_df

from helpers import (
    preprocess_filename,
    split_pdf_to_pages,
    UnsupportedFileTypeError,
    get_secret,
    extract_page_number,
    parse_table_replace,
    extract_original_pdf_name,
    ensure_xlsx_extension,            
    generate_invoice_json,            
    _extract_json_from_text,          
    balance_lists_by_item_id,        
    enrich_df                     
)

# === Configuración de variables de entorno ===
VISION_AGENT_API_KEY = get_secret("VISION_AGENT_API_KEY")
OPENAI_API_KEY = get_secret("OPENAI_API_KEY")
DB_HOST=get_secret("DB_HOST")     
DB_PORT=get_secret("DB_PORT")              
DB_NAME=get_secret("DB_NAME")   
DB_USER=get_secret("DB_USER")        
DB_PASSWORD=get_secret("DB_PASSWORD")
APPINSIGHTS_CONNECTION_STRING = get_secret("APPINSIGHTS_CONNECTION_STRING")

# Directorios base (junto a main.py y helpers.py)
BASE_DIR = Path(__file__).resolve().parent
FILES_DIR = BASE_DIR / "files"
PAGES_DIR = BASE_DIR / "pages"
RESULTS_DIR = BASE_DIR / "results"
TABLES_DIR = BASE_DIR / "tables"

# Cliente de OpenAI listo para reutilizar en endpoints que lo requieran
client = OpenAI(api_key=OPENAI_API_KEY)

# Logger (se configurará en el lifespan)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: configurar logger y crear carpetas
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Intenta configurar el logger de Azure, con fallback a la consola
    conn_str = get_secret("APPINSIGHTS_CONNECTION_STRING", raise_if_missing=False)
    if conn_str:
        try:
            handler = AzureLogHandler(connection_string=conn_str)
            logger.addHandler(handler)
            logger.info("Logger configurado con Azure Application Insights.")
        except Exception as e:
            logger.handlers.clear()
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            logger.addHandler(handler)
            logger.error(f"Error al configurar AzureLogHandler: {e}. Usando logging a consola.")
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(handler)
        logger.warning("APPINSIGHTS_CONNECTION_STRING no encontrada. Usando logging a consola.")

    logger.info("Iniciando aplicación y creando directorios...")
    for d in (FILES_DIR, PAGES_DIR, RESULTS_DIR, TABLES_DIR):
        d.mkdir(parents=True, exist_ok=True)
    logger.info("Directorios listos.")
    yield
    # Shutdown: (opcional) liberar recursos 
    logger.info("Cerrando aplicación.")

# Instancia de FastAPI con metadatos básicos
app = FastAPI(title="PDF Split API", version="1.0.0", lifespan=lifespan)


@app.get("/", summary="Bienvenida") # http://localhost:8000/
def root():
    # Endpoint raíz: mensaje breve e indicación de uso del endpoint /split
    return {
        "message": "API de división de PDFs en ejecución.",
        "hint": "Usa /split?filename=<tu_archivo.pdf> para separar el PDF en páginas.  /extract?filename=<tu_archivo_page_X.pdf> para extraer chunks con Agentic Document Extraction y guardar en Excel.  /generate?filename=<tu_archivo_page_X.xlsx> para generar tabla final desde Excel de results y guardar en tables.  /insert?filename=<tu_archivo_page_X_generated.xlsx> para insertar .xlsx desde 'tables' a PostgreSQL (tbl_captura_ia)",
        "status": "ok"
    }

@app.get("/split", summary="Separa un PDF en páginas") # http://localhost:8000/split?filename=<tu_archivo.pdf>
def split_pdf(filename: str = Query(..., description="Nombre del archivo en la carpeta 'files'")):
    """
    GET /split?filename=<archivo>
    - Preprocesa: valida extensión PDF (.pdf/.PDF). Si es .PDF la normaliza a .pdf
    - Procesa: separa el PDF por páginas en ./pages/<basename>_page_<N>.pdf
    - Respuestas:
        200 OK:   {"message": "...", "pages": <int>, "output_dir": "<ruta>"}
        400 Bad Request
        404 Not Found
        415 Unsupported Media Type
        500 Internal Server Error
    """
    logger.info(f"Recibida solicitud para /split con filename: '{filename}'")
    if not filename:
        logger.error("Solicitud /split rechazada: Falta el parámetro 'filename'")
        raise HTTPException(status_code=400, detail="Falta el parámetro 'filename'")

    try:
        # Preprocesamiento: valida que exista y sea PDF; normaliza extensión a .pdf si fuese .PDF
        input_pdf = preprocess_filename(filename, FILES_DIR)
        # Proceso: separa por páginas en la carpeta PAGES_DIR 
        num_pages, out_dir = split_pdf_to_pages(input_pdf, PAGES_DIR)
        
        # Respuesta exitosa con conteo de páginas y ruta de salida
        output_dir = str(PAGES_DIR.relative_to(BASE_DIR))
        logger.info(f"PDF '{filename}' separado exitosamente en {num_pages} páginas en '{output_dir}'")
        return JSONResponse(
            status_code=200,
            content={
                "message": "Proceso de separación de páginas exitoso",
                "pages": num_pages,
                "output_dir": output_dir,
            },
        )
    # Manejo de errores coherente con el contrato del endpoint
    except HTTPException as e:
        logger.error(f"Error de negocio en /split para '{filename}': {e.detail}")
        raise
    except FileNotFoundError as e:
        logger.error(f"Archivo no encontrado en /split para '{filename}': {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except UnsupportedFileTypeError as e:
        logger.error(f"Tipo de archivo no soportado en /split para '{filename}': {e}")
        raise HTTPException(status_code=415, detail=str(e))
    except Exception as e:
        logger.exception(f"Error inesperado en /split para '{filename}': {e}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


@app.get("/extract", summary="Extrae chunks de una página de PDF y guarda en Excel") # http://localhost:8000/extract?filename=<tu_archivo_page_X.pdf>
def extract(
    filename: str = Query(..., description="Nombre del PDF paginado dentro de la carpeta 'pages'")
):
    """
    Flujo:
      1) Validar existencia de pages/<file_name>
      2) Derivar original_pdf desde file_name (e.g. covalca_3.pdf)
      3) Obtener número de página con extract_page_number(file_name)
      4) parse(documents=[path], include_marginalia=True, include_metadata_in_markdown=True)
      5) Construir DataFrame con chunks y metadata
      6) Guardar Excel en carpeta results
    """
    logger.info(f"Recibida solicitud para /extract con filename: '{filename}'")
    if not filename:
        # Validación de parámetro requerido
        raise HTTPException(status_code=400, detail="Falta el parámetro 'filename'")

    try:
        # 1) Seguridad + existencia del recurso en pages/
        path = (PAGES_DIR / filename).resolve()
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"No se encontró el archivo en 'pages': {filename}")
        # Seguridad: que realmente esté dentro de PAGES_DIR
        if PAGES_DIR.resolve() not in path.parents:
            # Evita path traversal fuera del directorio previsto
            raise HTTPException(status_code=400, detail="Ruta inválida fuera de 'pages'")

        # 2) Derivar nombre del PDF original a partir del nombre con sufijo _page_N
        original_pdf = extract_original_pdf_name(filename)

        # 3) Extraer el número de página del filename
        page = extract_page_number(filename)

        # 4) Ejecutar extracción con Agentic Document Extraction
        result = parse(
            documents=[str(path)],
            include_marginalia=True,
            include_metadata_in_markdown=True,
        )

        # 5) Construcción del DataFrame con la salida del parser + metadatos
        tz = ZoneInfo("America/Mexico_City")
        capture_time_iso = datetime.now(tz).isoformat(" ", "seconds")

        records = []
        link = f"https://openia.soft-box.com.mx/files/{original_pdf}"  # URL pública del archivo original (contexto)
        for r in result:
            for i, chunk in enumerate(r.chunks):
                # chunk_type puede ser Enum; tomamos su .value si existe
                chunk_type = getattr(getattr(chunk, "chunk_type", ""), "value", str(getattr(chunk, "chunk_type", "")))
                chunk_id = getattr(chunk, "chunk_id", None)
                text_html = getattr(chunk, "text", "")

                groundings = getattr(chunk, "grounding", None)
                # Si el chunk trae múltiples groundings, se genera una fila por grounding
                if groundings:
                    for _ in groundings:
                        records.append({
                            "chunk_id": chunk_id,
                            "chunk": i + 1,
                            "chunk_type": str(chunk_type),
                            "text_html": text_html,
                            "name_file": original_pdf,
                            "url_file": link,
                            "page": page
                        })
                else:
                    # Si no hay grounding, al menos un registro por chunk
                    records.append({
                        "chunk_id": chunk_id,
                        "chunk": i + 1,
                        "chunk_type": str(chunk_type),
                        "text_html": text_html,
                        "name_file": original_pdf,
                        "url_file": link,
                        "page": page
                    })

        df = pd.DataFrame(records)
        if df.empty:
            # Estructura mínima si el parser no devolvió nada
            df = pd.DataFrame(columns=[
                "chunk_id", "chunk", "chunk_type", "text_html", "name_file", "url_file", "page",
                "active", "capture_log", "subject_mail", "clean_text"
            ])
        
        # Metacampos constantes y limpieza de texto
        df["active"] = "1"
        df["capture_log"] = capture_time_iso
        df["subject_mail"] = "captura"
        # Limpieza por fila (parse_table_replace proviene de helpers)
        df["clean_text"] = df.apply(parse_table_replace, axis=1)
        
        # Si no era tabla y clean_text quedó nulo, se usa el texto crudo
        mask = (df["chunk_type"].str.lower() != "table") & (df["clean_text"].isnull())
        df.loc[mask, "clean_text"] = df.loc[mask, "text_html"]
        
        # Persistencia a Excel en results/ con nombre basado en original + página
        original_stem = Path(original_pdf).stem
        excel_name = f"{original_stem}_page_{page}.xlsx"
        excel_path = RESULTS_DIR / excel_name
        df.to_excel(excel_path, index=False)  
        
        # Respuesta HTTP con detalles de la extracción
        logger.info(f"Extracción de '{filename}' completada. {len(df)} chunks guardados en '{excel_path.relative_to(BASE_DIR)}'")
        return JSONResponse(
            status_code=200,
            content={
                "message": "Extracción completada y guardada en Excel",
                "filename": filename,
                "original_pdf": original_pdf,
                "page": page,
                "rows": int(len(df)),
                "excel_path": str(excel_path.relative_to(BASE_DIR)),
            },
        )

    # Manejo de errores de validación, recursos y catch-all
    except HTTPException as e:
        logger.error(f"Error de negocio en /extract para '{filename}': {e.detail}")
        raise
    except FileNotFoundError as e:
        logger.error(f"Archivo no encontrado en /extract para '{filename}': {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Error inesperado en /extract para '{filename}': {e}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


@app.get("/generate", summary="Genera tabla final a partir de Excel de chunks") # http://localhost:8000/generate?filename=<tu_archivo_page_X.xlsx>
def generate(
    filename: str = Query(..., description="Nombre del archivo .xlsx dentro de 'results', p.ej. covalca_1_page_1.xlsx")
):
    """
    Flujo:
      1) Validar existencia y extensión .xlsx en 'results'
      2) Leer a pandas -> df
      3) Ejecutar pipeline LLM sobre df['clean_text'] -> blnc_data
      4) Convertir blnc_data a DataFrame -> gen_df
      5) Copiar metadatos repetidos (name_file, url_file, page, active, capture_log, subject_mail) desde df a gen_df
      6) Guardar gen_df en 'tables' como .xlsx
    """
    logger.info(f"Recibida solicitud para /generate con filename: '{filename}'")
    if not filename:
        raise HTTPException(status_code=400, detail="Falta el parámetro 'filename'")

    try:
        # 1) Validación y normalización de extensión
        path = (RESULTS_DIR / filename).resolve()
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"No se encontró el archivo en 'results': {filename}")
        if RESULTS_DIR.resolve() not in path.parents:
            raise HTTPException(status_code=400, detail="Ruta inválida fuera de 'results'")

        xlsx_path = ensure_xlsx_extension(path)  # valida/normaliza .xlsx

        # 2) Leer a DataFrame
        df = pd.read_excel(xlsx_path)  # requiere openpyxl instalado
        if "clean_text" not in df.columns:
            raise HTTPException(status_code=400, detail="El archivo no contiene la columna 'clean_text'")

        input_df = df

        # 3) Pipeline con OpenAI (helpers):
        #    - Se concatena el texto limpio por filas
        #    - Se genera JSON de factura con generate_invoice_json
        #    - Se extrae JSON válido con _extract_json_from_text
        #    - Se balancean listas por item_id con balance_lists_by_item_id
        resume_markdown = " ".join(input_df["clean_text"].astype(str).values)
        gen_data = generate_invoice_json(client, resume_markdown)
        ext_data = _extract_json_from_text(gen_data)
        blnc_data = balance_lists_by_item_id(ext_data)
        # 4) Conversión directa a DataFrame (el helper enrich_df se encargará de formas/normalizaciones adicionales)
        gen_df = pd.DataFrame(blnc_data)
        # 5) Enriquecer gen_df con metadatos repetidos provenientes de df (helper dedicado)
        enrich_gen_df = enrich_df(df, gen_df)

        # 6) Persistir resultado enriquecido en 'tables'
        out_name = f"{Path(filename).stem}_generated.xlsx"
        out_path = TABLES_DIR / out_name
        enrich_gen_df.to_excel(out_path, index=False)
        # 7) Respuesta indicando la tabla generada y cantidad de filas
        logger.info(f"Tabla generada para '{filename}' guardada en '{out_path.relative_to(BASE_DIR)}'")
        return JSONResponse(
            status_code=200,
            content={
                "message": "Generación completada y guardada en Excel",
                "input_results": str(xlsx_path),
                "rows": int(len(enrich_gen_df)),
                "output_tables": str(out_path.relative_to(BASE_DIR)),
            },
        )
    # Errores de tipo/validación/extensión y catch-alls
    except HTTPException as e:
        logger.error(f"Error de negocio en /generate para '{filename}': {e.detail}")
        raise
    except FileNotFoundError as e:
        logger.error(f"Archivo no encontrado en /generate para '{filename}': {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Error inesperado en /generate para '{filename}': {e}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


@app.get("/insert", summary="Inserta resultados de .xlsx a la base de datos") # http://localhost:8000/insert?filename=<tu_archivo_page_X_generated.xlsx>
def insert_results_to_db(
    filename: str = Query(..., description="Archivo .xlsx dentro de 'tables', ej: covalca_9_page_3_generated.xlsx")
):
    """
    Flujo:
      1) Validar existencia y extensión .xlsx del archivo en 'tables'
      2) Leer a pandas -> df
      3) (opcional) Normalización de vacíos -> 'NULL' literal
      4) Conectar a PostgreSQL (pg8000)
      5) INSERT de todas las filas en tbl_captura_ia
    """
    logger.info(f"Recibida solicitud para /insert con filename: '{filename}'")
    if not filename:
        raise HTTPException(status_code=400, detail="Falta el parámetro 'filename'")

    try:
        # 1) Validar existencia y extensión .xlsx del archivo en 'tables'
        path = (TABLES_DIR / filename).resolve()
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"No se encontró el archivo en 'tables': {filename}")
        if TABLES_DIR.resolve() not in path.parents:
            raise HTTPException(status_code=400, detail="Ruta inválida fuera de 'tables'")

        # Valida/normaliza .xlsx
        xlsx_path = ensure_xlsx_extension(path)

        # 2) Leer a DataFrame
        df = pd.read_excel(xlsx_path)  # requiere openpyxl instalado
        if df.empty:
            raise HTTPException(status_code=400, detail="El archivo .xlsx no contiene filas.")

        # 3) Sustituir cadenas vacías o espacios por la cadena literal "NULL" (tal como solicitaste)
        #    Nota: si tus columnas son numéricas/fecha en la BD, la cadena "NULL" puede causar error de tipo.
        #          En ese caso, considera usar None (SQL NULL) en lugar de la cadena "NULL".
        df = df.replace(r'^\s*$', 'NULL', regex=True)
        df = df.rename(columns={"item_id": "item"})
        df = df.rename(columns={"page": "page_number"})

        # 4) Conectar a PostgreSQL usando secretos
        DB_HOST = get_secret("DB_HOST")
        DB_PORT = int(get_secret("DB_PORT"))
        DB_NAME = get_secret("DB_NAME")
        DB_USER = get_secret("DB_USER")
        DB_PASSWORD = get_secret("DB_PASSWORD")

        # 5) INSERT a tabla fija
        dst_table = "tbl_captura_ia"

        cols = list(df.columns)
        if not cols:
            raise HTTPException(status_code=400, detail="No hay columnas para insertar.")

        # 6) Citar nombres de columnas para respetar mayúsculas/espacios si existieran
        cols_sql = ", ".join([f"\"{c}\"" for c in cols])
        placeholders = ", ".join(["%s"] * len(cols))
        insert_sql = f'INSERT INTO "{dst_table}" ({cols_sql}) VALUES ({placeholders})'

        # 7) Preparar filas
        rows = [tuple(row) for row in df.itertuples(index=False, name=None)]

        # 8) Ejecutar inserción
        conn = pg8000.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
        )
        try:
            cur = conn.cursor()
            cur.executemany(insert_sql, rows)
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
                logger.warning(f"Rollback ejecutado tras error de inserción para '{filename}'.")
            except Exception as rb_e:
                logger.error(f"Error durante el rollback para '{filename}': {rb_e}")
                pass
            logger.exception(f"Error al insertar en la base de datos para '{filename}': {e}")
            raise HTTPException(status_code=500, detail=f"Error al insertar en la base de datos: {str(e)}")
        finally:
            try:
                conn.close()
            except Exception: 
                pass
        # 9) Respuesta con conteo de filas insertadas
        logger.info(f"{len(rows)} filas de '{filename}' insertadas exitosamente en tabla '{dst_table}'.")
        return JSONResponse(
            status_code=200,
            content={
                "message": "Inserción completada",
                "filename": filename,
                "table": dst_table,
                "rows_inserted": int(len(rows)),
            },
        )

    # --- Manejadores de error homogéneos ---
    except HTTPException: 
        # Re-lanza errores ya formateados, ya loggeados en su contexto
        raise
    except FileNotFoundError as e:
        # Maneja archivos no encontrados
        logger.error(f"Archivo no encontrado en /insert para '{filename}': {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except UnsupportedFileTypeError as e:
        # Maneja archivos no soportados
        logger.error(f"Tipo de archivo no soportado en /insert para '{filename}': {e}")
        raise HTTPException(status_code=415, detail=str(e))
    except ValueError as e:
        # Maneja valores inválidos
        logger.error(f"Valor inválido en /insert para '{filename}': {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except pg8000.exceptions.InterfaceError as e:
        # Errores de conexión/protocolo
        logger.error(f"Fallo de conexión a la base de datos en /insert: {e}")
        raise HTTPException(status_code=502, detail=f"Fallo de conexión a la base de datos: {str(e)}")
    except pg8000.exceptions.DatabaseError as e:
        # Errores al ejecutar SQL
        logger.error(f"Error de base de datos en /insert para '{filename}': {e}")
        raise HTTPException(status_code=500, detail=f"Error de base de datos: {str(e)}")
    except Exception as e:
        # Maneja errores inesperados
        logger.exception(f"Error interno inesperado en /insert para '{filename}': {e}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")