"""
Enterprise Dual-Tier FinOps Excel Generator (Zero External Dependencies)

Generates TWO distinct Microsoft Excel (.xlsx) workbooks tailored for different enterprise stakeholders:

1. File 1: `executive_project_summary.xlsx` (C-Suite / Leadership View)
   - Exactly ONE row per Project / Repository.
   - Summarizes total spend, MoM dollar delta, percentage rise, executive reason, and accountable lead.

2. File 2: `pipeline_detailed_ledger.xlsx` (Engineering & FinOps Audit View)
   - Granular breakdown for every pipeline and cloud service component (Glue, S3, Athena, SFN, VPC).
   - Deep-dive root causes, resource metrics, and specific technical remediation steps.

Built entirely using Python's standard library (zipfile + XML) without third-party
dependencies. The .xlsx parts are assembled by hand, so `_build_styles_xml`'s index list and
the numeric style ids used in the row builders must stay in step — changing one without the
other silently produces a workbook that opens with the wrong formats, not an error.

Callers supply the rows. `generate_both_enterprise_reports` is the exception: it carries
hardcoded SAMPLE records so the module is runnable on its own. Those dollar figures are
illustrative, not Cost Explorer actuals and not BCM forecasts.

Depends on: nothing (stdlib only)
Shells out to: nothing — no cloud CLI, no network. Writes .xlsx files locally.
Used by: core/reporting/finops_agent.py (`--export-excel`, imported lazily inside
    `cmd_export_excel`); also runnable directly, which writes both workbooks into
    artifacts/reports/
"""

import os
import sys
import json
import zipfile
import datetime
from xml.sax.saxutils import escape


def _build_content_types_xml(num_sheets=1):
    sheets_xml = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(num_sheets)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        '  <Default Extension="xml" ContentType="application/xml"/>\n'
        '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\n'
        '  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>\n'
        f'  {sheets_xml}\n'
        '</Types>'
    )


def _build_root_rels_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>\n'
        '</Relationships>'
    )


def _build_workbook_xml(sheet_names):
    sheets_xml = "".join(
        f'<sheet name="{escape(name)}" sheetId="{i+1}" r:id="rId{i+1}"/>'
        for i, name in enumerate(sheet_names)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
        f'  <sheets>{sheets_xml}</sheets>\n'
        '</workbook>'
    )


def _build_workbook_rels_xml(num_sheets=1):
    rels_xml = "".join(
        f'<Relationship Id="rId{i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i+1}.xml"/>\n'
        for i in range(num_sheets)
    )
    styles_rel = (
        f'<Relationship Id="rId{num_sheets+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>\n'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        f'  {rels_xml}'
        f'  {styles_rel}'
        '</Relationships>'
    )


def _build_styles_xml():
    """
    Style indices:
    0: Normal text
    1: Table Header (Dark Navy #1F4E79, White bold text, centered)
    2: Title (Bold 15pt Navy)
    3: Currency ($#,##0.00)
    4: Currency Delta Red ($#,##0.00, Red bold)
    5: Currency Delta Green ($#,##0.00, Green bold)
    6: Percentage (0.0%)
    7: Percentage Red (+0.0%, Red bold)
    8: Percentage Green (+0.0%, Green bold)
    9: Zebra Gray Fill (#F9F9F9)
    10: Status Review Required (Bold Red #C00000)
    11: Status Healthy (Bold Green #375623)
    12: Section Subheader (Bold 11pt Slate #2F5597)
    """
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
        '  <numFmts count="3">\n'
        '    <numFmt numFmtId="164" formatCode="$#,##0.00"/>\n'
        '    <numFmt numFmtId="165" formatCode="+0.0%;-0.0%;0.0%"/>\n'
        '    <numFmt numFmtId="166" formatCode="0.0%"/>\n'
        '  </numFmts>\n'
        '  <fonts count="6">\n'
        '    <font><sz val="11"/><name val="Calibri"/></font>\n'
        '    <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>\n'
        '    <font><b/><sz val="15"/><color rgb="FF1F4E79"/><name val="Calibri"/></font>\n'
        '    <font><b/><sz val="11"/><color rgb="FFC00000"/><name val="Calibri"/></font>\n'
        '    <font><b/><sz val="11"/><color rgb="FF375623"/><name val="Calibri"/></font>\n'
        '    <font><b/><sz val="11"/><color rgb="FF2F5597"/><name val="Calibri"/></font>\n'
        '  </fonts>\n'
        '  <fills count="4">\n'
        '    <fill><patternFill patternType="none"/></fill>\n'
        '    <fill><patternFill patternType="gray125"/></fill>\n'
        '    <fill><patternFill patternType="solid"><fgColor rgb="FF1F4E79"/></patternFill></fill>\n'
        '    <fill><patternFill patternType="solid"><fgColor rgb="FFF9F9F9"/></patternFill></fill>\n'
        '  </fills>\n'
        '  <borders count="2">\n'
        '    <border><left/><right/><top/><bottom/><diagonal/></border>\n'
        '    <border>'
        '      <left style="thin"><color rgb="FFD9D9D9"/></left>'
        '      <right style="thin"><color rgb="FFD9D9D9"/></right>'
        '      <top style="thin"><color rgb="FFD9D9D9"/></top>'
        '      <bottom style="thin"><color rgb="FFD9D9D9"/></bottom>'
        '    </border>\n'
        '  </borders>\n'
        '  <cellXfs count="13">\n'
        '    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"/>\n'
        '    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>\n'
        '    <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>\n'
        '    <xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/>\n'
        '    <xf numFmtId="164" fontId="3" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyFont="1"/>\n'
        '    <xf numFmtId="164" fontId="4" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyFont="1"/>\n'
        '    <xf numFmtId="166" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/>\n'
        '    <xf numFmtId="165" fontId="3" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyFont="1"/>\n'
        '    <xf numFmtId="165" fontId="4" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyFont="1"/>\n'
        '    <xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1"/>\n'
        '    <xf numFmtId="0" fontId="3" fillId="0" borderId="1" xfId="0" applyFont="1"/>\n'
        '    <xf numFmtId="0" fontId="4" fillId="0" borderId="1" xfId="0" applyFont="1"/>\n'
        '    <xf numFmtId="0" fontId="5" fillId="0" borderId="0" xfId="0" applyFont="1"/>\n'
        '  </cellXfs>\n'
        '</styleSheet>'
    )


def _col_name(n):
    s = ""
    n += 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _build_sheet_xml(rows_data, column_widths=None):
    cols_xml = ""
    if column_widths:
        cols_xml = "<cols>" + "".join(
            f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(column_widths)
        ) + "</cols>"

    sheet_data = []
    for r_idx, row in enumerate(rows_data, start=1):
        cells = []
        for c_idx, cell in enumerate(row):
            if cell is None:
                continue
            val, style_idx, t_hint = cell
            c_ref = f"{_col_name(c_idx)}{r_idx}"
            if t_hint == 'n':
                cells.append(f'<c r="{c_ref}" s="{style_idx}"><v>{val}</v></c>')
            else:
                cells.append(
                    f'<c r="{c_ref}" t="inlineStr" s="{style_idx}">'
                    f'<is><t>{escape(str(val))}</t></is></c>'
                )
        sheet_data.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
        f'  {cols_xml}\n'
        f'  <sheetData>{"".join(sheet_data)}</sheetData>\n'
        '</worksheet>'
    )


# ===========================================================================
# FILE 1: EXECUTIVE PROJECT SUMMARY (1 Single Row Per Project)
# ===========================================================================
def generate_executive_project_summary_excel(output_path, project_records):
    """
    Generates File 1: Executive C-Suite Overview (1 Single Row per Project/Repository).

    project_records: list of dicts with:
      - domain: str
      - project_repo: str
      - active_pipelines: int
      - last_month_usd: float
      - current_month_usd: float
      - cost_center: str
      - owner: str
      - root_cause_summary: str
      - action_plan: str
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    total_last = sum(p["last_month_usd"] for p in project_records)
    total_curr = sum(p["current_month_usd"] for p in project_records)
    total_delta = total_curr - total_last
    total_pct = (total_delta / total_last) if total_last else 0.0

    rows = [
        [("EXECUTIVE CLOUD PORTFOLIO FINOPS REPORT (PROJECT ROLLUP)", 2, "s")],
        [(f"Reporting Cadence: Month-over-Month (MoM) Variance | Generated: {datetime.date.today().isoformat()}", 0, "s")],
        [],
        [("EXECUTIVE PORTFOLIO TOTALS", 12, "s")],
        [("Total Portfolio Spend (Last Mo)", 1, "s"), ("Total Portfolio Spend (Curr Mo)", 1, "s"), ("Overall Dollar Delta ($)", 1, "s"), ("Overall Portfolio Rise (%)", 1, "s"), ("Executive Status", 1, "s")],
        [
            (total_last, 3, "n"),
            (total_curr, 3, "n"),
            (total_delta, 4 if total_delta > 0 else 5, "n"),
            (total_pct, 7 if total_pct > 0 else 8, "n"),
            ("[WARN] PORTFOLIO SURGE (>15%)" if total_pct > 0.15 else "[OK] HEALTHY (<15%)", 10 if total_pct > 0.15 else 11, "s"),
        ],
        [],
        [("PROJECT-BY-PROJECT EXECUTIVE LEDGER (1 ROW PER PROJECT REPOSITORY)", 12, "s")],
        [
            ("Business Domain", 1, "s"),
            ("Project / Repository", 1, "s"),
            ("Pipelines", 1, "s"),
            ("Last Month ($)", 1, "s"),
            ("Current Month ($)", 1, "s"),
            ("MoM Delta ($)", 1, "s"),
            ("MoM Rise (%)", 1, "s"),
            ("Cost Center", 1, "s"),
            ("Accountable Project Lead", 1, "s"),
            ("Primary Executive Reason / Cost Driver", 1, "s"),
            ("Leadership Action Plan & Remediation", 1, "s"),
            ("Health Status", 1, "s"),
        ]
    ]

    for p in project_records:
        delta = p["current_month_usd"] - p["last_month_usd"]
        pct = (delta / p["last_month_usd"]) if p["last_month_usd"] else 0.0
        status_text = "[WARN] REVIEW REQUIRED" if pct > 0.15 else "[OK] HEALTHY"
        status_style = 10 if pct > 0.15 else 11

        rows.append([
            (p["domain"], 0, "s"),
            (p["project_repo"], 0, "s"),
            (p.get("active_pipelines", 1), 0, "n"),
            (p["last_month_usd"], 3, "n"),
            (p["current_month_usd"], 3, "n"),
            (delta, 4 if delta > 0 else 5, "n"),
            (pct, 7 if pct > 0 else 8, "n"),
            (p["cost_center"], 0, "s"),
            (p["owner"], 0, "s"),
            (p["root_cause_summary"], 0, "s"),
            (p["action_plan"], 0, "s"),
            (status_text, status_style, "s"),
        ])

    widths = [26, 32, 12, 18, 18, 18, 16, 16, 26, 50, 48, 22]

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _build_content_types_xml(1))
        zf.writestr("_rels/.rels", _build_root_rels_xml())
        zf.writestr("xl/workbook.xml", _build_workbook_xml(["Executive Project Summary"]))
        zf.writestr("xl/_rels/workbook.xml.rels", _build_workbook_rels_xml(1))
        zf.writestr("xl/styles.xml", _build_styles_xml())
        zf.writestr("xl/worksheets/sheet1.xml", _build_sheet_xml(rows, widths))

    print(f"[EXCEL 1] Successfully generated Executive Project Summary: {output_path}")
    return output_path


# ===========================================================================
# FILE 2: PIPELINE DETAILED LEDGER (Granular Engineering & Ops View)
# ===========================================================================
def generate_pipeline_detailed_ledger_excel(output_path, pipeline_records):
    """
    Generates File 2: Detailed Multi-Pipeline Engineering Ledger.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    rows = [
        [("GRANULAR MULTI-PROJECT PIPELINE FINOPS LEDGER (ENGINEERING AUDIT VIEW)", 2, "s")],
        [(f"Detailed Breakdown by Service Component | Generated: {datetime.date.today().isoformat()}", 0, "s")],
        [],
        [
            ("Business Domain", 1, "s"),
            ("Project Repo", 1, "s"),
            ("Pipeline Identifier", 1, "s"),
            ("Service Component", 1, "s"),
            ("Last Month ($)", 1, "s"),
            ("Current Month ($)", 1, "s"),
            ("Delta ($)", 1, "s"),
            ("Rise (%)", 1, "s"),
            ("Cost Center", 1, "s"),
            ("Technical Lead", 1, "s"),
            ("Detailed Root Cause & Workload Driver", 1, "s"),
            ("Technical Remediation & Tuning Action", 1, "s")
        ]
    ]

    for p in pipeline_records:
        delta = p["current_month_usd"] - p["last_month_usd"]
        pct = (delta / p["last_month_usd"]) if p["last_month_usd"] else 0.0

        rows.append([
            (p["domain"], 0, "s"),
            (p["project_repo"], 0, "s"),
            (p["pipeline_id"], 0, "s"),
            (p["service"], 0, "s"),
            (p["last_month_usd"], 3, "n"),
            (p["current_month_usd"], 3, "n"),
            (delta, 4 if delta > 0 else 5, "n"),
            (pct, 7 if pct > 0 else 8, "n"),
            (p["cost_center"], 0, "s"),
            (p["owner"], 0, "s"),
            (p["root_cause"], 0, "s"),
            (p["action_plan"], 0, "s"),
        ])

    widths = [24, 30, 28, 24, 18, 18, 18, 16, 16, 26, 48, 48]

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _build_content_types_xml(1))
        zf.writestr("_rels/.rels", _build_root_rels_xml())
        zf.writestr("xl/workbook.xml", _build_workbook_xml(["Pipeline Detailed Ledger"]))
        zf.writestr("xl/_rels/workbook.xml.rels", _build_workbook_rels_xml(1))
        zf.writestr("xl/styles.xml", _build_styles_xml())
        zf.writestr("xl/worksheets/sheet1.xml", _build_sheet_xml(rows, widths))

    print(f"[EXCEL 2] Successfully generated Pipeline Detailed Ledger: {output_path}")
    return output_path


def generate_both_enterprise_reports(reports_dir):
    """
    Helper to generate both standardized enterprise Excel workbooks into reports_dir.

    The records below are ILLUSTRATIVE SAMPLES demonstrating the layout, not live cost data.
    Replace them with provider-sourced figures before treating an exported workbook as a
    statement of actual spend.
    """
    os.makedirs(reports_dir, exist_ok=True)

    # 1. Project-level rollup records (1 row per project) -- sample data, see the docstring.
    project_records = [
        {
            "domain": "Domain-Analytics",
            "project_repo": "payer-reconciliation-engine",
            "active_pipelines": 3,
            "last_month_usd": 1410.00,
            "current_month_usd": 2136.00,
            "cost_center": "CC-4092",
            "owner": "sarah.t@company.com",
            "root_cause_summary": "Glue ETL scaled with +45GB/day data surge + S3 Bronze retention lag",
            "action_plan": "Enforce max 4-worker cap and 30-day Glacier lifecycle policy"
        },
        {
            "domain": "Domain-Regulatory",
            "project_repo": "claims-audit-pipeline",
            "active_pipelines": 2,
            "last_month_usd": 665.00,
            "current_month_usd": 663.00,
            "cost_center": "CC-8810",
            "owner": "elena.r@company.com",
            "root_cause_summary": "Stable execution; S3 Deep Archive transitions offset minor compute growth (-0.3%)",
            "action_plan": "Optimized; maintain current archiving lifecycle rules"
        },
        {
            "domain": "Domain-CoreOps",
            "project_repo": "enterprise-vpc-fabric",
            "active_pipelines": 1,
            "last_month_usd": 269.00,
            "current_month_usd": 269.00,
            "cost_center": "CC-1001",
            "owner": "david.k@company.com",
            "root_cause_summary": "Base idle network standing cost (S3 Gateway endpoint eliminates data transfer fee)",
            "action_plan": "Maintain S3 Gateway VPC endpoints"
        }
    ]

    # 2. Granular pipeline records (breakdown by service) -- sample data, see the docstring.
    pipeline_records = [
        {
            "domain": "Domain-Analytics",
            "project_repo": "payer-reconciliation-engine",
            "pipeline_id": "excel-ingest-medallion",
            "service": "AWS Glue 4.0 (Spark ETL)",
            "last_month_usd": 820.00,
            "current_month_usd": 1332.00,
            "cost_center": "CC-4092",
            "owner": "sarah.t@company.com",
            "root_cause": "Daily volume surged +45GB/day; worker autoscaling unconstrained",
            "action_plan": "Enforce max_capacity = 4 worker cap and tune executor memory"
        },
        {
            "domain": "Domain-Analytics",
            "project_repo": "payer-reconciliation-engine",
            "pipeline_id": "excel-ingest-medallion",
            "service": "Amazon S3 Lakehouse",
            "last_month_usd": 410.00,
            "current_month_usd": 550.00,
            "cost_center": "CC-4092",
            "owner": "sarah.t@company.com",
            "root_cause": "Raw bronze drops retained > 90 days without Glacier transition",
            "action_plan": "Enforce 30-day Glacier lifecycle transition rule"
        },
        {
            "domain": "Domain-Analytics",
            "project_repo": "payer-reconciliation-engine",
            "pipeline_id": "bi-analyst-queries",
            "service": "Amazon Athena",
            "last_month_usd": 180.00,
            "current_month_usd": 254.00,
            "cost_center": "CC-4092",
            "owner": "alex.m@company.com",
            "root_cause": "Analysts running full scans on raw Bronze instead of Gold Parquet",
            "action_plan": "Enforce 10 GiB per-query scan limit and direct BI to Gold"
        },
        {
            "domain": "Domain-Regulatory",
            "project_repo": "claims-audit-pipeline",
            "pipeline_id": "daily-claims-validator",
            "service": "AWS Step Functions",
            "last_month_usd": 45.00,
            "current_month_usd": 48.00,
            "cost_center": "CC-8810",
            "owner": "elena.r@company.com",
            "root_cause": "Normal execution volume increase (+6.6%)",
            "action_plan": "Within healthy threshold; no action required"
        },
        {
            "domain": "Domain-Regulatory",
            "project_repo": "claims-audit-pipeline",
            "pipeline_id": "daily-claims-validator",
            "service": "Amazon S3 Storage",
            "last_month_usd": 620.00,
            "current_month_usd": 615.00,
            "cost_center": "CC-8810",
            "owner": "elena.r@company.com",
            "root_cause": "Lifecycle rules active and tiering to Deep Archive (-0.8%)",
            "action_plan": "Optimized; maintain current lifecycle policies"
        },
        {
            "domain": "Domain-CoreOps",
            "project_repo": "enterprise-vpc-fabric",
            "pipeline_id": "shared-nat-gateway",
            "service": "Amazon VPC / NAT",
            "last_month_usd": 269.00,
            "current_month_usd": 269.00,
            "cost_center": "CC-1001",
            "owner": "david.k@company.com",
            "root_cause": "Base idle network standing cost (S3 Gateway endpoint keeps traffic free)",
            "action_plan": "Maintain S3 Gateway VPC endpoints"
        }
    ]

    path1 = os.path.join(reports_dir, "executive_project_summary.xlsx")
    path2 = os.path.join(reports_dir, "pipeline_detailed_ledger.xlsx")

    generate_executive_project_summary_excel(path1, project_records)
    generate_pipeline_detailed_ledger_excel(path2, pipeline_records)
    return path1, path2


if __name__ == "__main__":
    out_dir = os.path.join(os.getcwd(), "artifacts", "reports")
    generate_both_enterprise_reports(out_dir)
