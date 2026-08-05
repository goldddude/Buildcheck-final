from __future__ import annotations

import cgi
import html
import json
import base64
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from buildcheck_area import AnalysisResult, analyze_file
from buildcheck_compliance import ProjectInfo, build_report, load_registry
from buildcheck_pdf_report import build_pdf_report


ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.environ.get("TMPDIR", "/tmp")) if os.environ.get("VERCEL") else ROOT
UPLOAD_DIR = RUNTIME_DIR / "uploads"
REPORT_DIR = RUNTIME_DIR / "output" / "reports"
PORT = 8080


class BuildCheckHandler(BaseHTTPRequestHandler):
    server_version = "BuildCheck/0.3"

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/":
            self._send_html(render_page(load_registry()))
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/analyze":
            self._handle_analysis()
            return
        if path == "/download-report":
            self._handle_report_download()
            return
        self.send_error(404, "Not found")

    def _handle_analysis(self) -> None:
        registry = load_registry()
        try:
            form = self._read_form()
            project = self._read_project(form, registry)
            upload_path = self._save_upload(form)
            area_result = analyze_file(upload_path)
            report = build_report(area_result, project, registry)
            payload = report_payload(report, registry)
            report_id = save_report_payload(payload)
            self._send_html(render_page(registry, report=report, report_id=report_id))
        except Exception as exc:
            self._send_html(render_page(registry, error=str(exc)), status=500)

    def _handle_report_download(self) -> None:
        registry = load_registry()
        try:
            form = self._read_form()
            report_id = _field_value(form, "report_id")
            payload_text = _field_value(form, "report_payload")
            payload = decode_payload(payload_text) if payload_text else load_report_payload(report_id)
            selected_scheme_ids = _field_values(form, "schemes")
            available_schemes = {scheme["id"]: scheme for scheme in payload.get("applicable_schemes", [])}
            if not selected_scheme_ids:
                selected_scheme_ids = list(available_schemes)
            selected_schemes = [
                available_schemes[scheme_id]
                for scheme_id in selected_scheme_ids
                if scheme_id in available_schemes
            ]
            pdf_bytes = build_pdf_report(payload, selected_schemes)
            filename = safe_filename(payload["project"]["project_name"]) + "_BuildCheck_Report.pdf"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(pdf_bytes)))
            self.end_headers()
            self.wfile.write(pdf_bytes)
        except Exception as exc:
            self._send_html(render_page(registry, error=str(exc)), status=500)

    def _read_form(self) -> cgi.FieldStorage:
        return cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            },
        )

    def _read_project(self, form: cgi.FieldStorage, registry: dict) -> ProjectInfo:
        project_name = _field_value(form, "project_name")
        state = _field_value(form, "state")
        authority_id = _field_value(form, "authority_id")
        building_type = _field_value(form, "building_type")

        missing = []
        if not project_name:
            missing.append("Project Name")
        if not state:
            missing.append("State")
        if not authority_id:
            missing.append("Municipal Planning Authority")
        if not building_type:
            missing.append("Building Type")
        if missing:
            raise ValueError("Please complete: " + ", ".join(missing))

        authority = _authority_for(registry, state, authority_id)
        return ProjectInfo(
            project_name=project_name,
            state=state,
            authority_id=authority_id,
            building_type=building_type,
            selected_regulations=list(authority.get("regulations", [])),
        )

    def _save_upload(self, form: cgi.FieldStorage) -> Path:
        UPLOAD_DIR.mkdir(exist_ok=True)
        field = form["drawing"] if "drawing" in form else None
        if field is None or not field.filename:
            raise ValueError("Please choose a PDF, DXF, or DWG file.")

        safe_name = Path(field.filename).name
        output = UPLOAD_DIR / safe_name
        with output.open("wb") as handle:
            handle.write(field.file.read())
        return output

    def _send_html(self, body: str, status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def _field_value(form: cgi.FieldStorage, name: str) -> str:
    if name not in form:
        return ""
    value = form[name]
    if isinstance(value, list):
        return str(value[0].value).strip() if value else ""
    return str(value.value).strip()


def _field_values(form: cgi.FieldStorage, name: str) -> list[str]:
    if name not in form:
        return []
    value = form[name]
    if isinstance(value, list):
        return [str(item.value).strip() for item in value if str(item.value).strip()]
    single = str(value.value).strip()
    return [single] if single else []


def _authority_for(registry: dict, state_id: str, authority_id: str) -> dict:
    for state in registry["states"]:
        if state["id"] == state_id:
            for authority in state["authorities"]:
                if authority["id"] == authority_id:
                    return authority
    raise ValueError("Selected authority is not supported.")


def save_report_payload(payload: dict) -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_id = uuid.uuid4().hex
    (REPORT_DIR / f"{report_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return report_id


def load_report_payload(report_id: str) -> dict:
    if not report_id or not report_id.isalnum():
        raise ValueError("Invalid report reference.")
    path = REPORT_DIR / f"{report_id}.json"
    if not path.exists():
        raise ValueError("Report expired or missing. Please run the analysis again.")
    return json.loads(path.read_text(encoding="utf-8"))


def encode_payload(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_payload(value: str) -> dict:
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Report data could not be read. Please run the analysis again.") from exc


def safe_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())
    return cleaned or "BuildCheck"


def report_payload(report, registry: dict) -> dict:
    checks = report.room_checks + report.building_checks
    failures = [check for check in checks if check.status == "Fail"]
    reviews = [check for check in checks if check.status == "Review"]
    applicable_schemes = applicable_schemes_for_project(registry, report.project)
    observations = [
        f"Drawing analyzed using {report.area_result.method}.",
        f"Total extracted area is {report.area_result.total_area_m2:.2f} m2 ({report.area_result.total_area_ft2:.2f} ft2).",
        f"{len(report.area_result.items)} measurable area item(s) were found.",
        f"{len(applicable_schemes)} Maharashtra government scheme(s) were identified for this project.",
        f"{len(failures)} failed item(s) and {len(reviews)} review item(s) need attention.",
    ]
    improvements = [
        check.suggested_fix
        for check in checks
        if check.status != "Pass" and check.suggested_fix != "No correction needed."
    ]
    if not improvements:
        improvements = ["No failed items were detected in the current seed-rule checks."]

    return {
        "project": {
            "project_name": report.project.project_name,
            "state": report.project.state,
            "authority_id": report.project.authority_id,
            "building_type": report.project.building_type,
            "selected_regulations": report.project.selected_regulations,
        },
        "authority_name": report.authority_name,
        "regulation_names": report.regulation_names,
        "applicable_schemes": applicable_schemes,
        "loaded_categories": report.loaded_categories,
        "compliance_score": report.compliance_score,
        "observations": observations,
        "improvements": improvements,
        "warnings": report.warnings,
        "area": {
            "filename": report.area_result.filename,
            "file_type": report.area_result.file_type,
            "method": report.area_result.method,
            "total_area_m2": report.area_result.total_area_m2,
            "total_area_ft2": report.area_result.total_area_ft2,
            "items": [
                {
                    "label": item.label,
                    "area_m2": item.area_m2,
                    "area_ft2": item.area_m2 * 10.7639104167,
                    "source": item.source,
                    "confidence": item.confidence.title(),
                }
                for item in report.area_result.items
            ],
        },
        "checks": [
            {
                "scope": check.scope,
                "extracted": check.extracted,
                "required": check.required,
                "status": check.status,
                "rule_number": check.rule_number,
                "clause_number": check.clause_number,
                "suggested_fix": check.suggested_fix,
            }
            for check in checks
        ],
    }


def applicable_schemes_for_project(registry: dict, project: ProjectInfo) -> list[dict]:
    building_key = project.building_type.lower().replace(" ", "_")
    schemes: list[dict] = []
    for scheme in registry.get("schemes", []):
        scopes = scheme.get("authority_scope", [])
        tags = scheme.get("tags", [])
        in_authority = "all_maharashtra" in scopes or project.authority_id in scopes
        fits_building = building_key in tags or "all_buildings" in tags
        if in_authority and fits_building:
            schemes.append(scheme)
    return schemes


def render_page(registry: dict, report=None, report_id: str = "", error: str | None = None) -> str:
    result_block = ""
    if error:
        result_block = f'<section class="panel alert"><strong>Analysis failed:</strong> {html.escape(error)}</section>'
    elif report:
        result_block = render_report(report, report_id, registry)

    registry_json = json.dumps(registry).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BuildCheck Compliance</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f7fb;
      --panel: #ffffff;
      --text: #13213a;
      --muted: #63718a;
      --line: #d8e1ee;
      --brand: #1767ce;
      --brand-dark: #0f4f9f;
      --ok: #0f7a55;
      --fail: #a52626;
      --review: #916000;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Inter, Segoe UI, Arial, sans-serif; background: var(--bg); color: var(--text); }}
    header {{ background: #10233f; color: white; padding: 28px 32px; }}
    header h1 {{ margin: 0 0 8px; font-size: 38px; letter-spacing: 0; }}
    header p {{ margin: 0; max-width: 980px; color: #d9e4f4; line-height: 1.5; }}
    main {{
      width: min(1500px, calc(100% - 28px));
      margin: 24px auto 48px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 380px;
      gap: 20px;
      align-items: start;
    }}
    .left {{ display: grid; gap: 18px; min-width: 0; }}
    .scheme-panel {{ position: sticky; top: 16px; display: grid; gap: 14px; min-width: 0; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 22px; min-width: 0; }}
    .workflow {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 6px; margin-bottom: 18px; }}
    .step {{ background: #eef3fa; border: 1px solid var(--line); border-radius: 8px; padding: 9px 8px; color: #34445d; font-size: 12px; text-align: center; min-height: 42px; display: grid; align-content: center; }}
    form {{ display: grid; gap: 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    label {{ display: grid; gap: 7px; font-weight: 700; }}
    input, select {{ width: 100%; border: 1px solid var(--line); border-radius: 8px; padding: 11px 12px; color: var(--text); background: white; font: inherit; }}
    input[type=file] {{ border-style: dashed; background: #f9fbff; padding: 18px; }}
    .muted {{ color: var(--muted); font-size: 14px; line-height: 1.45; }}
    .checks {{ display: grid; gap: 8px; }}
    .check {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px; display: flex; gap: 8px; align-items: center; font-weight: 600; }}
    .check input {{ width: auto; }}
    button, .download-button {{ border: 0; border-radius: 8px; background: var(--brand); color: white; font-weight: 700; padding: 12px 18px; cursor: pointer; width: fit-content; text-decoration: none; }}
    button:disabled {{ opacity: .45; cursor: not-allowed; }}
    button:hover:not(:disabled), .download-button:hover {{ background: var(--brand-dark); }}
    .summary {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 18px; }}
    .metric {{ border: 1px solid var(--line); border-radius: 8px; padding: 16px; background: #fbfcff; }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; margin-bottom: 8px; }}
    .metric strong {{ font-size: 25px; overflow-wrap: anywhere; }}
    .points {{ display: grid; gap: 10px; margin: 12px 0 0; padding: 0; list-style: none; }}
    .points li {{ border-left: 4px solid var(--brand); background: #f8fbff; padding: 10px 12px; border-radius: 6px; }}
    .fail-list li {{ border-left-color: var(--fail); }}
    .scheme-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 14px; }}
    .scheme-card {{ border: 1px solid var(--line); border-radius: 8px; background: white; padding: 16px; display: grid; gap: 12px; box-shadow: 0 1px 3px rgba(19, 33, 58, .08); }}
    .scheme-top {{ display: flex; gap: 10px; align-items: flex-start; }}
    .scheme-top input {{ width: auto; margin-top: 4px; }}
    .scheme-title {{ font-weight: 800; margin-top: 7px; }}
    .scheme-level {{ color: #51638c; font-size: 13px; margin-top: 3px; }}
    .scheme-badge {{ width: fit-content; border: 1px solid #89cdfd; color: #005999; background: #eef8ff; border-radius: 999px; padding: 3px 9px; font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; }}
    .scheme-section {{ border-top: 1px solid var(--line); padding-top: 10px; color: #25395f; font-size: 13px; line-height: 1.45; }}
    .scheme-section strong {{ display: block; color: #51638c; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; margin-bottom: 5px; }}
    .scheme-note {{ color: #51638c; background: #f3f6fb; border-radius: 999px; padding: 6px 10px; font-size: 12px; width: fit-content; }}
    .scheme-link {{ color: #002d74; font-weight: 800; text-decoration: none; }}
    .scheme-actions {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; }}
    table {{ width: 100%; border-collapse: collapse; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; margin-top: 12px; table-layout: fixed; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--line); padding: 10px; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ background: #eef3fa; color: #34445d; font-size: 13px; }}
    tr:last-child td {{ border-bottom: 0; }}
    .status-Pass {{ color: var(--ok); font-weight: 800; }}
    .status-Fail {{ color: var(--fail); font-weight: 800; }}
    .status-Review {{ color: var(--review); font-weight: 800; }}
    .notes {{ display: grid; gap: 8px; margin-top: 16px; color: var(--review); }}
    .alert {{ border-color: #f2c6c6; background: #fff4f4; color: #8b2525; }}
    .pill {{ border: 1px solid var(--line); border-radius: 999px; padding: 7px 10px; background: #f8fbff; color: #34445d; display: inline-block; margin: 3px 4px 3px 0; }}
    details {{ margin-top: 14px; }}
    @media (max-width: 1100px) {{
      main {{ grid-template-columns: 1fr; }}
      .scheme-panel {{ position: static; }}
      .workflow, .grid, .summary, .scheme-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>BuildCheck</h1>
    <p>Select the Maharashtra planning authority, upload the drawing, review the analysis, then choose the applicable Maharashtra schemes for the final report.</p>
  </header>
  <main>
    <div class="left">
      <section class="panel">
        <div class="workflow">
          <div class="step">1. Project Details</div>
          <div class="step">2. Authority</div>
          <div class="step">3. Upload</div>
          <div class="step">4. Analysis</div>
          <div class="step">5. Schemes & PDF</div>
        </div>
        <form id="checkForm" method="post" action="/analyze" enctype="multipart/form-data">
          <div class="grid">
            <label>Project Name
              <input id="projectName" name="project_name" placeholder="Example: Prestige Mira Road Tower A" required>
            </label>
            <label>State
              <select id="state" name="state" required></select>
            </label>
            <label>Municipal Planning Authority
              <select id="authority" name="authority_id" required></select>
            </label>
            <label>Building Type
              <select id="buildingType" name="building_type" required></select>
            </label>
          </div>
          <div class="muted" id="authorityPreview">Select a Maharashtra authority to see applicable rules.</div>
          <label>Upload Drawing
            <input id="drawing" name="drawing" type="file" accept=".pdf,.dxf,.dwg" required>
          </label>
          <button id="submitBtn" type="submit" disabled>Run Analysis</button>
        </form>
      </section>
      {result_block}
    </div>
    <aside class="scheme-panel" id="schemePanel">
      {render_scheme_panel(registry, report, report_id)}
    </aside>
  </main>
  <script>
    const registry = {registry_json};
    const stateSelect = document.querySelector("#state");
    const authoritySelect = document.querySelector("#authority");
    const buildingTypeSelect = document.querySelector("#buildingType");
    const submitBtn = document.querySelector("#submitBtn");
    const form = document.querySelector("#checkForm");
    const preview = document.querySelector("#authorityPreview");

    function option(value, label) {{
      const item = document.createElement("option");
      item.value = value;
      item.textContent = label;
      return item;
    }}
    function selectedState() {{
      return registry.states.find((state) => state.id === stateSelect.value);
    }}
    function selectedAuthority() {{
      const state = selectedState();
      return state?.authorities.find((authority) => authority.id === authoritySelect.value);
    }}
    function init() {{
      stateSelect.appendChild(option("", "Select state"));
      registry.states.forEach((state) => stateSelect.appendChild(option(state.id, state.name)));
      buildingTypeSelect.appendChild(option("", "Select building type"));
      registry.building_types.forEach((type) => buildingTypeSelect.appendChild(option(type, type)));
      stateSelect.value = "MH";
      loadAuthorities();
      updatePreview();
      updateReadiness();
    }}
    function loadAuthorities() {{
      authoritySelect.innerHTML = "";
      authoritySelect.appendChild(option("", "Select authority"));
      const state = selectedState();
      if (!state) return;
      state.authorities.slice().sort((a, b) => a.name.localeCompare(b.name)).forEach((authority) => {{
        authoritySelect.appendChild(option(authority.id, `${{authority.name}} - ${{authority.type}}`));
      }});
    }}
    function updatePreview() {{
      const authority = selectedAuthority();
      if (!authority) {{
        preview.textContent = "Select a Maharashtra authority to see applicable rules.";
        updateReadiness();
        return;
      }}
      const names = authority.regulations.map((id) => registry.regulations[id]?.name).filter(Boolean);
      preview.innerHTML = `<strong>${{authority.name}}</strong><br>Applicable Maharashtra rules after analysis: ${{names.join(", ")}}.`;
      updateReadiness();
    }}
    function updateReadiness() {{
      const ready =
        document.querySelector("#projectName").value.trim() &&
        stateSelect.value &&
        authoritySelect.value &&
        buildingTypeSelect.value &&
        document.querySelector("#drawing").files.length;
      submitBtn.disabled = !ready;
    }}
    stateSelect.addEventListener("change", () => {{ loadAuthorities(); updatePreview(); }});
    authoritySelect.addEventListener("change", updatePreview);
    form.addEventListener("input", updateReadiness);
    form.addEventListener("change", updateReadiness);
    init();
  </script>
</body>
</html>"""


def render_scheme_panel(registry: dict, report=None, report_id: str = "") -> str:
    if not report:
        return """<section class="panel">
          <h2>Report Panel</h2>
          <p class="muted">After analysis, the Maharashtra-only scheme cards will appear below the result. Select the schemes you want and download the PDF report.</p>
        </section>"""

    return f"""<section class="panel">
      <h2>Report Ready</h2>
      <p class="muted">Use the scheme cards below the analysis to choose what should be included in the PDF report.</p>
    </section>"""


def render_report(report, report_id: str, registry: dict) -> str:
    payload = report_payload(report, registry)
    area = report.area_result
    failed_rows = _check_rows([check for check in report.room_checks + report.building_checks if check.status != "Pass"])
    area_rows = _area_rows(area)
    observations = "".join(f"<li>{html.escape(point)}</li>" for point in payload["observations"])
    improvements = "".join(f"<li>{html.escape(item)}</li>" for item in payload["improvements"])
    warnings = "".join(f"<div>{html.escape(warning)}</div>" for warning in report.warnings)
    regs = ", ".join(report.regulation_names) if report.regulation_names else "None"
    scheme_cards = render_scheme_cards(payload["applicable_schemes"], report_id)

    return f"""<section class="panel">
      <h2>Analysis Summary</h2>
      <div class="summary">
        <div class="metric"><span>Compliance Score</span><strong>{report.compliance_score}%</strong></div>
        <div class="metric"><span>Total Area</span><strong>{area.total_area_m2:,.2f} m2</strong></div>
        <div class="metric"><span>Total Area</span><strong>{area.total_area_ft2:,.2f} ft2</strong></div>
        <div class="metric"><span>Method</span><strong>{html.escape(area.file_type)}</strong></div>
      </div>
      <p><strong>{html.escape(report.project.project_name)}</strong> under <strong>{html.escape(report.authority_name)}</strong>.</p>
      <p class="muted">Applicable Maharashtra schemes detected for this authority: {html.escape(regs)}.</p>

      <h3>Main Points Noticed</h3>
      <ul class="points">{observations}</ul>

      <h3>Failed / Review Items</h3>
      <table>
        <thead><tr><th>Scope</th><th>Extracted</th><th>Required</th><th>Status</th><th>Suggested Fix</th></tr></thead>
        <tbody>{failed_rows}</tbody>
      </table>

      <h3>Overall Improvements Needed</h3>
      <ul class="points fail-list">{improvements}</ul>

      <details>
        <summary>Area Summary Details</summary>
        <table>
          <thead><tr><th>Area item</th><th>m2</th><th>ft2</th><th>Source</th><th>Confidence</th></tr></thead>
          <tbody>{area_rows}</tbody>
        </table>
      </details>

      <div class="notes">{warnings}</div>
    </section>
    {scheme_cards}"""


def render_scheme_cards(schemes: list[dict], report_id: str) -> str:
    if not schemes:
        return """<section class="panel">
          <h2>Applicable Government Schemes</h2>
          <p class="muted">No Maharashtra government schemes matched this authority and building type.</p>
        </section>"""

    cards = []
    for scheme in schemes:
        cards.append(
            f"""<article class="scheme-card">
              <div class="scheme-top">
                <input type="checkbox" name="schemes" value="{html.escape(scheme['id'])}" checked>
                <div>
                  <div class="scheme-badge">{html.escape(scheme['category'])}</div>
                  <div class="scheme-title">{html.escape(scheme['name'])}</div>
                  <div class="scheme-level">{html.escape(scheme['level'])}</div>
                </div>
              </div>
              <div class="scheme-section"><strong>Eligibility</strong>{html.escape(scheme['eligibility'])}</div>
              <div class="scheme-section"><strong>Key Benefits</strong>{html.escape(scheme['benefits'])}</div>
              <div class="scheme-actions">
                <span class="scheme-note">Applicable for this Maharashtra project profile</span>
                <a class="scheme-link" href="{html.escape(scheme['website'])}" target="_blank" rel="noreferrer">Visit Website -></a>
              </div>
            </article>"""
        )
    payload_value = ""
    if report_id:
        try:
            payload_value = encode_payload(load_report_payload(report_id))
        except ValueError:
            payload_value = ""

    return f"""<section class="panel">
      <h2>Applicable Government Schemes</h2>
      <p class="muted">{len(schemes)} Maharashtra scheme(s) identified for this project. Select whichever schemes you want included in the PDF report.</p>
      <form method="post" action="/download-report">
        <input type="hidden" name="report_id" value="{html.escape(report_id)}">
        <input type="hidden" name="report_payload" value="{html.escape(payload_value)}">
        <div class="scheme-grid">{''.join(cards)}</div>
        <button type="submit">Download PDF Report</button>
      </form>
    </section>"""


def _area_rows(result: AnalysisResult) -> str:
    rows = "\n".join(
        f"""<tr>
          <td>{html.escape(item.label)}</td>
          <td>{item.area_m2:,.2f}</td>
          <td>{item.area_m2 * 10.7639104167:,.2f}</td>
          <td>{html.escape(item.source)}</td>
          <td>{html.escape(item.confidence.title())}</td>
        </tr>"""
        for item in result.items
    )
    return rows or '<tr><td colspan="5">No measurable area items were found.</td></tr>'


def _check_rows(checks: list) -> str:
    rows = "\n".join(
        f"""<tr>
          <td>{html.escape(check.scope)}</td>
          <td>{html.escape(check.extracted)}</td>
          <td>{html.escape(check.required)}</td>
          <td class="status-{html.escape(check.status)}">{html.escape(check.status)}</td>
          <td>{html.escape(check.suggested_fix)}</td>
        </tr>"""
        for check in checks
    )
    return rows or '<tr><td colspan="5">No failed or review items detected.</td></tr>'


def main() -> None:
    address = ("127.0.0.1", PORT)
    print(f"BuildCheck Compliance running at http://{address[0]}:{address[1]}")
    ThreadingHTTPServer(address, BuildCheckHandler).serve_forever()


if __name__ == "__main__":
    main()
