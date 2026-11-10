"""CSV / Markdown export of the MAC table, confined to the config directory."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

EXTENSIONS = {".csv": "csv", ".md": "markdown"}

CSV_COLUMNS = (
    "mac",
    "ip",
    "hostname",
    "device_name",
    "integration",
    "manufacturer",
    "model",
    "confidence",
    "sources",
    "label",
    "tracker",
    "online",
    "random",
    "mac_type",
    "first_seen",
    "last_seen",
    "last_ips",
    "device_id",
    "entities",
)


class ExportPathError(ValueError):
    """The requested export path is not allowed."""

    def __init__(self, reason: str) -> None:
        """Store a translation key as reason."""
        super().__init__(reason)
        self.reason = reason


def resolve_export_path(config_dir: str, path: str, fmt: str | None) -> tuple[Path, str]:
    """Return the resolved target and format, or raise :class:`ExportPathError`.

    Rules: inside the config directory (after resolving ``..`` and symlinks),
    no hidden path component (``.storage``...), extension ``.csv`` or ``.md``
    matching the requested format, existing parent directory.
    """
    base = Path(config_dir).resolve()
    raw = Path(path.strip())
    candidate = raw if raw.is_absolute() else base / raw
    target = candidate.resolve()
    if target == base or not target.is_relative_to(base):
        raise ExportPathError("path_outside_config")
    relative = target.relative_to(base)
    if any(part.startswith(".") for part in relative.parts):
        raise ExportPathError("path_hidden")
    detected = EXTENSIONS.get(target.suffix.lower())
    if detected is None:
        raise ExportPathError("path_extension")
    if fmt is not None and fmt != detected:
        raise ExportPathError("path_extension")
    if not target.parent.is_dir():
        raise ExportPathError("path_no_directory")
    if target.exists() and not target.is_file():
        raise ExportPathError("path_outside_config")
    return target, detected


def _cell(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value)


def render_csv(rows: list[dict[str, Any]]) -> str:
    """Render rows as CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([_cell(row, key) for key in CSV_COLUMNS])
    return buffer.getvalue()


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ") or "-"


def render_markdown(rows: list[dict[str, Any]], summary: dict[str, Any], generated_at: str) -> str:
    """Render rows as a Markdown document."""
    lines = [
        "# MAC Registry",
        "",
        f"Generated: {generated_at}  ",
        f"Known: {summary.get('known', 0)} · matched: {summary.get('matched', 0)} · "
        f"orphans: {summary.get('orphans', 0)} · "
        f"HA devices never seen by the router: {summary.get('unseen_devices', 0)}",
        "",
        "| MAC | IP | Hostname | Device | Integration | Confidence | Tracker | Entities |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        device = row.get("device_name") or row.get("label") or ""
        entities = row.get("entities") or []
        shown = ", ".join(entities[:3]) + (f" (+{len(entities) - 3})" if len(entities) > 3 else "")
        flags = " (random)" if row.get("random") else ""
        lines.append(
            "| "
            + " | ".join(
                _md(cell)
                for cell in (
                    f"{row['mac']}{flags}",
                    _cell(row, "ip"),
                    _cell(row, "hostname"),
                    device,
                    _cell(row, "integration"),
                    _cell(row, "confidence"),
                    _cell(row, "tracker"),
                    shown,
                )
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def write_text(target: Path, content: str) -> None:
    """Write a file (executor)."""
    target.write_text(content, encoding="utf-8")
