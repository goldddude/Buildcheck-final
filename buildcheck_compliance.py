from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from buildcheck_area import AnalysisResult, AreaItem


ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT / "data" / "regulations_maharashtra.json"


ROOM_MINIMUMS_M2 = {
    "living": 9.5,
    "bedroom": 9.5,
    "kitchen": 5.0,
    "dining": 5.0,
    "toilet": 1.1,
    "bathroom": 1.8,
    "utility": 1.0,
    "balcony": 1.0,
    "passage": 1.0,
    "staircase": 5.0,
    "lift": 2.0,
    "parking": 12.5,
    "store": 1.5,
    "terrace": 1.0,
}


@dataclass
class ProjectInfo:
    project_name: str
    state: str
    authority_id: str
    building_type: str
    selected_regulations: list[str]


@dataclass
class ComplianceCheck:
    scope: str
    extracted: str
    required: str
    status: str
    rule_number: str
    clause_number: str
    suggested_fix: str


@dataclass
class ComplianceReport:
    project: ProjectInfo
    authority_name: str
    regulation_names: list[str]
    loaded_categories: list[str]
    area_result: AnalysisResult
    room_checks: list[ComplianceCheck] = field(default_factory=list)
    building_checks: list[ComplianceCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def compliance_score(self) -> int:
        checks = self.room_checks + self.building_checks
        if not checks:
            return 0
        passed = sum(1 for check in checks if check.status == "Pass")
        review = sum(1 for check in checks if check.status == "Review")
        return round(((passed + review * 0.5) / len(checks)) * 100)


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def get_state(registry: dict, state_id: str) -> dict:
    return next(state for state in registry["states"] if state["id"] == state_id)


def get_authority(registry: dict, state_id: str, authority_id: str) -> dict:
    state = get_state(registry, state_id)
    return next(authority for authority in state["authorities"] if authority["id"] == authority_id)


def regulation_names(registry: dict, regulation_ids: list[str]) -> list[str]:
    return [registry["regulations"][reg_id]["name"] for reg_id in regulation_ids if reg_id in registry["regulations"]]


def loaded_categories(registry: dict, regulation_ids: list[str]) -> list[str]:
    categories: set[str] = set()
    for reg_id in regulation_ids:
        regulation = registry["regulations"].get(reg_id, {})
        categories.update(regulation.get("categories", []))
    return sorted(categories)


def build_report(area_result: AnalysisResult, project: ProjectInfo, registry: dict) -> ComplianceReport:
    authority = get_authority(registry, project.state, project.authority_id)
    report = ComplianceReport(
        project=project,
        authority_name=authority["name"],
        regulation_names=regulation_names(registry, project.selected_regulations),
        loaded_categories=loaded_categories(registry, project.selected_regulations),
        area_result=area_result,
    )
    report.room_checks.extend(_room_checks(area_result.items, project.selected_regulations))
    report.building_checks.extend(_building_checks(area_result))
    report.warnings.extend(area_result.warnings)
    report.warnings.append(
        "Rule references are a seed clause database. Replace the seed thresholds with verified clause records before using this for statutory approval."
    )
    return report


def _room_checks(items: list[AreaItem], selected_regulations: list[str]) -> list[ComplianceCheck]:
    checks: list[ComplianceCheck] = []
    primary_regulation = _primary_regulation(selected_regulations)
    for item in items:
        room_type = _classify_room(item.label)
        if not room_type:
            continue
        required = ROOM_MINIMUMS_M2[room_type]
        status = "Pass" if item.area_m2 >= required else "Fail"
        checks.append(
            ComplianceCheck(
                scope=item.label,
                extracted=f"{item.area_m2:.2f} m2",
                required=f">= {required:.2f} m2",
                status=status,
                rule_number=primary_regulation,
                clause_number=f"{primary_regulation}-ROOM-{room_type.upper()}-MIN",
                suggested_fix="No correction needed." if status == "Pass" else f"Increase {item.label} to at least {required:.2f} m2 or revise room classification.",
            )
        )
    if not checks:
        checks.append(
            ComplianceCheck(
                scope="Room detection",
                extracted="No classified rooms detected",
                required="Readable room labels or CAD room layers",
                status="Review",
                rule_number=primary_regulation,
                clause_number=f"{primary_regulation}-ROOM-DETECTION",
                suggested_fix="Use CAD layers or text labels for rooms such as living, bedroom, kitchen, toilet, balcony, passage and staircase.",
            )
        )
    return checks


def _building_checks(area_result: AnalysisResult) -> list[ComplianceCheck]:
    checks = [
        ComplianceCheck(
            scope="Built-up area",
            extracted=f"{area_result.total_area_m2:.2f} m2",
            required="Area must be extracted from CAD/vector geometry or explicit area annotation",
            status="Pass" if area_result.total_area_m2 > 0 else "Fail",
            rule_number="BUILDING-GEOMETRY",
            clause_number="GEOM-BUA-001",
            suggested_fix="Upload a DXF with closed BUA/RERA/RCA boundaries for high-confidence geometry extraction." if area_result.total_area_m2 <= 0 else "No correction needed.",
        ),
        ComplianceCheck(
            scope="Plot, footprint, setbacks, open space and parking",
            extracted="Not fully available in current drawing extraction",
            required="Plot boundary, footprint, setback lines, parking and open-space layers",
            status="Review",
            rule_number="BUILDING-GEOMETRY",
            clause_number="GEOM-LAYER-MAP-001",
            suggested_fix="Add or map CAD layers for plot boundary, building footprint, setbacks, open space, RG, parking, staircase and lift cores.",
        ),
    ]
    return checks


def _classify_room(label: str) -> str:
    normalized = re.sub(r"[^a-z]+", " ", label.lower())
    mapping = [
        ("living", "living"),
        ("bed", "bedroom"),
        ("kitchen", "kitchen"),
        ("dining", "dining"),
        ("toilet", "toilet"),
        ("bath", "bathroom"),
        ("utility", "utility"),
        ("balcony", "balcony"),
        ("passage", "passage"),
        ("stair", "staircase"),
        ("lift", "lift"),
        ("parking", "parking"),
        ("store", "store"),
        ("terrace", "terrace"),
    ]
    for token, room_type in mapping:
        if token in normalized:
            return room_type
    return ""


def _primary_regulation(selected_regulations: list[str]) -> str:
    if not selected_regulations:
        return "SEED"
    return selected_regulations[0].replace("_", "-").upper()
