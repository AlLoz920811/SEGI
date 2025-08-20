# uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# uvicorn main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive 300

## === FastAPI API for splitting PDFs by pages, extracting content with Agentic Document Extraction,
##     generating structured tables with OpenAI, and finally inserting results into PostgreSQL. ===
import os
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

# Import utilities and helpers:
# - File validation/normalization (.pdf/.xlsx)
# - Pre/processing functions (split, extract, parse_table_replace)
# - Secrets access and LLM post-processing functions (generate_invoice_json, etc.)
# - enrich_df: helper function that adds repeated metadata from source df to gen_df

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
    enrich_df,                     
    get_original_pdf_from_generated_xlsx
)

# === Environment Variables Configuration ===
VISION_AGENT_API_KEY = get_secret("VISION_AGENT_API_KEY")
OPENAI_API_KEY = get_secret("OPENAI_API_KEY")
DB_HOST=get_secret("DB_HOST")     
DB_PORT=get_secret("DB_PORT")              
DB_NAME=get_secret("DB_NAME")   
DB_USER=get_secret("DB_USER")        
DB_PASSWORD=get_secret("DB_PASSWORD")  

# Base directories (alongside main.py and helpers.py)
BASE_DIR = Path(__file__).resolve().parent
FILES_DIR = BASE_DIR / "uploads/files"
PAGES_DIR = BASE_DIR / "uploads/pages"
RESULTS_DIR = BASE_DIR / "uploads/results"
TABLES_DIR = BASE_DIR / "uploads/tables"

# OpenAI client ready for reuse in endpoints that require it
client = OpenAI(api_key=OPENAI_API_KEY)

# FastAPI instance with basic metadata
app = FastAPI(title="PDF Split API", version="1.0.0")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create folders idempotently
    for d in (FILES_DIR, PAGES_DIR, RESULTS_DIR, TABLES_DIR):
        d.mkdir(parents=True, exist_ok=True)
    yield
    # Shutdown: (optional) release resources 

@app.get("/", summary="Welcome") # http://localhost:8000/
def root():
    # Root endpoint: brief message and hint about using the /split endpoint
    return {
        "message": "PDF splitting API is running.",
        "hint": "Use /split?filename=<your_file.pdf> to split the PDF into pages.  /extract?filename=<your_file_page_X.pdf> to extract chunks with Agentic Document Extraction and save to Excel.  /generate?filename=<your_file_page_X.xlsx> to generate final table from results Excel and save to tables.  /insert?filename=<your_file_page_X_generated.xlsx> to insert .xlsx from 'tables' to PostgreSQL (tbl_captura_ia)",
        "status": "ok"
    }

@app.get("/split", summary="Split a PDF into pages") # http://localhost:8000/split?filename=<your_file.pdf>
def split_pdf(filename: str = Query(..., description="Name of the file in the 'files' folder")):
    """
    GET /split?filename=<file>
    - Preprocessing: validates PDF extension (.pdf/.PDF). If .PDF, normalizes to .pdf
    - Processing: splits the PDF into pages in ./pages/<basename>_page_<N>.pdf
    - Responses:
        200 OK:   {"message": "...", "pages": <int>, "output_dir": "<path>"}
        400 Bad Request
        404 Not Found
        415 Unsupported Media Type
        500 Internal Server Error
    """
    if not filename:
        raise HTTPException(status_code=400, detail="Missing 'filename' parameter")

    try:
        # Preprocessing: validates file exists and is PDF; normalizes extension to .pdf if it was .PDF
        input_pdf = preprocess_filename(filename, FILES_DIR)
        # Check if the file has already been processed
        output_page_path = PAGES_DIR / f"{input_pdf.stem}_page_1.pdf"
        if output_page_path.exists():
            raise HTTPException(
                status_code=409, 
                detail=f"File '{filename}' has already been processed. Results exist in 'uploads/pages/'."
            )
        # Process: split into pages in PAGES_DIR 
        num_pages, out_dir = split_pdf_to_pages(input_pdf, PAGES_DIR)
        
        # Success response with page count and output path
        return JSONResponse(
            status_code=200,
            content={
                "message": "Page splitting process successful",
                "pages": num_pages,
                "output_dir": str(out_dir),
            },
        )
    # Error handling consistent with the endpoint contract
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except UnsupportedFileTypeError as e:
        # 415 Unsupported Media Type
        raise HTTPException(status_code=415, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Catch-all: unexpected errors
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@app.get("/extract", summary="Extract chunks with Agentic Document Extraction and save to Excel") # http://localhost:8000/extract?filename=<your_file_page_X.pdf>
def extract(
    filename: str = Query(..., description="Name of the paginated PDF inside the 'pages' folder")
):
    """
    Flow:
      1) Validate existence of pages/<filename>
      2) Derive original_pdf from filename (e.g., covalca_3.pdf)
      3) Get page number with extract_page_number(filename)
      4) parse(documents=[path], include_marginalia=True, include_metadata_in_markdown=True)
      5) Build DataFrame with chunks and metadata
      6) Save Excel to results folder
    """
    if not filename:
        # Required parameter validation
        raise HTTPException(status_code=400, detail="Missing 'filename' parameter")

    try:
        # 1) Security + resource existence in pages/
        path = (PAGES_DIR / filename).resolve()
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"File not found in 'pages': {filename}")
        # Security: ensure it's actually inside PAGES_DIR
        if PAGES_DIR.resolve() not in path.parents:
            # Prevents path traversal outside the intended directory
            raise HTTPException(status_code=400, detail="Invalid path outside 'pages'")

        # Check if the file has already been processed
        output_excel_path = RESULTS_DIR / f"{path.stem}.xlsx"
        if output_excel_path.exists():
            raise HTTPException(
                status_code=409, 
                detail=f"File '{filename}' has already been extracted. The result exists in 'uploads/results/{output_excel_path.name}'."
            )

        # 2) Derive original PDF name from the _page_N suffixed filename
        original_pdf = extract_original_pdf_name(filename)

        # 3) Extract page number from filename
        page = extract_page_number(filename)

        # 4) Perform extraction with Agentic Document Extraction
        result = parse(
            documents=[str(path)],
            include_marginalia=True,
            include_metadata_in_markdown=True,
        )

        # 5) Build DataFrame with parser output + metadata
        tz = ZoneInfo("America/Mexico_City")
        capture_time_iso = datetime.now(tz).isoformat(" ", "seconds")

        records = []
        link = f"https://v-card.mx/captura/uploads/{original_pdf}"  # Public URL of the original file (context)
        for r in result:
            for i, chunk in enumerate(r.chunks):
                # chunk_type might be Enum; we take its .value if it exists
                chunk_type = getattr(getattr(chunk, "chunk_type", ""), "value", str(getattr(chunk, "chunk_type", "")))
                chunk_id = getattr(chunk, "chunk_id", None)
                text_html = getattr(chunk, "text", "")

                groundings = getattr(chunk, "grounding", None)
                # If the chunk has multiple groundings, generate a row per grounding
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
                    # If there's no grounding, at least one record per chunk
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
            # Minimal structure if the parser didn't return anything
            df = pd.DataFrame(columns=[
                "chunk_id", "chunk", "chunk_type", "text_html", "name_file", "url_file", "page",
                "active", "capture_log", "subject_mail", "clean_text"
            ])
        
        # Metafields constants and text cleaning
        df["active"] = "1"
        df["capture_log"] = capture_time_iso
        df["subject_mail"] = "capture"
        # Cleaning per row (parse_table_replace comes from helpers)
        df["clean_text"] = df.apply(parse_table_replace, axis=1)
        
        # If it wasn't a table and clean_text ended up null, use the raw text
        mask = (df["chunk_type"].str.lower() != "table") & (df["clean_text"].isnull())
        df.loc[mask, "clean_text"] = df.loc[mask, "text_html"]
        
        # Save to Excel in results/ with a name based on the original + page
        original_stem = Path(original_pdf).stem
        excel_name = f"{original_stem}_page_{page}.xlsx"
        excel_path = RESULTS_DIR / excel_name
        df.to_excel(excel_path, index=False)  
        
        # Delete the your_file_page_X.pdf file to monitor new arrivals
        os.remove(PAGES_DIR / filename)
        
        # HTTP response with extraction details
        return JSONResponse(
            status_code=200,
            content={
                "message": "Extraction completed and saved to Excel",
                "filename": filename,
                "original_pdf": original_pdf,
                "page": page,
                "rows": int(len(df)),
                "excel_path": str(excel_path),
            },
        )

    # Error handling for validation, resources, and catch-all
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@app.get("/generate", summary="Generate final table from results Excel and save to tables") # http://localhost:8000/generate?filename=<your_file_page_X.xlsx>
def generate(
    filename: str = Query(..., description="Name of the .xlsx file inside 'results', e.g., covalca_1_page_1.xlsx")
):
    """
    Flow:
      1) Validate existence and .xlsx extension in 'results' (helpers.ensure_xlsx_extension)
      2) Read to pandas -> df
      3) Run LLM pipeline on df['clean_text'] -> blnc_data
      4) Convert blnc_data to DataFrame -> gen_df
      5) Copy repeated metadata (name_file, url_file, page, active, capture_log, subject_mail) from df to gen_df
      6) Save gen_df to 'tables' as .xlsx
    """
    if not filename:
        raise HTTPException(status_code=400, detail="Missing 'filename' parameter")

    try:
        # 1) Validation and normalization of extension
        path = (RESULTS_DIR / filename).resolve()
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"File not found in 'results': {filename}")
        if RESULTS_DIR.resolve() not in path.parents:
            raise HTTPException(status_code=400, detail="Invalid path outside 'results'")

        xlsx_path = ensure_xlsx_extension(path)  # validates/normalizes .xlsx

        # Check if the file has already been processed
        output_generated_path = TABLES_DIR / f"{path.stem}_generated.xlsx"
        if output_generated_path.exists():
            raise HTTPException(
                status_code=409, 
                detail=f"File '{filename}' has already been generated. The result exists in 'uploads/tables/{output_generated_path.name}'."
            )

        # 2) Read to DataFrame
        df = pd.read_excel(xlsx_path)  # requires openpyxl installed
        if "clean_text" not in df.columns:
            raise HTTPException(status_code=400, detail="The file does not contain the 'clean_text' column")

        input_df = df

        # 3) LLM pipeline with OpenAI (helpers):
        #    - Concatenates cleaned text per row
        #    - Generates JSON invoice with generate_invoice_json
        #    - Extracts valid JSON with _extract_json_from_text
        #    - Balances lists by item_id with balance_lists_by_item_id
        resume_markdown = " ".join(input_df["clean_text"].astype(str).values)
        gen_data = generate_invoice_json(client, resume_markdown)
        ext_data = _extract_json_from_text(gen_data)
        blnc_data = balance_lists_by_item_id(ext_data)
        # 4) Direct conversion to DataFrame (the enrich_df helper will handle additional formatting/normalization)
        gen_df = pd.DataFrame(blnc_data)
        # 5) Enrich gen_df with repeated metadata from df (dedicated helper)
        enrich_gen_df = enrich_df(df, gen_df)

        # 6) Save enriched result to 'tables'
        out_name = f"{Path(filename).stem}_generated.xlsx"
        out_path = TABLES_DIR / out_name
        enrich_gen_df.to_excel(out_path, index=False)
        
        # 6.1) Delete the your_file_page_X.xlsx file to monitor new arrivals
        os.remove(RESULTS_DIR / filename)
        
        # 7) Response indicating the generated table and row count
        return JSONResponse(
            status_code=200,
            content={
                "message": "Generation completed and saved to Excel",
                "input_results": str(xlsx_path),
                "rows": int(len(enrich_gen_df)),
                "output_tables": str(out_path),
            },
        )
    # Error handling for type/validation/extension and catch-all
    except UnsupportedFileTypeError as e:
        raise HTTPException(status_code=415, detail=str(e))
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@app.get("/insert", summary="Insert .xlsx from 'tables' to PostgreSQL (tbl_captura_ia)") # http://localhost:8000/insert?filename=<your_file_page_X_generated.xlsx>
def insert_results_to_db(
    filename: str = Query(..., description="File .xlsx inside 'tables', e.g., covalca_9_page_3_generated.xlsx")
):
    """
    Flow:
      1) Validate existence and .xlsx extension of the file in 'tables'
      2) Read to pandas -> df
      3) (Optional) Normalize empty values to 'NULL' literal
      4) Connect to PostgreSQL (pg8000)
      5) INSERT all rows into tbl_captura_ia
    """
    if not filename:
        raise HTTPException(status_code=400, detail="Missing 'filename' parameter")

    try:
        # 1) Validate file path
        path = (TABLES_DIR / filename).resolve()
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"File not found in 'tables': {filename}")
        if TABLES_DIR.resolve() not in path.parents:
            raise HTTPException(status_code=400, detail="Invalid path outside 'tables'")

        # Validate/normalize .xlsx
        xlsx_path = ensure_xlsx_extension(path)

        # 2) Read to DataFrame
        df = pd.read_excel(xlsx_path)  # requires openpyxl installed
        if df.empty:
            raise HTTPException(status_code=400, detail="The .xlsx file does not contain rows.")

        # 3) Replace empty strings or spaces with the 'NULL' literal (as requested)
        #    Note: if your columns are numeric/date in the DB, the 'NULL' string may cause a type error.
        #          In that case, consider using None (SQL NULL) instead of the 'NULL' string.
        df = df.replace(r'^\s*$', 'NULL', regex=True)
        df = df.rename(columns={"item_id": "item"})
        df = df.rename(columns={"page": "page_number"})

        # 4) Connect to PostgreSQL using secrets
        DB_HOST = get_secret("DB_HOST")
        DB_PORT = int(get_secret("DB_PORT"))
        DB_NAME = get_secret("DB_NAME")
        DB_USER = get_secret("DB_USER")
        DB_PASSWORD = get_secret("DB_PASSWORD")

        # 5) INSERT into fixed table
        dst_table = "tbl_captura_ia"

        cols = list(df.columns)
        if not cols:
            raise HTTPException(status_code=400, detail="No columns to insert.")

        # 6) Quote column names to respect uppercase/spaces if they exist
        cols_sql = ", ".join([f"\"{c}\"" for c in cols])
        placeholders = ", ".join(["%s"] * len(cols))
        insert_sql = f'INSERT INTO "{dst_table}" ({cols_sql}) VALUES ({placeholders})'

        # 7) Prepare rows
        rows = [tuple(row) for row in df.itertuples(index=False, name=None)]

        # 8) Execute insertion
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
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=f"Error inserting into the database: {str(e)}")
        finally:
            try:
                conn.close()
            except Exception: 
                pass
        
        # 8.1) Delete the your_file_page_X_generated.xlsx file to monitor new arrivals
        os.remove(TABLES_DIR / filename)
        
        # 9) Response with row count inserted
        original_filename = get_original_pdf_from_generated_xlsx(filename)
        return JSONResponse(
            status_code=200,
            content={
                "message": "Insertion completed",
                "filename": filename,
                "original_filename": original_filename,
                "table": dst_table,
                "rows_inserted": int(len(rows)),
            },
        )

    # --- Homogeneous error handlers ---
    except HTTPException: 
        # Re-raise already formatted errors
        raise
    except FileNotFoundError as e:
        # Handle files not found
        raise HTTPException(status_code=404, detail=str(e))
    except UnsupportedFileTypeError as e:
        # Handle unsupported files
        raise HTTPException(status_code=415, detail=str(e))
    except ValueError as e:
        # Handle invalid values
        raise HTTPException(status_code=400, detail=str(e))
    except pg8000.exceptions.InterfaceError as e:
        # Connection/protocol errors
        raise HTTPException(status_code=502, detail=f"Database connection failure: {str(e)}")
    except pg8000.exceptions.DatabaseError as e:
        # SQL execution errors
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        # Handle unexpected errors
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
