"""Copy one imported Google tab into a new spreadsheet and patch safe edits.

The only request that uses the source spreadsheet id is sheets.copyTo.
Every later request uses the destination spreadsheet id.
"""

import requests
from .services import GOOGLE_SHEETS_URL, _col_letter
import json
import uuid

_BATCH_SIZE = 2000

_GOOGLE_FONT_FAMILY = {
    "Arial, sans-serif": "Arial",
    "Helvetica, Arial, sans-serif": "Helvetica",
    "Georgia, serif": "Georgia",
    '"Times New Roman", Times, serif': "Times New Roman",
    "monospace": "Consolas",
}

_CURRENCY_PREFIX = {
    "USD": "[$$-409]",
    "GBP": "[$£-809]",
    "EUR": "[$€-407]",
    "JPY": "[$¥-411]",
}

class GooglePatchExportError(Exception):
    """The copy could not be completed. The view may rebuild."""

class GoogleBlankTabCleanupError(Exception):
    """The copied tab is complete. The view must not rebuild."""

    def __init__(self, message, *, spreadsheet_id, url):
        super().__init__(message)
        self.spreadsheet_id = spreadsheet_id
        self.url = url

def export_copied_google_sheet(source, diff, title, access_token) -> dict:
    """Copy source.google_sheet_id into a new spreadsheet and apply a safe diff."""
    source_spreadsheet_id = _source_spreadsheet_id(source)
    source_sheet_id = _source_sheet_id(source)
    worksheet_name = _worksheet_name(source)
    _reject_unsafe_diff(diff)
    value_entries = []
    repeat_templates = []
    if diff.get("has_changes") is True:
        value_entries = _value_entries(diff)
        repeat_templates = _repeat_templates(diff)

    if value_entries and diff.get("structural_operations"):
        value_entries = _without_structural_formula_rewrites(source, diff, value_entries)

    destination_id, blank_sheet_id = _create_destination(title, access_token)
    copied_sheet_id, copied_title = _copy_source_tab(
        source_spreadsheet_id,
        source_sheet_id,
        destination_id,
        access_token,
    )
    try:
        _replay_structure(destination_id, copied_sheet_id, diff.get("structural_operations") or [], access_token,)

        if value_entries:
            _write_values(destination_id, copied_title, value_entries, access_token)
        if repeat_templates:
            _write_repeats(destination_id, copied_sheet_id, repeat_templates, access_token)
    except GooglePatchExportError as patch_error:
        _discard_copied_tab(destination_id, copied_sheet_id, access_token, patch_error)
        raise

    url = _spreadsheet_url(destination_id)
    try:
        _delete_sheet(destination_id, blank_sheet_id, access_token)
    except GooglePatchExportError as exc:
        raise GoogleBlankTabCleanupError(
            "blank tab delete failed",
            spreadsheet_id=destination_id,
            url=url,
        ) from exc
    if copied_title != worksheet_name:
        try:
            _rename_sheet(destination_id, copied_sheet_id, worksheet_name, access_token)
        except GooglePatchExportError as exc:
            raise GoogleBlankTabCleanupError(
                "copied tab rename failed",
                spreadsheet_id=destination_id,
                url=url,
            ) from exc
    return {"spreadsheet_id": destination_id, "url": url}

_MAX_XLSX_UPLOAD_BYTES = 5 * 1024 * 1024
_DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"

def export_converted_excel_sheet(source, diff, title, access_token) -> dict:
    """Convert the stored xlsx to a new Google spreadsheet and patch one worksheet."""
    worksheet_name = _worksheet_name(source)
    _reject_unsafe_diff(diff)
    content = _excel_file_bytes(source)
    value_entries = []
    repeat_templates = []
    if diff.get("has_changes") is True:
        value_entries = _value_entries(diff)
        repeat_templates = _repeat_templates(diff)
    if value_entries and diff.get("structural_operations"):
        value_entries = _without_structural_formula_rewrites(source, diff, value_entries)

    destination_id = _upload_xlsx_as_google_sheet(title, content, access_token)
    try:
        converted_sheet_id, converted_title = _converted_worksheet(
            destination_id,
            worksheet_name,
            access_token,
        )
        _replay_structure(
            destination_id,
            converted_sheet_id,
            diff.get("structural_operations") or [],
            access_token,
        )
        if value_entries:
            _write_values(destination_id, converted_title, value_entries, access_token)
        if repeat_templates:
            _write_repeats(destination_id, converted_sheet_id, repeat_templates, access_token)
    except GooglePatchExportError as patch_error:
        _delete_converted_spreadsheet(destination_id, access_token, patch_error)
        raise
    return {"spreadsheet_id": destination_id, "url": _spreadsheet_url(destination_id)}


def _excel_file_bytes(source):
    file_field = getattr(source, "file", None)
    if not file_field:
        raise GooglePatchExportError("missing excel file")
    try:
        with file_field.open("rb") as handle:
            content = handle.read()
    except Exception as exc:
        raise GooglePatchExportError("excel file could not be read") from exc
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise GooglePatchExportError("excel file could not be read")
    if len(content) > _MAX_XLSX_UPLOAD_BYTES:
        raise GooglePatchExportError("excel file is too large to convert")
    return bytes(content)


def _upload_xlsx_as_google_sheet(title, content, access_token):
    boundary = f"excel{uuid.uuid4().hex}"
    metadata = json.dumps({
        "name": title or "Exported Sheet",
        "mimeType": "application/vnd.google-apps.spreadsheet",
    })
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    response = requests.post(
        _DRIVE_UPLOAD_URL,
        params={"uploadType": "multipart", "fields": "id"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        data=body,
        timeout=60,
    )
    if response.status_code == 401:
        response.raise_for_status()
    if response.status_code >= 400:
        raise GooglePatchExportError("excel conversion failed")
    try:
        spreadsheet_id = response.json().get("id")
    except ValueError as exc:
        raise GooglePatchExportError("excel conversion failed") from exc
    if not isinstance(spreadsheet_id, str) or not spreadsheet_id.strip():
        raise GooglePatchExportError("excel conversion failed")
    return spreadsheet_id.strip()


def _converted_worksheet(spreadsheet_id, worksheet_name, access_token):
    response = _request(
        "get",
        f"{GOOGLE_SHEETS_URL}/{spreadsheet_id}",
        access_token,
        timeout=20,
        params={"fields": "sheets.properties(sheetId,title)"},
    )
    wanted = worksheet_name.strip()
    try:
        for sheet in response.json().get("sheets") or []:
            properties = sheet.get("properties") or {}
            title = properties.get("title")
            sheet_id = properties.get("sheetId")
            if not isinstance(title, str) or title.strip() != wanted:
                continue
            if isinstance(sheet_id, bool) or not isinstance(sheet_id, int) or sheet_id < 0:
                break
            return sheet_id, title.strip()
    except (ValueError, AttributeError) as exc:
        raise GooglePatchExportError("converted worksheet was not found") from exc
    raise GooglePatchExportError("converted worksheet was not found")


def _delete_converted_spreadsheet(spreadsheet_id, access_token, patch_error):
    try:
        _request(
            "delete",
            f"https://www.googleapis.com/drive/v3/files/{spreadsheet_id}",
            access_token,
            timeout=20,
        )
    except GooglePatchExportError as exc:
        raise GoogleBlankTabCleanupError(
            "converted spreadsheet delete failed",
            spreadsheet_id=spreadsheet_id,
            url=_spreadsheet_url(spreadsheet_id),
        ) from exc
    return patch_error



def _source_spreadsheet_id(source):
    spreadsheet_id = getattr(source, "google_spreadsheet_id", None)
    if not isinstance(spreadsheet_id, str) or not spreadsheet_id.strip():
        raise GooglePatchExportError("missing google spreadsheet id")
    return spreadsheet_id.strip()

def _source_sheet_id(source):
    sheet_id = getattr(source, "google_sheet_id", None)
    if isinstance(sheet_id, bool) or not isinstance(sheet_id, int) or sheet_id < 0:
        raise GooglePatchExportError("missing google sheet id")
    return sheet_id

def _worksheet_name(source):
    name = getattr(source, "worksheet_name", None)
    if not isinstance(name, str) or not name.strip():
        raise GooglePatchExportError("missing worksheet name")
    return name.strip()

def _reject_unsafe_diff(diff):
    if not isinstance(diff, dict) or diff.get("requires_rebuild") is not False:
        raise GooglePatchExportError("rebuild required")
    highlights = diff.get("highlights") or {}
    if not isinstance(highlights, dict):
        raise GooglePatchExportError("malformed diff")
    if highlights.get("rows") or highlights.get("columns"):
        raise GooglePatchExportError("row or column highlight change")

def _parse_coord(key):
    try:
        row_text, column_text = str(key).split(",", 1)
        row = int(row_text)
        column = int(column_text)
    except (TypeError, ValueError):
        raise GooglePatchExportError("malformed diff coordinate") from None
    if row < 0 or column < 0:
        raise GooglePatchExportError("malformed diff coordinate")
    return row, column

def _value_entries(diff):
    cells = diff.get("cells") or {}
    if not isinstance(cells, dict):
        raise GooglePatchExportError("malformed diff")
    entries = []
    for key, change in cells.items():
        row, column = _parse_coord(key)
        if not isinstance(change, dict) or "raw_input" not in change:
            raise GooglePatchExportError("malformed diff")
        raw = change.get("raw_input")
        if raw is not None and not isinstance(raw, str):
            raw = str(raw)
        entries.append((row, column, raw))
    return entries

def _repeat_templates(diff):
    formats = diff.get("formats") or {}
    highlights = (diff.get("highlights") or {}).get("cells") or {}
    if not isinstance(formats, dict) or not isinstance(highlights, dict):
        raise GooglePatchExportError("malformed diff")
    templates = []
    for key, changes in formats.items():
        row, column = _parse_coord(key)
        if not isinstance(changes, dict):
            raise GooglePatchExportError("malformed diff")
        templates.append((row, column, *_format_body(changes)))
    for key, color in highlights.items():
        row, column = _parse_coord(key)
        templates.append((row, column, *_highlight_body(color)))
    return templates

def _format_body(changes):
    text = {}
    fields = []
    user_format = {}
    for name, google_name in (
        ("bold", "bold"),
        ("italic", "italic"),
        ("strikethrough", "strikethrough"),
    ):
        if name in changes:
            text[google_name] = bool(changes[name])
            fields.append(f"userEnteredFormat.textFormat.{google_name}")
    if "font_family" in changes:
        fields.append("userEnteredFormat.textFormat.fontFamily")
        if changes["font_family"] is not None:
            text["fontFamily"] = _font_family(changes["font_family"])
    if "font_size" in changes:
        fields.append("userEnteredFormat.textFormat.fontSize")
        if changes["font_size"] is not None:
            text["fontSize"] = _font_size(changes["font_size"])
    if "text_color" in changes:
        fields.append("userEnteredFormat.textFormat.foregroundColor")
        fields.append("userEnteredFormat.textFormat.foregroundColorStyle")
        if changes["text_color"] is not None:
            text["foregroundColor"] = _google_color(changes["text_color"])
    if text:
        user_format["textFormat"] = text
    if "number_format" in changes:
        fields.append("userEnteredFormat.numberFormat")
        number_format = _google_number_format(changes["number_format"])
        if number_format is not None:
            user_format["numberFormat"] = number_format
    if not fields:
        raise GooglePatchExportError("malformed diff")
    return user_format, fields

def _highlight_body(color):
    fields = [
        "userEnteredFormat.backgroundColor",
        "userEnteredFormat.backgroundColorStyle",
    ]
    if color is None:
        return {}, fields
    return {"backgroundColor": _google_color(color)}, fields

def _font_family(value):
    if not isinstance(value, str):
        raise GooglePatchExportError("unsupported font family")
    mapped = _GOOGLE_FONT_FAMILY.get(value.strip())
    if not mapped:
        raise GooglePatchExportError("unsupported font family")
    return mapped

def _font_size(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GooglePatchExportError("unsupported font size")
    return value

def _google_color(value):
    if not isinstance(value, str):
        raise GooglePatchExportError("unsupported color")
    raw = value.strip()
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) != 6:
        raise GooglePatchExportError("unsupported color")
    try:
        parsed = int(raw, 16)
    except ValueError:
        raise GooglePatchExportError("unsupported color") from None
    return {
        "red": ((parsed >> 16) & 255) / 255,
        "green": ((parsed >> 8) & 255) / 255,
        "blue": (parsed & 255) / 255,
    }

def _decimal_places(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 2
    return max(0, min(10, int(value)))

def _number_pattern(places):
    if places == 0:
        return "#,##0"
    return "#,##0." + ("0" * places)

def _percent_pattern(places):
    if places == 0:
        return "0%"
    return "0." + ("0" * places) + "%"

def _google_number_format(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise GooglePatchExportError("unsupported number format")
    kind = value.get("type")
    if kind in (None, "", "GENERAL"):
        return None
    places = _decimal_places(value.get("decimal_places"))
    if kind == "NUMBER":
        return {"type": "NUMBER", "pattern": _number_pattern(places)}
    if kind == "PERCENT":
        return {"type": "PERCENT", "pattern": _percent_pattern(places)}
    if kind == "CURRENCY":
        code = str(value.get("currency_code") or "").upper()
        prefix = _CURRENCY_PREFIX.get(code)
        if not prefix:
            raise GooglePatchExportError("unsupported number format")
        return {"type": "CURRENCY", "pattern": prefix + _number_pattern(places)}
    raise GooglePatchExportError("unsupported number format")

def _request(method, url, access_token, *, timeout, **kwargs):
    try:
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout,
            **kwargs,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            raise
        raise GooglePatchExportError("google sheets request failed") from exc
    except requests.RequestException as exc:
        raise GooglePatchExportError("google sheets request failed") from exc
    return response

def _create_destination(title, access_token):
    response = _request(
        "post",
        GOOGLE_SHEETS_URL,
        access_token,
        timeout=20,
        json={
            "properties": {"title": title or "Exported Sheet"},
            "sheets": [{"properties": {"title": "Sheet1"}}],
        },
    )
    try:
        created = response.json()
        destination_id = created["spreadsheetId"]
        blank_sheet_id = created["sheets"][0]["properties"]["sheetId"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise GooglePatchExportError("destination creation failed") from exc
    if not destination_id or isinstance(blank_sheet_id, bool) or not isinstance(blank_sheet_id, int):
        raise GooglePatchExportError("destination creation failed")
    return destination_id, blank_sheet_id

def _copy_source_tab(source_spreadsheet_id, source_sheet_id, destination_id, access_token):
    response = _request(
        "post",
        f"{GOOGLE_SHEETS_URL}/{source_spreadsheet_id}/sheets/{source_sheet_id}:copyTo",
        access_token,
        timeout=30,
        json={"destinationSpreadsheetId": destination_id},
    )
    try:
        payload = response.json()
        copied_sheet_id = payload["sheetId"]
        copied_title = payload["title"]
    except (ValueError, KeyError, TypeError) as exc:
        raise GooglePatchExportError("copy failed") from exc
    if isinstance(copied_sheet_id, bool) or not isinstance(copied_sheet_id, int) or not copied_title:
        raise GooglePatchExportError("copy failed")
    return copied_sheet_id, copied_title

def _without_structural_formula_rewrites(source, diff, entries):
    """Drop formula patches that only repeat the structural rewrite.

    Google already adjusted those formulas while inserting or deleting.
    A formula the user edited after that still differs and is kept.
    """
    operations = diff.get("structural_operations") or []
    baseline = getattr(source, "baseline", None)
    if not operations or not isinstance(baseline, dict):
        return entries
    from spreadsheet.formula_rewrite import rewrite_formula_text

    cells = dict(baseline.get("cells") or {})
    for operation in operations:
        cells = _rewrite_baseline_formulas(cells, operation, rewrite_formula_text)
    automatic = {
        key: value.get("raw_input")
        for key, value in cells.items()
        if isinstance(value, dict) and isinstance(value.get("raw_input"), str)
    }
    return [
        (row, column, raw)
        for row, column, raw in entries
        if not (
            isinstance(raw, str)
            and raw.startswith("=")
            and automatic.get(f"{row},{column}") == raw
        )
    ]


def _rewrite_baseline_formulas(cells, operation, rewrite_formula_text):
    op_type = operation.get("op_type")
    anchor = operation.get("anchor_position")
    count = operation.get("count")
    if op_type not in ("ROW_INSERT", "ROW_DELETE", "COL_INSERT", "COL_DELETE"):
        return cells
    if isinstance(anchor, bool) or not isinstance(anchor, int):
        return cells
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        return cells
    delete = op_type.endswith("_DELETE")
    axis = "row" if op_type.startswith("ROW_") else "column"
    shifted = {}
    for key, value in cells.items():
        row_text, column_text = str(key).split(",", 1)
        row = int(row_text)
        column = int(column_text)
        if axis == "row":
            row = _shift_formula_index(row, anchor, count, delete)
        else:
            column = _shift_formula_index(column, anchor, count, delete)
        if row is None or column is None:
            continue
        if (
            isinstance(value, dict)
            and isinstance(value.get("raw_input"), str)
            and value["raw_input"].startswith("=")
        ):
            value = dict(value)
            value["raw_input"] = rewrite_formula_text(value["raw_input"], op_type, anchor, count)
        shifted[f"{row},{column}"] = value
    return shifted


def _shift_formula_index(index, anchor, count, delete):
    """Same coordinate rule as structure_replay._shift_index."""
    if delete:
        if anchor <= index < anchor + count:
            return None
        if index >= anchor + count:
            return index - count
        return index
    if index >= anchor:
        return index + count
    return index


def _replay_structure(destination_id, copied_sheet_id, operations, access_token):
    """Apply validated inserts and deletes to the copied destination tab."""
    if not operations:
        return
    row_count, column_count = _copied_grid_size(destination_id, copied_sheet_id, access_token)
    requests_body = []
    for operation in operations:
        row_count, column_count = _append_dimension_change(
            copied_sheet_id,
            operation,
            row_count,
            column_count,
            requests_body,
        )
    _request(
        "post",
        f"{GOOGLE_SHEETS_URL}/{destination_id}:batchUpdate",
        access_token,
        timeout=30,
        json={"requests": requests_body},
    )


def _copied_grid_size(destination_id, copied_sheet_id, access_token):
    response = _request(
        "get",
        f"{GOOGLE_SHEETS_URL}/{destination_id}",
        access_token,
        timeout=20,
        params={"fields": "sheets.properties(sheetId,gridProperties(rowCount,columnCount))"},
    )
    try:
        for sheet in response.json().get("sheets") or []:
            properties = sheet.get("properties") or {}
            if properties.get("sheetId") != copied_sheet_id:
                continue
            grid = properties.get("gridProperties") or {}
            row_count = grid.get("rowCount")
            column_count = grid.get("columnCount")
            if (
                isinstance(row_count, int)
                and row_count >= 1
                and isinstance(column_count, int)
                and column_count >= 1
            ):
                return row_count, column_count
    except (ValueError, AttributeError):
        pass
    raise GooglePatchExportError("copied sheet size is unavailable")


def _append_dimension_change(sheet_id, operation, row_count, column_count, requests_body):
    if not isinstance(operation, dict):
        raise GooglePatchExportError("bad structural operation")
    op_type = operation.get("op_type")
    anchor = operation.get("anchor_position")
    count = operation.get("count")
    if isinstance(anchor, bool) or not isinstance(anchor, int) or anchor < 0:
        raise GooglePatchExportError("bad structural anchor")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise GooglePatchExportError("bad structural count")
    if op_type in ("ROW_INSERT", "ROW_DELETE"):
        dimension = "ROWS"
        size = row_count
    elif op_type in ("COL_INSERT", "COL_DELETE"):
        dimension = "COLUMNS"
        size = column_count
    else:
        raise GooglePatchExportError("unsupported structural operation")

    start = anchor
    end = anchor + count
    if op_type.endswith("_DELETE"):
        if end > size or size - count < 1:
            raise GooglePatchExportError("structural delete is outside the sheet")
        requests_body.append({
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": dimension,
                    "startIndex": start,
                    "endIndex": end,
                }
            }
        })
        size -= count
    else:
        if start > size:
            requests_body.append(_grow_grid(sheet_id, dimension, start))
            size = start
        requests_body.append({
            "insertDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": dimension,
                    "startIndex": start,
                    "endIndex": end,
                },
                "inheritFromBefore": start > 0,
            }
        })
        size += count
        requests_body.append(_clear_inserted_format(sheet_id, dimension, start, end, row_count, column_count))
    if dimension == "ROWS":
        return size, column_count
    return row_count, size

def _clear_inserted_format(sheet_id, dimension, start, end, row_count, column_count):
    """Remove formatting Google copied onto the new rows or columns."""
    if dimension == "ROWS":
        cell_range = {
            "sheetId": sheet_id,
            "startRowIndex": start,
            "endRowIndex": end,
            "startColumnIndex": 0,
            "endColumnIndex": column_count,
        }
    else:
        cell_range = {
            "sheetId": sheet_id,
            "startRowIndex": 0,
            "endRowIndex": row_count,
            "startColumnIndex": start,
            "endColumnIndex": end,
        }
    return {
        "repeatCell": {
            "range": cell_range,
            "cell": {},
            "fields": "userEnteredFormat",
        }
    }


def _grow_grid(sheet_id, dimension, new_count):
    grid = {}
    if dimension == "ROWS":
        grid["rowCount"] = new_count
        fields = "gridProperties.rowCount"
    else:
        grid["columnCount"] = new_count
        fields = "gridProperties.columnCount"
    return {
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": grid},
            "fields": fields,
        }
    }


def _quote_title(title):
    return "'" + str(title).replace("'", "''") + "'"

def _write_values(destination_id, copied_title, entries, access_token):
    quoted = _quote_title(copied_title)
    data = [
        {
            "range": f"{quoted}!{_col_letter(column + 1)}{row + 1}",
            "values": [["" if raw is None else raw]],
        }
        for row, column, raw in entries
    ]
    for start in range(0, len(data), _BATCH_SIZE):
        _request(
            "post",
            f"{GOOGLE_SHEETS_URL}/{destination_id}/values:batchUpdate",
            access_token,
            timeout=30,
            json={
                "valueInputOption": "USER_ENTERED",
                "data": data[start:start + _BATCH_SIZE],
            },
        )

def _repeat_request(sheet_id, row, column, user_format, fields):
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row,
                "endRowIndex": row + 1,
                "startColumnIndex": column,
                "endColumnIndex": column + 1,
            },
            "cell": {"userEnteredFormat": user_format},
            "fields": ",".join(fields),
        }
    }

def _write_repeats(destination_id, copied_sheet_id, templates, access_token):
    requests_body = [
        _repeat_request(copied_sheet_id, row, column, user_format, fields)
        for row, column, user_format, fields in templates
    ]
    for start in range(0, len(requests_body), _BATCH_SIZE):
        _request(
            "post",
            f"{GOOGLE_SHEETS_URL}/{destination_id}:batchUpdate",
            access_token,
            timeout=30,
            json={"requests": requests_body[start:start + _BATCH_SIZE]},
        )

def _delete_sheet(destination_id, sheet_id, access_token):
    _request(
        "post",
        f"{GOOGLE_SHEETS_URL}/{destination_id}:batchUpdate",
        access_token,
        timeout=30,
        json={"requests": [{"deleteSheet": {"sheetId": sheet_id}}]},
    )

def _rename_sheet(destination_id, sheet_id, title, access_token):
    _request(
        "post",
        f"{GOOGLE_SHEETS_URL}/{destination_id}:batchUpdate",
        access_token,
        timeout=30,
        json={
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": sheet_id, "title": title},
                        "fields": "title",
                    }
                }
            ]
        },
    )

def _discard_copied_tab(destination_id, copied_sheet_id, access_token, patch_error):
    try:
        _delete_sheet(destination_id, copied_sheet_id, access_token)
    except GooglePatchExportError as cleanup_error:
        raise GooglePatchExportError("copied tab cleanup failed") from cleanup_error
    raise patch_error

def _spreadsheet_url(destination_id):
    return f"https://docs.google.com/spreadsheets/d/{destination_id}/edit"