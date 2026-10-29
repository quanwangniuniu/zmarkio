from datetime import timedelta
from html import escape
from urllib.parse import urlencode
import math

import requests
from django.conf import settings
from django.utils import timezone

from .models import GoogleDocsConnection


GOOGLE_OAUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_DOCS_URL = "https://docs.googleapis.com/v1/documents"
GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
GOOGLE_SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"

GOOGLE_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def build_google_auth_url(state: str) -> str:
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_DOCS_OAUTH_REDIRECT_URI,   
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_OAUTH_BASE_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict:
    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_DOCS_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    expires_in = int(payload.get("expires_in", 3600))
    payload["token_expiry"] = timezone.now() + timedelta(seconds=expires_in)
    return payload


def refresh_google_tokens(refresh_token: str) -> dict:
    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise ValueError("No access_token in Google refresh response.")
    expires_in = int(payload.get("expires_in", 3600))
    payload["token_expiry"] = timezone.now() + timedelta(seconds=expires_in)
    return payload


def _persist_refreshed_tokens(connection: GoogleDocsConnection, payload: dict) -> None:
    connection.set_access_token(payload["access_token"])
    new_refresh = payload.get("refresh_token")
    if new_refresh:
        connection.set_refresh_token(new_refresh)
    connection.token_expiry = payload.get("token_expiry")
    connection.save(update_fields=["encrypted_access_token", "encrypted_refresh_token", "token_expiry", "updated_at"])


def get_access_token_for_api(connection: GoogleDocsConnection) -> str:
    if not connection.get_access_token():
        raise ValueError("Google Docs is not connected.")
    now = timezone.now()
    buffer = timedelta(minutes=2)
    expiry_ok = connection.token_expiry and connection.token_expiry > now + buffer
    if expiry_ok:
        return connection.get_access_token()
    if not connection.get_refresh_token():
        return connection.get_access_token()
    payload = refresh_google_tokens(connection.get_refresh_token())
    _persist_refreshed_tokens(connection, payload)
    return connection.get_access_token()


def run_google_api_with_token_retry(connection: GoogleDocsConnection, fn):
    token = get_access_token_for_api(connection)
    try:
        return fn(token)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 401:
            raise
        if not connection.get_refresh_token():
            raise
        payload = refresh_google_tokens(connection.get_refresh_token())
        _persist_refreshed_tokens(connection, payload)
        return fn(connection.get_access_token())

_XLSX_EXPORT_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

def export_google_spreadsheet_xlsx(source, access_token):
    """Read one Google spreadsheet as xlsx. The source spreadsheet is not modified."""
    spreadsheet_id = getattr(source, "google_spreadsheet_id", None)
    sheet_id = getattr(source, "google_sheet_id", None)
    if not isinstance(spreadsheet_id, str) or not spreadsheet_id.strip():
        raise ValueError("missing google spreadsheet id")
    if isinstance(sheet_id, bool) or not isinstance(sheet_id, int) or sheet_id < 0:
        raise ValueError("missing google sheet id")
    spreadsheet_id = spreadsheet_id.strip()

    metadata = requests.get(
        f"{GOOGLE_SHEETS_URL}/{spreadsheet_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"fields": "sheets.properties(sheetId,title)"},
        timeout=20,
    )
    metadata.raise_for_status()
    title = None
    for item in metadata.json().get("sheets") or []:
        properties = item.get("properties") or {}
        if properties.get("sheetId") == sheet_id:
            title = properties.get("title")
            break
    if not isinstance(title, str) or not title.strip():
        raise ValueError("google worksheet was not found")

    exported = requests.get(
        f"{GOOGLE_DRIVE_FILES_URL}/{spreadsheet_id}/export",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"mimeType": _XLSX_EXPORT_MIME},
        timeout=60,
    )
    exported.raise_for_status()
    if not exported.content:
        raise ValueError("google xlsx export was empty")
    return exported.content, title.strip()



def fetch_google_email(access_token: str) -> str | None:
    response = requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("email")


def _extract_plain_text(structural_elements: list[dict]) -> str:
    chunks: list[str] = []
    for element in structural_elements:
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        for text_run in paragraph.get("elements", []):
            content = text_run.get("textRun", {}).get("content")
            if content:
                chunks.append(content)
    return "".join(chunks).strip()

def _named_style_text_styles(named_styles: dict | None) -> dict[str, dict]:
    styles: dict[str, dict] = {}
    for style in (named_styles or {}).get("styles") or []:
        name = style.get("namedStyleType")
        if name:
            styles[name] = style.get("textStyle") or {}
    return styles


def _render_text_run_to_html(text_run: dict, inherited_style: dict | None = None) -> str:
    content = text_run.get("content") or ""
    if not content:
        return ""

    text_style = text_run.get("textStyle") or {}
    inherited_style = inherited_style or {}
    rendered = escape(content).replace("\n", "<br>")

    family = ((text_style.get("weightedFontFamily") or {}).get("fontFamily") or "").strip()
    if not family:
        family = ((inherited_style.get("weightedFontFamily") or {}).get("fontFamily") or "").strip()
    size = text_style.get("fontSize") or inherited_style.get("fontSize") or {}
    magnitude = size.get("magnitude")
    styles = []
    if family:
        styles.append(f"font-family: '{escape(family)}'")
    if isinstance(magnitude, (int, float)) and not isinstance(magnitude, bool):
        styles.append(f"font-size: {magnitude}pt")
    if styles:
        rendered = f'<span style="{"; ".join(styles)}">{rendered}</span>'

    link = (text_style.get("link") or {}).get("url")
    if link:
        rendered = f'<a href="{escape(link)}" target="_blank" rel="noopener noreferrer">{rendered}</a>'
    if text_style.get("bold"):
        rendered = f"<strong>{rendered}</strong>"
    if text_style.get("italic"):
        rendered = f"<em>{rendered}</em>"
    if text_style.get("underline"):
        rendered = f"<u>{rendered}</u>"

    return rendered


def _extract_rich_html(structural_elements: list[dict], named_text_styles: dict[str, dict] | None = None) -> str:
    named_text_styles = named_text_styles or {}
    blocks: list[str] = []
    for element in structural_elements:
        paragraph = element.get("paragraph")
        if paragraph:
            style_name = (paragraph.get("paragraphStyle") or {}).get("namedStyleType") or "NORMAL_TEXT"
            inherited = named_text_styles.get(style_name) or named_text_styles.get("NORMAL_TEXT") or {}
            line_parts: list[str] = []
            for paragraph_element in paragraph.get("elements", []):
                text_run = paragraph_element.get("textRun")
                if text_run:
                    line_parts.append(_render_text_run_to_html(text_run, inherited))
            line_html = "".join(line_parts).strip()
            if line_html:
                blocks.append(f"<p>{line_html}</p>")
            continue

        table = element.get("table")
        if table:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    cell_html = _extract_rich_html(cell.get("content", []), named_text_styles)
                    if cell_html:
                        blocks.append(cell_html)

    return "".join(blocks).strip()

def fetch_document_text(document_id: str, access_token: str) -> tuple[str, str, str]:
    response = requests.get(
        f"{GOOGLE_DOCS_URL}/{document_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    title = payload.get("title") or "Imported Google Doc"
    body = payload.get("body", {}).get("content", [])
    plain_text = _extract_plain_text(body)
    named_text_styles = _named_style_text_styles(payload.get("namedStyles"))
    rich_html = _extract_rich_html(body, named_text_styles)
    return title, plain_text, rich_html


def create_google_doc(title: str, content: str, access_token: str) -> dict:
    create_response = requests.post(
        GOOGLE_DOCS_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={"title": title},
        timeout=20,
    )
    create_response.raise_for_status()
    document = create_response.json()
    document_id = document["documentId"]

    requests.post(
        f"{GOOGLE_DOCS_URL}/{document_id}:batchUpdate",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "requests": [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": content,
                    }
                }
            ]
        },
        timeout=20,
    ).raise_for_status()

    return {
        "document_id": document_id,
        "url": f"https://docs.google.com/document/d/{document_id}/edit",
    }


def list_google_docs(access_token: str, page_size: int = 20) -> list[dict]:
    response = requests.get(
        GOOGLE_DRIVE_FILES_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "q": "mimeType='application/vnd.google-apps.document' and trashed=false",
            "fields": "files(id,name,modifiedTime,webViewLink)",
            "orderBy": "modifiedTime desc",
            "pageSize": max(1, min(page_size, 100)),
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    files = payload.get("files") or []
    return [
        {
            "id": item.get("id"),
            "name": item.get("name") or "Untitled document",
            "modified_time": item.get("modifiedTime"),
            "web_view_link": item.get("webViewLink"),
        }
        for item in files
        if item.get("id")
    ]


def _col_letter(n: int) -> str:
    """Convert 1-based column number to column letter(s): 1→A, 26→Z, 27→AA."""
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result or "A"

_DATE_TYPES = {"DATE", "TIME", "DATE_TIME"}

_GRID_FIELDS = ",".join([
    "sheets.merges",
    "sheets.data.startRow",
    "sheets.data.startColumn",
    "sheets.data.rowData.values.formattedValue",
    "sheets.data.rowData.values.effectiveValue",
    "sheets.data.rowData.values.userEnteredValue.formulaValue",
    "sheets.data.rowData.values.userEnteredFormat.numberFormat",
    "sheets.data.rowData.values.userEnteredFormat.backgroundColor",
    "sheets.data.rowData.values.userEnteredFormat.backgroundColorStyle",
    "sheets.data.rowData.values.userEnteredFormat.textFormat",
    "sheets.data.rowData.values.effectiveFormat.textFormat.foregroundColor",
    "sheets.data.rowData.values.effectiveFormat.textFormat.foregroundColorStyle",
    "sheets.data.rowData.values.effectiveFormat.backgroundColor",
    "sheets.data.rowData.values.effectiveFormat.backgroundColorStyle",
])


def _number_to_matrix(number) -> str:
    if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
        return ""
    if float(number).is_integer():
        return str(int(number))
    return format(float(number), "f").rstrip("0").rstrip(".")

def _default_font_size(metadata: dict):
    size = (
        ((metadata.get("properties") or {}).get("defaultFormat") or {})
        .get("textFormat") or {}
    ).get("fontSize")
    if isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size):
        return None
    return size

def _matrix_text(cell: dict) -> str:
    formula = ((cell.get("userEnteredValue") or {}).get("formulaValue"))
    if isinstance(formula, str) and formula.strip():
        formula = formula.strip()
        return formula if formula.startswith("=") else f"={formula}"
    number_type = ((cell.get("userEnteredFormat") or {}).get("numberFormat") or {}).get("type")
    effective = cell.get("effectiveValue") or {}
    if number_type in _DATE_TYPES:
        return str(cell.get("formattedValue") or "")
    if "numberValue" in effective:
        return _number_to_matrix(effective["numberValue"])
    if "stringValue" in effective:
        return str(effective["stringValue"])
    if "boolValue" in effective:
        return "TRUE" if effective["boolValue"] else "FALSE"
    if cell.get("formattedValue") is not None:
        return str(cell["formattedValue"])
    return ""


def _color_fallback(fmt: dict | None) -> dict | None:
    if not fmt:
        return None
    text = fmt.get("textFormat") or {}
    kept = {}
    foreground = {}
    if text.get("foregroundColor"):
        foreground["foregroundColor"] = text["foregroundColor"]
    if text.get("foregroundColorStyle"):
        foreground["foregroundColorStyle"] = text["foregroundColorStyle"]
    if foreground:
        kept["textFormat"] = foreground
    if fmt.get("backgroundColor"):
        kept["backgroundColor"] = fmt["backgroundColor"]
    if fmt.get("backgroundColorStyle"):
        kept["backgroundColorStyle"] = fmt["backgroundColorStyle"]
    return kept or None

def _merged_slave_positions(merges: list) -> set[tuple[int, int]]:
    slaves: set[tuple[int, int]] = set()
    for merge in merges or []:
        end_row = merge.get("endRowIndex")
        end_col = merge.get("endColumnIndex")
        if not isinstance(end_row, int) or not isinstance(end_col, int):
            continue
        start_row = merge.get("startRowIndex")
        start_col = merge.get("startColumnIndex")
        if not isinstance(start_row, int):
            start_row = 0
        if not isinstance(start_col, int):
            start_col = 0
        for row in range(start_row, end_row):
            for col in range(start_col, end_col):
                if row == start_row and col == start_col:
                    continue
                slaves.add((row, col))
    return slaves

def _grid_to_matrix_and_cells(grid: dict) -> tuple[list, list]:
    sheet = (grid.get("sheets") or [{}])[0]
    data = sheet.get("data") or [{}]
    block = data[0] if data else {}
    start_row = block.get("startRow") or 0
    start_col = block.get("startColumn") or 0
    slaves = _merged_slave_positions(sheet.get("merges") or [])
    matrix = [[] for _ in range(start_row)]
    cells = []
    for row_offset, row in enumerate(block.get("rowData") or []):
        values = row.get("values") or []
        row_index = start_row + row_offset
        row_text = [""] * start_col
        for col_offset, cell in enumerate(values):
            col_index = start_col + col_offset
            if (row_index, col_index) in slaves:
                row_text.append("")
                continue
            row_text.append(_matrix_text(cell))
            entered = cell.get("userEnteredFormat")
            item = {
                "row": row_index,
                "col": col_index,
                "userEnteredFormat": entered,
            }
            fallback = _color_fallback(cell.get("effectiveFormat"))
            if fallback:
                item["effectiveFormat"] = fallback
            cells.append(item)
        matrix.append(row_text)
    for row_index, col_index in slaves:
        while len(matrix) <= row_index:
            matrix.append([])
        while len(matrix[row_index]) <= col_index:
            matrix[row_index].append("")
        matrix[row_index][col_index] = ""
    return matrix, cells


def fetch_google_sheet(spreadsheet_id: str, access_token: str) -> tuple[str, list, list, int | float | None]:
    """Fetch the first sheet. Return (title, matrix, cells, default font size)."""
    meta_response = requests.get(
        f"{GOOGLE_SHEETS_URL}/{spreadsheet_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"fields":"properties.title,properties.defaultFormat.textFormat.fontSize,sheets.properties(title,sheetId)"},
        timeout=20,
    )
    meta_response.raise_for_status()
    metadata = meta_response.json()
    title = metadata.get("properties", {}).get("title") or "Imported Sheet"
    sheets = metadata.get("sheets", [])
    first_sheet = sheets[0]["properties"]["title"] if sheets else "Sheet1"
    first_properties = sheets[0]["properties"] if sheets else {}
    worksheet_name = first_properties.get("title") or "Sheet1"
    google_sheet_id = first_properties.get("sheetId")

    grid_response = requests.get(
        f"{GOOGLE_SHEETS_URL}/{spreadsheet_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "includeGridData": "true",
            "ranges": first_sheet,
            "fields": _GRID_FIELDS,
        },
        timeout=20,
    )
    grid_response.raise_for_status()
    default_font_size = _default_font_size(metadata)
    matrix, cells = _grid_to_matrix_and_cells(grid_response.json())
    return {
        "title": title,
        "matrix": matrix,
        "cells": cells,
        "default_font_size": default_font_size,
        "worksheet_name": worksheet_name,
        "google_sheet_id": google_sheet_id,
        "google_spreadsheet_id": spreadsheet_id,
    }

def _repeat_cell(sheet_id: int, item: dict) -> dict:
    fmt = item["userEnteredFormat"]
    fields = []
    text = fmt.get("textFormat") or {}
    for key in ("bold", "italic", "strikethrough", "fontFamily", "fontSize", "foregroundColor"):
        if key in text:
            fields.append(f"userEnteredFormat.textFormat.{key}")
    if "backgroundColor" in fmt:
        fields.append("userEnteredFormat.backgroundColor")
    if "numberFormat" in fmt:
        fields.append("userEnteredFormat.numberFormat")
    row = item["row"]
    column = item["column"]
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row,
                "endRowIndex": row + 1,
                "startColumnIndex": column,
                "endColumnIndex": column + 1,
            },
            "cell": {"userEnteredFormat": fmt},
            "fields": ",".join(fields),
        }
    }

# Excel export uses column width 16 and row height 18.
# Google sizes are pixels: 16 character-widths -> 117px, 18 points -> 24px.
EXCEL_COLUMN_WIDTH_PX = int(16 * 7 + 5)
EXCEL_ROW_HEIGHT_PX = int(18 * 96 / 72)

def _dimension_requests(sheet_id: int, row_count: int, column_count: int) -> list:
    def one(dimension: str, end: int, pixels: int) -> dict:
        return {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": dimension,
                    "startIndex": 0,
                    "endIndex": end,
                },
                "properties": {"pixelSize": pixels},
                "fields": "pixelSize",
            }
        }

    return [
        one("COLUMNS", column_count, EXCEL_COLUMN_WIDTH_PX),
        one("ROWS", row_count, EXCEL_ROW_HEIGHT_PX),
    ]

def export_to_google_sheet(title: str, matrix: list, access_token: str, formats: list | None = None) -> dict:
    """Create a new Google Sheet, write values, then apply the given formats."""
    create_response = requests.post(
        GOOGLE_SHEETS_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "properties": {"title": title or "Exported Sheet"},
            "sheets": [{"properties": {"title": "Sheet1"}}],
        },
        timeout=20,
    )
    create_response.raise_for_status()
    created = create_response.json()
    spreadsheet_id = created["spreadsheetId"]
    sheet_id = created["sheets"][0]["properties"]["sheetId"]

    if matrix:
        max_cols = max((len(row) for row in matrix), default=0)
        if max_cols > 0:
            range_notation = f"Sheet1!A1:{_col_letter(max_cols)}{len(matrix)}"
            update_response = requests.put(
                f"{GOOGLE_SHEETS_URL}/{spreadsheet_id}/values/{range_notation}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"valueInputOption": "USER_ENTERED"},
                json={"values": matrix},
                timeout=30,
            )
            update_response.raise_for_status()

    has_content = bool(formats) or any(cell != "" for row in (matrix or []) for cell in row)
    column_count = max((len(row) for row in matrix), default=0) if matrix else 0
    row_count = len(matrix) if matrix else 0

    requests_body = [_repeat_cell(sheet_id, item) for item in (formats or [])]
    if has_content and row_count > 0 and column_count > 0:
        requests_body.extend(_dimension_requests(sheet_id, row_count, column_count))


    for start in range(0, len(requests_body), 2000):
        batch_response = requests.post(
            f"{GOOGLE_SHEETS_URL}/{spreadsheet_id}:batchUpdate",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"requests": requests_body[start:start + 2000]},
            timeout=30,
        )
        batch_response.raise_for_status()

    return {
        "spreadsheet_id": spreadsheet_id,
        "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
    }
