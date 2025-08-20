# === helpers.py ===
# Utilities for file validation/normalization, data extraction/cleaning,
# OpenAI API calls, and post-processing for table generation.

import os, json, re, ast, shutil
from openai import OpenAI
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from typing import Any, Optional, Union, List, Tuple, Dict
from copy import deepcopy
  
class UnsupportedFileTypeError(Exception):
    """Raised when the file is not a PDF."""
    pass

def enrich_df(df, gen_df):    
    # Copy metadata fields from df (result of /extract) to gen_df (LLM-generated result):
    # name_file, url_file, page, active, capture_log, subject_mail
    meta_cols = ["name_file", "url_file", "page", "active", "capture_log", "subject_mail"]

    def _first_non_null(series):
        """Returns the first non-null value in the series (or None if none exists)."""
        s = pd.Series(series) if not isinstance(series, pd.Series) else series
        s = s.dropna()
        return s.iloc[0] if not s.empty else None

    # Build a dict with metadata values taken from df
    meta_values = {}
    for c in meta_cols:
        if c not in df.columns:
            meta_values[c] = None
        else:
            # If there are multiple values in df, take the first non-null one
            meta_values[c] = _first_non_null(df[c])

    # Assign (broadcast) to all rows in gen_df
    for c, v in meta_values.items():
        gen_df[c] = v
    
    # Return a copy for safety (avoids external side effects)
    enriched_df = gen_df.copy()

    return enriched_df


def ensure_xlsx_extension(path: Path) -> Path:
    """
    Verifies that 'path' points to an .xlsx file (accepts .XLSX and normalizes to .xlsx).
    - Returns the final path (possibly renamed to .xlsx).
    - Raises FileNotFoundError if it doesn't exist.
    - Raises UnsupportedFileTypeError if the extension is not xlsx.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if path.suffix.lower() != ".xlsx":
        raise UnsupportedFileTypeError("File must end with .xlsx/.XLSX")

    # Normalize extension to lowercase if needed (useful on Windows)
    if path.suffix != ".xlsx":
        target = path.with_suffix(".xlsx")
        try:
            os.replace(path, target)
        except OSError:
            tmp = path.with_name(path.stem + ".__tmp__")
            os.replace(path, tmp)
            os.replace(tmp, target)
        return target

    return path

def _ensure_lowercase_pdf_extension(path: Path) -> Path:
    """
    If the file ends with .PDF, tries to rename it to .pdf.
    Handles Windows (case-insensitive) using a temporary rename if needed.
    Returns the final path (may be the same if not renamed).
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    # If it's already .pdf (lowercase), do nothing
    if path.suffix == ".pdf":
        return path

    # If it's .PDF or other case variations
    if path.suffix.lower() == ".pdf":
        target = path.with_suffix(".pdf")

        if str(path) == str(target):
            # Same path (can happen on case-insensitive FS); do nothing
            return target

        try:
            # Direct attempt
            os.replace(path, target)
            return target
        except OSError:
            # On Windows, renaming just by case change might fail.
            # Use a temporary name and then rename to final.
            tmp = path.with_name(path.stem + ".__tmp__")
            os.replace(path, tmp)
            os.replace(tmp, target)
            return target

    # Not a PDF
    raise UnsupportedFileTypeError("File does not have a .pdf/.PDF extension")

def preprocess_filename(filename: str, files_dir: Path) -> Path:
    """
    Preprocesses the filename:
      - Verifies it exists in 'files_dir'
      - Verifies it has a .pdf or .PDF extension
      - Normalizes to .pdf on disk if it was .PDF (or other case variations)
    Returns the final path (with lowercase .pdf if applicable).
    """
    if not filename:
        raise ValueError("You must provide a filename")

    candidate = (files_dir / filename).resolve()
    # Basic security: ensure it's within files_dir
    if files_dir.resolve() not in candidate.parents:
        raise ValueError("Invalid path outside allowed directory")

    if not candidate.exists():
        raise FileNotFoundError(f"File not found in {files_dir}: {filename}")

    # Verification + extension normalization
    return _ensure_lowercase_pdf_extension(candidate)

def split_pdf_to_pages(input_pdf: Path, output_dir: Path) -> Tuple[int, Path]:
    """
    Splits a PDF into individual pages inside 'output_dir'.
    Returns (num_pages, output_dir).
    Files are named: <basename>_page_<N>.pdf
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Lazy import to support both pypdf and PyPDF2
    try:
        from pypdf import PdfReader, PdfWriter  # preferred pypdf
    except Exception:
        from PyPDF2 import PdfReader, PdfWriter  # fallback

    reader = PdfReader(str(input_pdf))
    num_pages = len(reader.pages)

    basename = input_pdf.stem  # without extension
    for i in range(num_pages):
        writer = PdfWriter()
        writer.add_page(reader.pages[i])
        out_name = f"{basename}_page_{i+1}.pdf"
        out_path = output_dir / out_name
        with open(out_path, "wb") as f:
            writer.write(f)

    # Move file to sources
    shutil.move(f"{input_pdf}", f"/var/www/openai/uploads/source/{basename}.pdf")
    
    return num_pages, output_dir


def extract_page_number(filename):
    """
    Extracts the page number from a filename with format '*_page_NUMBER.pdf'
    """
    match = re.search(r'_page_(\d+)\.pdf$', filename)
    if match:
        return match.group(1)
    else:
        return None


def extract_original_pdf_name(file_name: str) -> str:
    """
    Given 'covalca_3_page_16.pdf' -> returns 'covalca_3.pdf'
    If the pattern doesn't match, returns the same name ensuring .pdf extension
    """
    if not file_name:
        raise ValueError("Empty file_name")

    # Force .pdf extension in lowercase
    if not file_name.lower().endswith(".pdf"):
        raise ValueError("File must end with .pdf/.PDF")

    # Normalize to lowercase only for extraction; return .pdf
    name = Path(file_name).name
    m = re.match(r"^(?P<base>.+?)_page_\d+\.pdf$", name, flags=re.IGNORECASE)
    base = m.group("base") if m else Path(name).stem
    return f"{base}.pdf"


def get_secret(
    var_name: str,
    dotenv_path: str | Path | None = None,
    *,
    raise_if_missing: bool = True,
    default: str | None = None,
    ) -> str | None:

    load_dotenv(dotenv_path)  

    value = os.getenv(var_name, default)
    if value is None and raise_if_missing:
        raise RuntimeError(
            f'Environment variable "{var_name}" not found. '
            "Create it in your .env file or export it in the shell."
        )
    return value


def html_table_to_tuples(html: str) -> List[Tuple[str, ...]]:
    """
    Converts an HTML table to a list of tuples representing the table rows.
    Returns an empty list if no table is found in the HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []          # No table → empty list

    rows_tuples = []
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"])
        rows_tuples.append(tuple(cell.get_text(strip=True) for cell in cells))
    return str(rows_tuples)

def parse_table_replace(row):
    if row['chunk_type'] == 'table':
        return html_table_to_tuples(row['text_html'])
    return None

def clean_filename(filename: str) -> str:
    """
    Cleans a filename by removing the last underscore and number before the extension.
    Example: 'file_123_45.pdf' -> 'file_123.pdf'
    """
    try:
        base, ext = filename.rsplit(".", 1)
    except ValueError:
        return filename
    
    cleaned_base = base.rsplit("_", 1)[0]

    return f"{cleaned_base}.{ext}"

def generate_invoice_json(client: OpenAI, resume_markdown: str):
    """
    Generates structured invoice data from markdown text using OpenAI's API.
    Returns a JSON object with invoice details.
    """
    system_prompt = (
                        "You are an AI résumé / invoice → JSON converter.\n"
                        "Your only goal is to transform user‑supplied Markdown into one valid JSON "
                        "object that exactly matches the schema the user provides.\n"
                        "Output ONLY that JSON – no prose, no markdown fences, no explanations."
                    )

    prompt = f"""
            ## TASK
            Convert the Markdown in **INPUT** into a single JSON object that follows the
            schema in **SCHEMA**.  
            The number of rows equals the count of **unique `item_id` values**.  
            Ensure every list has that same length.
            
            ## INPUT (≤ 650 tokens)
            {resume_markdown}
            
            ## SCHEMA
            {{
              "description":       [<str>, …],
              "codigo_1":          [<str>, …],
              "quantity":          [<str>, …],
              "unit_price_usd":    [<str>, …],
              "amount_usd":        [<str>, …],
              "customer":          [<str>, …],
              "origin":            [<str>, …],
              "brand":             [<str>, …],
              "part_number":       [<str>, …],
              "invoice":           [<str>, …],
              "sender":            [<str>, …],
              "unit":              [<str>, …],
              "currency":          [<str>, …],
              "incoterm":          [<str>, …],
              "item_id":           [<str>, …],
              "invoice_date":      [<str>, …],
              "customer_address":  [<str>, …],
              "codigo_2":          [<str>, …],
              "invoice_total":     [<str>, …],
              "subtotal":          [<str>, …],
              "due_date":          [<str>, …]
            }}
            
            ## RULES
            1. Return **only** the JSON object above; no extra keys, commentary or markdown.
            2. Lists the items if the item_id does not exist as a string numeric value in the invoice.
            3. If the input is text extracted from a Trexsel invoice, ignore 'FEDEX IP: ' row from list of tuples.
            4. Use valid UTF‑8, standard double quotes, no trailing commas.
            5. The entire response must be ≤ 3000 tokens.
            6. Just extract the client's address and ignores information regarding email, phone, or fax.
            """
    
    chat_history = []
    chat_history.append({"role": "system", "content": system_prompt})
    chat_history.append({"role": "user", "content": prompt})  
    
    gpt_response  = client.chat.completions.create(
        model = "o4-mini-2025-04-16",
        messages=chat_history,  # Enviar historial completo,
        max_completion_tokens=32000,  
        temperature=1,   
        frequency_penalty=0,  
        presence_penalty=0,
        stop=None,  
        stream=False
        )
    
    response = gpt_response.choices[0].message.content
    return response

def _extract_json_from_text(text: str) -> Optional[Union[dict, list]]:
    
    if not text:
        return None
    # Try to find JSON string within triple backticks
    match = re.search(r"```(json)?\n?(.*?)```", text, re.DOTALL)
    if match:
        json_str = match.group(2)
    else:
        json_str = text
    json_str = json_str.strip()

    # Attempt direct JSON parsing
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Fallback: find first JSON structure and try again
    start_candidates = [json_str.find(c) for c in ('{', '[') if json_str.find(c) != -1]
    end_candidates = [json_str.rfind(c) for c in ('}', ']') if json_str.rfind(c) != -1]
    if start_candidates and end_candidates:
        start = min(start_candidates)
        end = max(end_candidates)
        candidate = json_str[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # Last resort: literal_eval (handles single quotes, trailing commas, etc.)
        try:
            return ast.literal_eval(candidate)  # type: ignore[no-any-return]
        except Exception:
            return None
    return None


def balance_lists_by_item_id(data: Dict[str, List[Any]], placeholder: Any = "") -> Dict[str, List[Any]]:
    # Alinea todas las listas del diccionario a la longitud de data["item_id"].
    # - Si una lista es más corta, la rellena (placeholder o repite valor constante).
    # - Si es más larga, la recorta.
    # - Mantiene el input original sin modificar (deepcopy).
    # Precondiciones: data["item_id"] debe existir; todos los valores deben ser listas.

    if "item_id" not in data:
        raise KeyError('"item_id" key not found in input dictionary')

    target_len = len(data["item_id"])
    balanced = deepcopy(data)                   # keep original unchanged

    for key, lst in data.items():
        if not isinstance(lst, list):
            raise TypeError(f'Value for "{key}" must be a list, got {type(lst).__name__}')

        cur_len = len(lst)

        # ▸ Case 1: shorter than target  → pad
        if cur_len < target_len:
            if cur_len == 0:
                pad_value = placeholder
            elif all(elem == lst[0] for elem in lst):
                pad_value = lst[0]              # repeat constant value
            else:
                pad_value = placeholder

            balanced[key] = lst + [pad_value] * (target_len - cur_len)

        # ▸ Case 2: longer than target   → truncate
        elif cur_len > target_len:
            balanced[key] = lst[:target_len]

        # ▸ Case 3: already correct length – leave as is

    return balanced

def get_original_pdf_from_generated_xlsx(filename: str) -> str:
    """
    Derives the original PDF name from a generated XLSX filename.
    Example: 'covalca_9_page_3_generated.xlsx' -> 'covalca_9.pdf'
    """
    if not isinstance(filename, str) or not filename:
        return ""

    base_name = Path(filename).stem
    # Remove '_generated'
    if base_name.endswith('_generated'):
        base_name = base_name[:-10]

    # Remove '_page_N'
    match = re.match(r'^(?P<base>.+?)_page_\d+$', base_name)
    if match:
        original_base = match.group('base')
    else:
        original_base = base_name

    return f"{original_base}.pdf"

# 1. Define la función que hará el filtrado y concatenación
def extract_resume_markdown(x: Any, y: Any, df: pd.DataFrame) -> str:
    z = df[df['name_file'] == x]
    textos = z[z['page'] == y]['clean_text'].values
    # Si no hay valores, devolvemos cadena vacía
    return " ".join(textos) if len(textos) > 0 else ""
