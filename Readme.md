# PDF Split & Extract API (FastAPI)

API for **splitting PDFs by pages**, **extracting content** (Agentic Document Extraction), **generating structured tables with LLM**, and **loading results to PostgreSQL**.  
Project structure:

- `main.py` → API with FastAPI
- `helpers.py` → utilities (file validation, cleaning, LLM functions, etc.)

> Note: If your repo has the file named `helper.py` (singular), rename it to `helpers.py` or adjust the imports.

---

## 🚀 Features

- **/split**: Validates PDF and splits into pages in `./pages`.
- **/extract**: Analyzes a PDF page (`pages/<base>_page_<N>.pdf`), builds a DataFrame with *chunks*, and saves it as Excel in `./results`.
- **/generate**: Takes an Excel from `./results`, summarizes/structures it with LLM, and saves the final table in `./tables`.
- **/insert**: Reads an Excel from `./tables` and inserts it into PostgreSQL (`tbl_captura_ia`) using `pg8000`.

Folder structure (created when the app starts):
```
files/    # Input PDFs
pages/    # Paginated PDFs (<base>_page_<N>.pdf)
results/  # Raw extraction to Excel
tables/   # Generated tables (flattened) to Excel
```

---

## 🧩 Endpoints

| Method | Path        | Description |
|---|---|---|
| GET | `/` | Welcome message. |
| GET | `/split?filename=<file.pdf>` | Validates and splits the PDF into pages in `pages/`. |
| GET | `/extract?filename=<base>_page_<N>.pdf` | Extracts content (chunks) and saves Excel in `results/`. |
| GET | `/generate?filename=<base>_page_<N>.xlsx` | LLM → JSON → DataFrame; enriches metadata and saves Excel in `tables/`. |
| GET | `/insert?filename=<base>_page_<N>_generated.xlsx` | Inserts the Excel from `tables/` into PostgreSQL (`tbl_captura_ia`). |

### Quick Examples

- Split:  
  `GET http://localhost:8000/split?filename=covalca_3.pdf`
- Extract:  
  `GET http://localhost:8000/extract?filename=covalca_3_page_16.pdf`
- Generate:  
  `GET http://localhost:8000/generate?filename=covalca_1_page_1.xlsx`
- Insert:  
  `GET http://localhost:8000/insert?filename=covalca_1_page_1_generated.xlsx`

> Open `http://localhost:8000/docs` to test with Swagger.

---

## 🛠️ Dependencies

- **FastAPI**, **Uvicorn** (ASGI)
- **pypdf** (or PyPDF2 as fallback)
- **pandas**, **openpyxl**
- **pg8000** (PostgreSQL)
- **python-dotenv** (load `.env`)
- **beautifulsoup4** (HTML table parsing)
- Extraction package: `agentic_doc.parse` (your **Agentic Document Extraction** framework)
- **openai** (Python client for LLM stage)

Typical installation:

```bash
pip install fastapi uvicorn pypdf pandas openpyxl pg8000 python-dotenv beautifulsoup4 openai
# and your package/SDK for agentic_doc.parse
```

Recommended Python: **3.10+**.

---

## 🔐 Environment Variables

Create a `.env` file in the project root:

```env
# OpenAI
OPENAI_API_KEY=your_api_key
VISION_AGENT_API_KEY=your_optional_api_key

# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=my_database
DB_USER=my_user
DB_PASSWORD=my_password
```

`helpers.get_secret` loads these values with `.env` support.

---

## ▶️ How to Run

1) Clone and install dependencies.  
2) Run the API:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- In the browser, use **http://localhost:8000** (not `0.0.0.0`).
- Interactive documentation: **http://localhost:8000/docs**

---

## 🧠 Workflow

1. **/split**  
   - Verifies `.pdf` / `.PDF`.  
   - Normalizes extension to `.pdf` (handles Windows *case-insensitive*).  
   - Splits into pages: `pages/<base>_page_<N>.pdf`.

2. **/extract**  
   - Uses `agentic_doc.parse.parse(...)`.  
   - Builds a DataFrame with:
     - `chunk_id`, `chunk_type`, `text_html`  
     - Metadata: `name_file`, `url_file`, `page`, `active`, `capture_log`, `subject_mail`  
     - `clean_text` (via `parse_table_replace`)  
   - Saves as Excel in `results/`.

3. **/generate**  
   - Reads Excel from `results`.  
   - Concatenates `clean_text` → LLM prompt:
     - `generate_invoice_json` → `_extract_json_from_text` → `balance_lists_by_item_id`  
   - Creates `gen_df` (one row per item).  
   - `enrich_df(df, gen_df)` copies metadata from `df`.  
   - Saves as Excel in `tables/` (`*_generated.xlsx`).

4. **/insert**  
   - Validates `.xlsx` (`ensure_xlsx_extension`).  
   - Reads `tables/*.xlsx` to `df` and replaces empty values with `"NULL"` (literal).  
   - Renames columns if needed (`item_id`→`item`, `page`→`page_number`).  
   - Inserts into **PostgreSQL** table `tbl_captura_ia` using `pg8000`.

---

## 🧰 Key Helpers

- **File validation/normalization**
  - `ensure_xlsx_extension(path)`  
  - `_ensure_lowercase_pdf_extension(path)`  
  - `preprocess_filename(filename, files_dir)`
- **PDF**
  - `split_pdf_to_pages(input_pdf, output_dir)`
  - `extract_page_number(filename)`
  - `extract_original_pdf_name(file_name)`
- **Parsing/Cleaning**
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

## ⚠️ Common Errors and Tips

- **422 (Unprocessable Content)**: Incorrect parameter. E.g., `/extract` requires `filename` (not `file_name`).  
- **415 (Unsupported Media Type)**: Invalid extension.  
- **404**: File not found in the expected folder.  
- **0.0.0.0** is not browsable: use `http://localhost:8000` or `http://127.0.0.1:8000`.  
- When inserting into PostgreSQL, the string `"NULL"` is **text**, not SQL `NULL`. If you need actual `NULL`, use `None` instead of `"NULL"`.

---

## 📂 Suggested Repo Structure

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

`./.env.example` (optional) with empty environment variables as a guide.

---

## 🧪 Quick Tests (curl)

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

## 📜 License

MIT (or the license of your choice). Add `LICENSE` to the repo.

---

## 🤝 Contributions

Issues and PRs are welcome.  
Please include:
- Clear description of the change
- Steps to reproduce
- Examples of input/output if applicable
