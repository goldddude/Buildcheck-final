from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


AREA_LABEL_RE = re.compile(
    r"(?P<label>R\.?\s*C\.?\s*A|BUA|BUILT\s*UP\s*AREA|CARPET\s*AREA|BAL/?UTILITY|BALCONY|UTILITY)"
    r"\s*[-:=]\s*(?P<area>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

DIMENSION_RE = re.compile(
    r"(?P<label>[A-Z][A-Z ./'&-]{1,30})?\s*"
    r"(?P<width>\d{3,5})\s*[Xx]\s*(?P<height>\d{3,5})"
)


@dataclass
class AreaItem:
    label: str
    area_m2: float
    source: str
    confidence: str = "medium"


@dataclass
class AnalysisResult:
    filename: str
    file_type: str
    total_area_m2: float = 0.0
    method: str = ""
    items: list[AreaItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_text_preview: str = ""

    @property
    def total_area_ft2(self) -> float:
        return self.total_area_m2 * 10.7639104167


def analyze_file(path: str | Path) -> AnalysisResult:
    drawing = Path(path)
    suffix = drawing.suffix.lower()

    if suffix == ".pdf":
        return analyze_pdf(drawing)
    if suffix == ".dxf":
        return analyze_dxf(drawing)
    if suffix == ".dwg":
        return analyze_dwg(drawing)

    return AnalysisResult(
        filename=drawing.name,
        file_type=suffix.lstrip(".").upper() or "Unknown",
        method="Unsupported file type",
        warnings=["Upload a PDF, DXF, or DWG drawing."],
    )


def analyze_pdf(path: Path) -> AnalysisResult:
    text = _extract_pdf_text(path)
    result = AnalysisResult(
        filename=path.name,
        file_type="PDF",
        method="Text extraction from drawing annotations",
        raw_text_preview=_compact_text(text)[:1200],
    )

    area_items = _extract_area_labels(text)
    if area_items:
        result.items.extend(area_items)
        result.total_area_m2 = sum(item.area_m2 for item in area_items)
        result.warnings.append(
            "Total is based on explicit area labels found in the drawing, such as RCA, balcony, utility, or built-up area annotations."
        )
        return result

    dimension_items = _extract_dimension_areas(text)
    result.items.extend(dimension_items)
    result.total_area_m2 = sum(item.area_m2 for item in dimension_items)
    result.warnings.append(
        "No explicit area labels were found, so the estimate uses room dimension text. This may miss irregular walls, ducts, voids, and duplicate labels."
    )
    return result


def _extract_pdf_text(path: Path) -> str:
    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text(x_tolerance=1, y_tolerance=3) or "" for page in pdf.pages)
    except Exception:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_area_labels(text: str) -> list[AreaItem]:
    items: list[AreaItem] = []
    for match in AREA_LABEL_RE.finditer(text):
        label = re.sub(r"\s+", " ", match.group("label").upper()).strip()
        items.append(
            AreaItem(
                label=label,
                area_m2=float(match.group("area")),
                source=match.group(0).strip(),
                confidence="high",
            )
        )
    return items


def _extract_dimension_areas(text: str) -> list[AreaItem]:
    items: list[AreaItem] = []
    for match in DIMENSION_RE.finditer(text):
        width_mm = float(match.group("width"))
        height_mm = float(match.group("height"))
        area_m2 = width_mm * height_mm / 1_000_000
        label = (match.group("label") or "Room/space").strip(" .-\n\t")
        if 0.2 <= area_m2 <= 500:
            items.append(
                AreaItem(
                    label=label.title(),
                    area_m2=area_m2,
                    source=f"{int(width_mm)} x {int(height_mm)} mm",
                    confidence="medium",
                )
            )
    return _dedupe_items(items)


def _dedupe_items(items: Iterable[AreaItem]) -> list[AreaItem]:
    seen: set[tuple[str, str, int]] = set()
    unique: list[AreaItem] = []
    for item in items:
        key = (item.label, item.source, round(item.area_m2 * 100))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def analyze_dxf(path: Path) -> AnalysisResult:
    pairs = _read_dxf_pairs(path)
    unit_label, unit_to_m2 = _dxf_area_unit_scale(pairs)
    result = AnalysisResult(
        filename=path.name,
        file_type="DXF",
        method=f"Geometric measurement of closed DXF entities ({unit_label})",
    )

    shapes = _extract_lwpolyline_shapes(pairs) + _extract_classic_polyline_shapes(pairs)
    circles = _extract_circle_shapes(pairs)
    selected_shapes = _select_area_shapes(shapes)

    for index, shape in enumerate(selected_shapes, start=1):
        area = shape["area"] * unit_to_m2
        if area <= 0:
            continue
        result.items.append(
            AreaItem(
                label=f"{shape['layer'] or 'Closed polyline'} {index}",
                area_m2=area,
                source=f"{shape['vertices']} vertices on layer {shape['layer'] or '0'}",
                confidence="high" if _is_preferred_area_layer(shape["layer"]) else "medium",
            )
        )

    if not selected_shapes and not shapes:
        for index, circle in enumerate(circles, start=1):
            radius_m = circle["radius"] * math.sqrt(unit_to_m2)
            result.items.append(
                AreaItem(
                    label=f"{circle['layer'] or 'Circle'} {index}",
                    area_m2=math.pi * radius_m * radius_m,
                    source=f"radius {circle['radius']:g} on layer {circle['layer'] or '0'}",
                    confidence="medium",
                )
            )

    result.total_area_m2 = sum(item.area_m2 for item in result.items)
    if not shapes and not circles:
        result.warnings.append(
            "No closed DXF polylines or circles were found. Ensure area boundaries are closed and drawn at real-world scale."
        )
    elif selected_shapes:
        layers = sorted({shape["layer"] or "0" for shape in selected_shapes})
        result.warnings.append(
            "DXF calculation used area-specific layers only: " + ", ".join(layers[:8]) + ("..." if len(layers) > 8 else "")
        )
        result.warnings.append(
            f"The drawing unit was detected as {unit_label}. Areas were converted to square metres before summing."
        )
    else:
        result.warnings.append(
            f"No RERA/RCA/carpet/built-up area layer was found. The drawing unit was detected as {unit_label}; only circle entities were reported."
        )
    return result


def _dxf_area_unit_scale(pairs: list[tuple[str, str]]) -> tuple[str, float]:
    units = {
        "0": ("unitless", 1.0),
        "1": ("inches", 0.00064516),
        "2": ("feet", 0.09290304),
        "4": ("millimetres", 0.000001),
        "5": ("centimetres", 0.0001),
        "6": ("metres", 1.0),
    }
    for index, (_, value) in enumerate(pairs):
        if value == "$INSUNITS" and index + 1 < len(pairs):
            code = pairs[index + 1][1]
            return units.get(code, (f"INSUNITS {code}", 1.0))
    return "unknown units", 1.0


def _select_area_shapes(shapes: list[dict]) -> list[dict]:
    preferred = [shape for shape in shapes if _is_preferred_area_layer(shape["layer"])]
    if preferred:
        return preferred
    if len(shapes) <= 25:
        return shapes
    return []


def _is_preferred_area_layer(layer: str) -> bool:
    normalized = re.sub(r"[^A-Z0-9.]+", " ", layer.upper())
    return bool(re.search(r"\b(RERA|R\.?\s*C\.?\s*A|CARPET|BUILT|BUA)\b", normalized))


def analyze_dwg(path: Path) -> AnalysisResult:
    version_code = _read_dwg_version_code(path)
    version_name = _dwg_version_name(version_code)
    result = AnalysisResult(
        filename=path.name,
        file_type=f"DWG ({version_name})" if version_name else "DWG",
        method="DWG conversion required before area measurement",
    )
    result.warnings.extend(
        [
            f"This file is a binary AutoCAD DWG{f' with header {version_code}' if version_code else ''}. BuildCheck cannot safely measure its geometry until it is converted to DXF.",
            "Convert the drawing to DXF from AutoCAD, DraftSight, BricsCAD, or ODA File Converter, then upload the DXF here.",
            "Once converted, make sure the built-up-area boundaries are closed polylines. Closed DXF boundaries can be measured with high confidence.",
        ]
    )
    return result


def _read_dwg_version_code(path: Path) -> str:
    try:
        header = path.read_bytes()[:6].decode("latin1", errors="ignore")
    except OSError:
        return ""
    return header if header.startswith("AC") else ""


def _dwg_version_name(code: str) -> str:
    versions = {
        "AC1009": "AutoCAD R12",
        "AC1012": "AutoCAD R13",
        "AC1014": "AutoCAD R14",
        "AC1015": "AutoCAD 2000-2002",
        "AC1018": "AutoCAD 2004-2006",
        "AC1021": "AutoCAD 2007-2009",
        "AC1024": "AutoCAD 2010-2012",
        "AC1027": "AutoCAD 2013-2017",
        "AC1032": "AutoCAD 2018-2026",
    }
    return versions.get(code, code)


def _read_dxf_pairs(path: Path) -> list[tuple[str, str]]:
    lines = path.read_text(errors="ignore").splitlines()
    pairs: list[tuple[str, str]] = []
    for index in range(0, len(lines) - 1, 2):
        pairs.append((lines[index].strip(), lines[index + 1].strip()))
    return pairs


def _extract_lwpolyline_shapes(pairs: list[tuple[str, str]]) -> list[dict]:
    shapes: list[dict] = []
    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == "0" and value == "LWPOLYLINE":
            layer = ""
            points: list[tuple[float, float]] = []
            closed = False
            pending_x: float | None = None
            i += 1
            while i < len(pairs) and not (pairs[i][0] == "0" and pairs[i][1] in {"LWPOLYLINE", "POLYLINE", "CIRCLE", "ENDSEC", "EOF"}):
                code, value = pairs[i]
                if code == "8":
                    layer = value
                elif code == "70":
                    closed = bool(int(float(value)) & 1)
                elif code == "10":
                    pending_x = float(value)
                elif code == "20" and pending_x is not None:
                    points.append((pending_x, float(value)))
                    pending_x = None
                i += 1
            area = abs(_polygon_area(points)) if closed and len(points) >= 3 else 0
            if area > 0:
                shapes.append({"layer": layer, "vertices": len(points), "area": area})
            continue
        i += 1
    return shapes


def _extract_classic_polyline_shapes(pairs: list[tuple[str, str]]) -> list[dict]:
    shapes: list[dict] = []
    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == "0" and value == "POLYLINE":
            layer = ""
            closed = False
            points: list[tuple[float, float]] = []
            i += 1
            while i < len(pairs):
                code, value = pairs[i]
                if code == "8":
                    layer = value
                elif code == "70":
                    closed = bool(int(float(value)) & 1)
                elif code == "0" and value == "VERTEX":
                    vertex, i = _read_vertex(pairs, i + 1)
                    if vertex:
                        points.append(vertex)
                    continue
                elif code == "0" and value == "SEQEND":
                    break
                i += 1
            area = abs(_polygon_area(points)) if closed and len(points) >= 3 else 0
            if area > 0:
                shapes.append({"layer": layer, "vertices": len(points), "area": area})
        i += 1
    return shapes


def _extract_circle_shapes(pairs: list[tuple[str, str]]) -> list[dict]:
    circles: list[dict] = []
    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == "0" and value == "CIRCLE":
            layer = ""
            radius: float | None = None
            i += 1
            while i < len(pairs) and pairs[i][0] != "0":
                if pairs[i][0] == "8":
                    layer = pairs[i][1]
                elif pairs[i][0] == "40":
                    radius = float(pairs[i][1])
                i += 1
            if radius and radius > 0:
                circles.append({"layer": layer, "radius": radius})
            continue
        i += 1
    return circles


def _extract_lwpolylines(pairs: list[tuple[str, str]]) -> list[list[tuple[float, float]]]:
    polygons: list[list[tuple[float, float]]] = []
    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == "0" and value == "LWPOLYLINE":
            points: list[tuple[float, float]] = []
            closed = False
            pending_x: float | None = None
            i += 1
            while i < len(pairs) and not (pairs[i][0] == "0" and pairs[i][1] in {"LWPOLYLINE", "POLYLINE", "CIRCLE", "ENDSEC", "EOF"}):
                code, value = pairs[i]
                if code == "70":
                    closed = bool(int(float(value)) & 1)
                elif code == "10":
                    pending_x = float(value)
                elif code == "20" and pending_x is not None:
                    points.append((pending_x, float(value)))
                    pending_x = None
                i += 1
            if closed:
                polygons.append(points)
            continue
        i += 1
    return polygons


def _extract_classic_polylines(pairs: list[tuple[str, str]]) -> list[list[tuple[float, float]]]:
    polygons: list[list[tuple[float, float]]] = []
    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == "0" and value == "POLYLINE":
            closed = False
            points: list[tuple[float, float]] = []
            i += 1
            while i < len(pairs):
                code, value = pairs[i]
                if code == "70":
                    closed = bool(int(float(value)) & 1)
                elif code == "0" and value == "VERTEX":
                    vertex, i = _read_vertex(pairs, i + 1)
                    if vertex:
                        points.append(vertex)
                    continue
                elif code == "0" and value == "SEQEND":
                    break
                i += 1
            if closed:
                polygons.append(points)
        i += 1
    return polygons


def _read_vertex(pairs: list[tuple[str, str]], start: int) -> tuple[tuple[float, float] | None, int]:
    x: float | None = None
    y: float | None = None
    i = start
    while i < len(pairs) and pairs[i][0] != "0":
        code, value = pairs[i]
        if code == "10":
            x = float(value)
        elif code == "20":
            y = float(value)
        i += 1
    if x is None or y is None:
        return None, i
    return (x, y), i


def _extract_circles(pairs: list[tuple[str, str]]) -> list[float]:
    radii: list[float] = []
    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == "0" and value == "CIRCLE":
            radius: float | None = None
            i += 1
            while i < len(pairs) and pairs[i][0] != "0":
                if pairs[i][0] == "40":
                    radius = float(pairs[i][1])
                i += 1
            if radius and radius > 0:
                radii.append(radius)
            continue
        i += 1
    return radii


def _polygon_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
