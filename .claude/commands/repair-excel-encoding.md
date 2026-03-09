---
name: repair-excel-encoding
description: >
  Use when customer Excel file has encoding corruption, null bytes, _x0000_ markers,
  BOM artifacts, alternating empty rows, wrapping quotes, mojibake, HTML entities,
  or CSV-in-Excel single-column issues
---

# Repair Excel Encoding

## Overview

Detect and repair common Excel encoding corruption found in customer data migration files. This skill runs a single self-contained Python script that scans for 9 corruption patterns simultaneously, prints a diagnostic report, and writes a clean copy — never modifying the original file.

## When to Use

**Symptoms (any of these):**
- Cell values contain `_x0000_` markers or visible null bytes
- First cell starts with `ÿþ` (UTF-16 BOM) or `ï»¿` (UTF-8 BOM)
- Every other row is empty across all columns
- Values wrapped in literal `"double quotes"` that aren't part of the data
- Numbers stored as text (green triangle in Excel, `number_format = '@'`)
- Accented characters look wrong: `Ã©` instead of `é`, `Ã¼` instead of `ü`
- HTML entities visible in cells: `&amp;`, `&lt;`, `&#169;`
- Entire dataset in a single column with commas/tabs/pipes between values
- Leading apostrophes on numeric cells (`'12345`)

**When NOT to use:**
- File opens normally with correct data — no repair needed
- Corruption is in the data itself (wrong values, not encoding artifacts)
- File is password-protected or macro-enabled (.xlsm) — handle manually
- File is CSV — just re-import with correct encoding; this skill is for .xlsx

## Quick Reference

| # | Pattern | Detection Signature | Repair Action |
|---|---------|-------------------|---------------|
| 1 | UTF-16LE null bytes | `_x0000_` in cell values | Strip `_x0000_` and `\x00` |
| 2 | BOM artifacts | First cell starts with `ÿþ` or `ï»¿` | Strip BOM prefix |
| 3 | Alternating empty rows | >30% rows empty, evenly spaced | Delete empty rows |
| 4 | Wrapping double quotes | Cell starts AND ends with `"` | Strip outer quotes |
| 5 | Leading apostrophe | Cell starts with `'`, number_format=@ | Strip `'` prefix |
| 6 | All-text format | number_format=@ on numeric-looking data | Convert to int/float, set General |
| 7 | Mojibake (double-encoding) | `\xc3[\x80-\xbf]` byte pattern | Decode latin-1 → utf-8 |
| 8 | HTML entities | `&amp;`, `&lt;`, `&#NNN;` patterns | `html.unescape()` |
| 9 | CSV-in-Excel | Single used column with consistent delimiters | Split on detected delimiter |

## Implementation

Run this script against the customer file. It uses **openpyxl only** (no pandas dependency).

**Usage:** `/repair-excel-encoding /path/to/Customer File.xlsx`

The file path is: $ARGUMENTS

```python
#!/usr/bin/env python3
"""
Excel Encoding Repair Tool
Detects and repairs 9 common corruption patterns in customer Excel files.
Never modifies the original — writes to '{name} - CLEAN.xlsx'.
"""

import os
import re
import sys
import html
from pathlib import Path
from collections import Counter

try:
    from openpyxl import load_workbook
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import get_column_letter
except ImportError:
    print("ERROR: openpyxl not installed. Run: pip install openpyxl")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Pattern detection helpers
# ---------------------------------------------------------------------------

RE_NULL_MARKER = re.compile(r'_x0000_')
RE_RAW_NULL = re.compile(r'\x00')
RE_BOM_UTF16 = re.compile(r'^(\xff\xfe|\xfe\xff|ÿþ)')
RE_BOM_UTF8 = re.compile(r'^(ï»¿|\xef\xbb\xbf)')
RE_WRAPPING_QUOTES = re.compile(r'^"(.*)"$', re.DOTALL)
RE_LEADING_APOSTROPHE = re.compile(r"^'(.+)$")
RE_MOJIBAKE = re.compile(r'[\xc3][\x80-\xbf]')
RE_HTML_ENTITY = re.compile(r'&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);')
RE_NUMERIC = re.compile(r'^-?\d+\.?\d*$')
RE_INTEGER = re.compile(r'^-?\d+$')

DELIMITERS = [',', '\t', '|', ';']


def detect_cell(value, number_format):
    """Check a single cell value against all patterns. Returns set of pattern names."""
    if not isinstance(value, str):
        return set()

    found = set()

    if RE_NULL_MARKER.search(value) or RE_RAW_NULL.search(value):
        found.add('null_bytes')

    if RE_BOM_UTF16.match(value) or RE_BOM_UTF8.match(value):
        found.add('bom')

    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        inner = value[1:-1]
        if not inner.startswith('"'):
            found.add('wrapping_quotes')

    if number_format == '@' and RE_LEADING_APOSTROPHE.match(value):
        found.add('leading_apostrophe')

    if number_format == '@' and RE_NUMERIC.match(value):
        found.add('text_format')

    if RE_MOJIBAKE.search(value):
        try:
            value.encode('latin-1').decode('utf-8')
            found.add('mojibake')
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass

    if RE_HTML_ENTITY.search(value):
        found.add('html_entities')

    return found


def detect_empty_row_pattern(ws, max_row):
    """Check for alternating empty rows pattern."""
    if max_row < 10:
        return False, []

    empty_rows = []
    for row_idx in range(1, max_row + 1):
        is_empty = all(
            ws.cell(row=row_idx, column=c).value is None
            for c in range(1, ws.max_column + 1)
        )
        if is_empty:
            empty_rows.append(row_idx)

    if not empty_rows:
        return False, []

    empty_pct = len(empty_rows) / max_row
    if empty_pct < 0.30:
        return False, []

    # Check for even spacing (alternating pattern)
    if len(empty_rows) >= 3:
        gaps = [empty_rows[i+1] - empty_rows[i] for i in range(min(20, len(empty_rows)-1))]
        gap_counts = Counter(gaps)
        most_common_gap, count = gap_counts.most_common(1)[0]
        if most_common_gap == 2 and count >= len(gaps) * 0.7:
            return True, empty_rows

    return False, empty_rows


def detect_csv_in_excel(ws, max_row, max_col):
    """Check if data is crammed into a single column with delimiters."""
    if max_col > 3:
        return False, None

    used_col = None
    for c in range(1, max_col + 1):
        has_data = any(ws.cell(row=r, column=c).value is not None for r in range(1, min(20, max_row + 1)))
        if has_data:
            if used_col is not None:
                return False, None
            used_col = c

    if used_col is None:
        return False, None

    # Sample cells to detect delimiter
    sample_values = []
    for r in range(2, min(20, max_row + 1)):
        val = ws.cell(row=r, column=used_col).value
        if isinstance(val, str) and len(val) > 5:
            sample_values.append(val)

    if len(sample_values) < 3:
        return False, None

    for delim in DELIMITERS:
        counts = [v.count(delim) for v in sample_values]
        if all(c > 0 for c in counts):
            avg = sum(counts) / len(counts)
            if all(abs(c - avg) <= 1 for c in counts):
                return True, delim

    return False, None


# ---------------------------------------------------------------------------
# Repair functions
# ---------------------------------------------------------------------------

def repair_cell(value, number_format, patterns):
    """Apply repairs to a single cell value based on detected patterns."""
    if not isinstance(value, str):
        return value, number_format

    new_fmt = number_format

    if 'null_bytes' in patterns:
        value = RE_NULL_MARKER.sub('', value)
        value = RE_RAW_NULL.sub('', value)

    if 'bom' in patterns:
        value = RE_BOM_UTF16.sub('', value)
        value = RE_BOM_UTF8.sub('', value)

    if 'wrapping_quotes' in patterns:
        m = RE_WRAPPING_QUOTES.match(value)
        if m:
            value = m.group(1)

    if 'leading_apostrophe' in patterns:
        m = RE_LEADING_APOSTROPHE.match(value)
        if m:
            value = m.group(1)

    if 'mojibake' in patterns:
        try:
            value = value.encode('latin-1').decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass

    if 'html_entities' in patterns:
        value = html.unescape(value)

    if 'text_format' in patterns:
        if RE_INTEGER.match(value):
            try:
                value = int(value)
                new_fmt = 'General'
            except ValueError:
                pass
        elif RE_NUMERIC.match(value):
            try:
                value = float(value)
                new_fmt = 'General'
            except ValueError:
                pass

    return value, new_fmt


# ---------------------------------------------------------------------------
# Main workflow: DETECT → REPORT → REPAIR
# ---------------------------------------------------------------------------

def process_workbook(file_path):
    path = Path(file_path)
    if not path.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)

    if path.suffix.lower() not in ('.xlsx', '.xlsm', '.xltx'):
        print(f"ERROR: Not an Excel file: {path.suffix}")
        sys.exit(1)

    print(f"Loading: {path.name}")
    wb = load_workbook(str(path))
    all_issues = {}
    sheet_details = {}

    # ── PHASE 1: DETECT ──────────────────────────────────────────────
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        max_row = ws.max_row or 0
        max_col = ws.max_column or 0

        if max_row == 0:
            continue

        print(f"  Scanning sheet '{sheet_name}' ({max_row:,} rows x {max_col} cols)...")

        pattern_counts = Counter()
        cell_patterns = {}

        # Single-pass cell scan
        for row_idx in range(1, max_row + 1):
            for col_idx in range(1, max_col + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                if cell.value is None:
                    continue
                found = detect_cell(cell.value, cell.number_format)
                if found:
                    cell_patterns[(row_idx, col_idx)] = found
                    for p in found:
                        pattern_counts[p] += 1

        # Structural patterns
        has_empty_rows, empty_rows = detect_empty_row_pattern(ws, max_row)
        if has_empty_rows:
            pattern_counts['empty_rows'] = len(empty_rows)

        is_csv, csv_delim = detect_csv_in_excel(ws, max_row, max_col)
        if is_csv:
            pattern_counts['csv_in_excel'] = max_row

        sheet_details[sheet_name] = {
            'pattern_counts': pattern_counts,
            'cell_patterns': cell_patterns,
            'empty_rows': empty_rows if has_empty_rows else [],
            'is_csv': is_csv,
            'csv_delim': csv_delim,
            'max_row': max_row,
            'max_col': max_col,
        }

        for p, count in pattern_counts.items():
            all_issues[p] = all_issues.get(p, 0) + count

    # ── PHASE 2: REPORT ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DIAGNOSIS REPORT")
    print("=" * 60)

    if not all_issues:
        print("  No corruption patterns detected. File appears clean.")
        return

    pattern_labels = {
        'null_bytes':          '1. UTF-16LE null bytes (_x0000_)',
        'bom':                 '2. BOM artifacts',
        'empty_rows':          '3. Alternating empty rows',
        'wrapping_quotes':     '4. Wrapping double quotes',
        'leading_apostrophe':  '5. Leading apostrophe',
        'text_format':         '6. All-text format (numbers as text)',
        'mojibake':            '7. Mojibake (double-encoding)',
        'html_entities':       '8. HTML entities',
        'csv_in_excel':        '9. CSV-in-Excel (single column)',
    }

    for sheet_name, details in sheet_details.items():
        pc = details['pattern_counts']
        if not pc:
            continue
        print(f"\n  Sheet: '{sheet_name}'")
        for key, label in pattern_labels.items():
            if key in pc:
                print(f"    {label}: {pc[key]:,} cells")

    print(f"\n  TOTAL issues across all sheets:")
    for key, label in pattern_labels.items():
        if key in all_issues:
            print(f"    {label}: {all_issues[key]:,}")

    # ── PHASE 3: REPAIR ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("REPAIRING...")
    print("=" * 60)

    for sheet_name in wb.sheetnames:
        if sheet_name not in sheet_details:
            continue

        details = sheet_details[sheet_name]
        ws = wb[sheet_name]
        repaired = 0

        # Handle CSV-in-Excel first (restructures the sheet)
        if details['is_csv']:
            delim = details['csv_delim']
            print(f"  Sheet '{sheet_name}': Splitting CSV (delimiter='{repr(delim)}')...")

            # Read all data from the single column
            used_col = 1
            for c in range(1, details['max_col'] + 1):
                if ws.cell(row=1, column=c).value is not None:
                    used_col = c
                    break

            rows_data = []
            for r in range(1, details['max_row'] + 1):
                val = ws.cell(row=r, column=used_col).value
                if isinstance(val, str):
                    rows_data.append(val.split(delim))
                elif val is not None:
                    rows_data.append([str(val)])
                else:
                    rows_data.append([])

            # Clear sheet and rewrite
            for r in range(1, details['max_row'] + 1):
                for c in range(1, details['max_col'] + 1):
                    ws.cell(row=r, column=c).value = None

            for r_idx, row_vals in enumerate(rows_data, 1):
                for c_idx, val in enumerate(row_vals, 1):
                    ws.cell(row=r_idx, column=c_idx).value = val.strip()
                    repaired += 1

            # Update max_col for formatting
            if rows_data:
                details['max_col'] = max(len(r) for r in rows_data)

            print(f"    Split into {details['max_col']} columns, {len(rows_data)} rows")

        # Remove alternating empty rows (delete from bottom up to preserve indices)
        if details['empty_rows']:
            print(f"  Sheet '{sheet_name}': Removing {len(details['empty_rows']):,} empty rows...")
            for row_idx in reversed(details['empty_rows']):
                ws.delete_rows(row_idx, 1)
            repaired += len(details['empty_rows'])

        # Cell-level repairs
        cell_patterns = details['cell_patterns']
        if cell_patterns:
            # Collect all pattern types found across cells
            all_cell_patterns = set()
            for patterns in cell_patterns.values():
                all_cell_patterns.update(patterns)

            # Re-scan and repair (row indices may have shifted from empty row deletion)
            current_max_row = ws.max_row or 0
            current_max_col = ws.max_column or 0
            for row_idx in range(1, current_max_row + 1):
                for col_idx in range(1, current_max_col + 1):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    if cell.value is None:
                        continue
                    found = detect_cell(cell.value, cell.number_format)
                    if found:
                        new_val, new_fmt = repair_cell(cell.value, cell.number_format, found)
                        if new_val != cell.value or new_fmt != cell.number_format:
                            cell.value = new_val
                            cell.number_format = new_fmt
                            repaired += 1

        # Bold header row + auto-fit columns
        current_max_col = ws.max_column or 0
        for col_idx in range(1, current_max_col + 1):
            header_cell = ws.cell(row=1, column=col_idx)
            if header_cell.value is not None:
                header_cell.font = Font(bold=True)

            # Auto-fit: sample first 50 rows for width
            max_len = 0
            for r in range(1, min(51, (ws.max_row or 0) + 1)):
                val = ws.cell(row=r, column=col_idx).value
                if val is not None:
                    max_len = max(max_len, len(str(val)))
            if max_len > 0:
                col_letter = get_column_letter(col_idx)
                ws.column_dimensions[col_letter].width = min(max_len + 2, 60)

        print(f"  Sheet '{sheet_name}': {repaired:,} repairs applied")

    # Write output
    stem = path.stem
    out_path = path.parent / f"{stem} - CLEAN.xlsx"
    wb.save(str(out_path))
    print(f"\nSaved: {out_path}")
    print("Original file unchanged.")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python repair_excel.py <file.xlsx>")
        sys.exit(1)
    process_workbook(sys.argv[1])
```

Save the above script to a temp file and run it against the target Excel file:

```bash
# Save script to temp file
cat > /tmp/repair_excel_encoding.py << 'SCRIPT_EOF'
<paste the Python block above>
SCRIPT_EOF

# Run it
python3 /tmp/repair_excel_encoding.py "$ARGUMENTS"
```

**Alternatively**, run the script inline — copy the entire Python block into a bash heredoc:

```bash
python3 - "$ARGUMENTS" << 'PYEOF'
# ... paste full script here ...
PYEOF
```

After running, verify the output:
1. Open `{name} - CLEAN.xlsx` and spot-check a few cells
2. Compare row counts between original and clean file
3. Check that no data was lost (column count should match or increase for CSV-in-Excel)

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Running on `.xls` (old binary format) | Convert to `.xlsx` first via Excel or LibreOffice |
| Mojibake false positive on legitimate accented text | Check if `encode('latin-1').decode('utf-8')` succeeds — if it raises, it's real accented text |
| Empty row deletion shifts formula references | This tool is for data files, not formula-heavy workbooks |
| CSV-in-Excel delimiter detection picks wrong character | Override: modify the `DELIMITERS` list order or hardcode the delimiter |
| Text-to-number converts IDs that should stay text | If ZIP codes or IDs start with 0, they'll lose the leading zero — review after repair |
| Original file modified | Never happens — script always writes to `- CLEAN.xlsx` |
