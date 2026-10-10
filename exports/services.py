"""
Exports CSV / XLSX / PDF des signalements.

CONFIDENTIALITÉ
  * Un export ne contient JAMAIS de numéro de téléphone, de compte ou de référence au déclarant :
    la requête ne charge même pas la relation `user`.
  * Les colonnes sont définies UNE fois (COLUMNS) pour les trois formats.
  * Exclus volontairement : note de l'administrateur et identité de la personne qui a traité le signalement.

SÉCURITÉ DES TABLEURS
  * Une cellule commençant par = + - @ (ou une tabulation) est exécutée comme une formule par Excel /
    LibreOffice. Tout texte saisi par un utilisateur est donc préfixé par une apostrophe (safe_text).
"""
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Callable
from xml.sax.saxutils import escape

from django.conf import settings
from django.db.models import Count
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from dashboard.stats import compute_stats
from reports.choices import AccidentType, ReportStatus, Severity
from reports.filters import MODE_CHOICES, ZONE_CHOICES
from reports.zone import annotate_in_zone, get_coverage_zone

NAVY = "0F2F4F"
ACCENT = "C2570A"
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
NOTICE = "Aucun numéro de téléphone n'est inclus dans cet export."


# ---------------------------------------------------------------------------
# Nettoyage des valeurs
# ---------------------------------------------------------------------------
def safe_text(value):
    """Texte sûr pour un tableur : sans caractère de contrôle et sans formule exécutable."""
    if value is None:
        return ""
    text = CONTROL_CHARS.sub("", str(value))
    if text.startswith(FORMULA_PREFIXES):
        text = "'" + text
    return text


def pdf_text(value):
    """Helvetica (police intégrée) ne couvre que le jeu Windows-1252 : le reste devient « ? »."""
    return escape(str(value or "").encode("cp1252", "replace").decode("cp1252"))


# ---------------------------------------------------------------------------
# Colonnes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Column:
    key: str
    header: str
    width: int                 # largeur Excel (caractères)
    kind: str                  # text | date | time | datetime | float | int
    getter: Callable[[Any], Any]


def _local(dt):
    return timezone.localtime(dt).replace(tzinfo=None) if dt else None


COLUMNS = [
    Column("reference", "Référence", 17, "text", lambda r: r.reference),
    Column("date", "Date de l'accident", 17, "date", lambda r: r.accident_date),
    Column("time", "Heure", 8, "time", lambda r: r.accident_time),
    Column("type", "Type d'accident", 45, "text", lambda r: r.get_reported_type_display()),
    Column("severity", "Gravité", 11, "text", lambda r: r.get_severity_display()),
    Column("status", "Statut", 12, "text", lambda r: r.get_status_display()),
    Column("mode", "Mode", 11, "text", lambda r: "Anonyme" if r.is_anonymous else "Identifié"),
    Column("latitude", "Latitude", 12, "float", lambda r: round(r.location.y, 6)),
    Column("longitude", "Longitude", 12, "float", lambda r: round(r.location.x, 6)),
    Column("zone", "Zone de couverture", 20, "text", lambda r: "Dans la zone" if r.in_zone else "Hors zone"),
    Column("vehicles", "Véhicules impliqués (approx.)", 14, "int", lambda r: r.vehicle_count),
    Column("injured", "Blessés", 9, "int", lambda r: r.injured_count),
    Column("deaths", "Décès", 8, "int", lambda r: r.death_count),
    Column("description", "Description", 50, "text", lambda r: r.description),
    Column("photos", "Photos (nombre)", 14, "int", lambda r: (1 if r.photo else 0) + r.extra_photos_count),
    Column("created", "Signalé le", 17, "datetime", lambda r: _local(r.created_at)),
    Column("updated", "Modifié le", 17, "datetime", lambda r: _local(r.updated_at)),
    Column("processed", "Traité le", 17, "datetime", lambda r: _local(r.verified_at)),
    Column("demo", "Donnée de démonstration", 14, "text", lambda r: "Oui" if r.is_demo else "Non"),
]

EXPORT_FIELDS = [
    "reference", "accident_date", "accident_time", "accident_type", "accident_cause", "severity", "status", "is_anonymous",
    "location", "vehicle_count", "injured_count", "death_count", "description", "photo", "created_at",
    "updated_at", "verified_at", "is_demo",
]


def export_queryset(queryset):
    """Signalements à exporter. La relation `user` n'est volontairement jamais chargée.
    `extra_photos_count` est annoté en SQL pour compter les photos sans requête par ligne (N+1)."""
    return (
        annotate_in_zone(queryset)
        .only(*EXPORT_FIELDS)
        .annotate(extra_photos_count=Count("extra_photos"))
        .order_by("-accident_date", "-accident_time", "reference")
    )


def row_values(report):
    return [column.getter(report) for column in COLUMNS]


# ---------------------------------------------------------------------------
# Description des filtres et résumé
# ---------------------------------------------------------------------------
def _fmt_date(value):
    return value.strftime("%d/%m/%Y") if value else "…"


def describe_filters(form):
    """Liste (libellé, valeur) des filtres réellement appliqués, pour les en-têtes d'export."""
    data = form.cleaned_data if form.is_bound and form.is_valid() else {}
    lines = []
    if data.get("date_from") or data.get("date_to"):
        lines.append(("Période (date de l'accident)", f"du {_fmt_date(data.get('date_from'))} au {_fmt_date(data.get('date_to'))}"))
    if data.get("status"):
        lines.append(("Statut", ReportStatus(data["status"]).label))
    if data.get("mode"):
        lines.append(("Mode", dict(MODE_CHOICES)[data["mode"]]))
    if data.get("accident_type"):
        lines.append(("Type d'accident", AccidentType(data["accident_type"]).label))
    if data.get("severity"):
        lines.append(("Gravité", Severity(data["severity"]).label))
    if data.get("zone"):
        lines.append(("Zone", dict(ZONE_CHOICES)[data["zone"]]))
    if data.get("q"):
        lines.append(("Recherche", data["q"]))
    return lines


def filters_summary_text(form):
    lines = describe_filters(form)
    return "; ".join(f"{label} : {value}" for label, value in lines) or "aucun filtre"


def summary_blocks(stats):
    c = stats["counts"]
    return [
        ("Signalements", [("Total", c["total"]), ("Anonymes", c["anonymous"]), ("Identifiés", c["identified"]),
                          ("En attente", c["pending"]), ("Vérifiés", c["verified"]), ("Rejetés", c["rejected"])]),
        ("Accidents retenus" + ("" if stats["include_rejected"] else " (hors rejetés)"),
         [("Accidents", stats["accidents"]), ("Véhicules impliqués (déclarés)", stats["vehicles"]),
          ("Blessés", stats["injured"]), ("Décès", stats["deaths"]),
          ("Hors zone de couverture", stats["outside_zone"])]),
    ]


def zone_note():
    zone = get_coverage_zone()
    return f"{zone.label}." + (f" {zone.disclaimer}" if zone.disclaimer else "")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def _csv_value(column, value):
    if value is None:
        return ""
    if column.kind == "date":
        return value.isoformat()
    if column.kind == "time":
        return value.strftime("%H:%M")
    if column.kind == "datetime":
        return value.strftime("%Y-%m-%d %H:%M")
    if column.kind == "float":
        return f"{value:.6f}"
    if column.kind == "int":
        return str(value)
    return safe_text(value)


def build_csv(reports):
    """CSV prêt pour Excel (UTF-8 avec BOM) : séparateur « ; », décimales avec un point (outils SIG)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([c.header for c in COLUMNS])
    for report in reports:
        writer.writerow([_csv_value(c, v) for c, v in zip(COLUMNS, row_values(report))])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def _xlsx_value(column, value):
    if value is None:
        return None
    return safe_text(value) if column.kind == "text" else value


def build_xlsx(reports, form, stats):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Signalements"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=NAVY)

    for index, column in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=1, column=index, value=column.header)
        cell.font, cell.fill = header_font, header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = column.width
    sheet.row_dimensions[1].height = 30

    formats = {"date": "DD/MM/YYYY", "time": "HH:MM", "datetime": "DD/MM/YYYY HH:MM", "float": "0.000000", "int": "0"}
    for row_index, report in enumerate(reports, start=2):
        for col_index, (column, value) in enumerate(zip(COLUMNS, row_values(report)), start=1):
            cell = sheet.cell(row=row_index, column=col_index, value=_xlsx_value(column, value))
            if column.kind in formats:
                cell.number_format = formats[column.kind]
            if column.key == "description":
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(sheet.max_row, 1)}"

    summary = workbook.create_sheet("Résumé")
    summary.column_dimensions["A"].width = 34
    summary.column_dimensions["B"].width = 60
    summary["A1"], summary["A1"].font = "ACCIMAP : export des signalements", Font(bold=True, size=14, color=NAVY)
    summary["A2"] = f"Généré le {timezone.localtime():%d/%m/%Y à %H:%M}"
    row = 4
    summary.cell(row=row, column=1, value="Filtres appliqués").font = Font(bold=True)
    filters = describe_filters(form)
    for label, value in filters or [("Aucun filtre", "tous les signalements")]:
        row += 1
        summary.cell(row=row, column=1, value=safe_text(label))
        summary.cell(row=row, column=2, value=safe_text(value))
    for title, items in summary_blocks(stats):
        row += 2
        summary.cell(row=row, column=1, value=title).font = Font(bold=True)
        for label, value in items:
            row += 1
            summary.cell(row=row, column=1, value=label)
            summary.cell(row=row, column=2, value=value).alignment = Alignment(horizontal="left")
    row += 2
    for note in (NOTICE, zone_note(), "Données collaboratives, non officielles."):
        summary.cell(row=row, column=1, value=note)
        row += 1

    workbook.properties.title = "ACCIMAP : export des signalements"
    workbook.properties.creator = "ACCIMAP"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
class _NumberedCanvas(pdf_canvas.Canvas):
    """Canvas qui connaît le nombre total de pages (« Page 2 sur 5 »)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_pages = []

    def showPage(self):
        self._saved_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_pages)
        for state in self._saved_pages:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total):
        width, _ = self._pagesize
        self.setStrokeColor(colors.HexColor("#" + ACCENT))
        self.setLineWidth(1.2)
        self.line(28, 34, width - 28, 34)
        self.setFont("Helvetica", 7)
        self.setFillColor(colors.HexColor("#555555"))
        self.drawString(28, 22, "ACCIMAP  |  Prototype académique  |  Données collaboratives, non officielles  |  " + NOTICE)
        self.drawRightString(width - 28, 22, f"Page {self._pageNumber} sur {total}")


def _pdf_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold", fontSize=22, leading=26,
                                alignment=0, textColor=colors.HexColor("#" + NAVY), spaceAfter=2),
        "subtitle": ParagraphStyle("s", parent=base["Normal"], fontName="Helvetica", fontSize=11,
                                   textColor=colors.HexColor("#444444"), spaceAfter=8),
        "h2": ParagraphStyle("h", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11,
                             textColor=colors.HexColor("#" + NAVY), spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica", fontSize=9, leading=12),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontName="Helvetica", fontSize=7.2, leading=8.8),
        "head": ParagraphStyle("hd", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.2, leading=8.8,
                               textColor=colors.white),
    }


def build_pdf(reports, form, stats, total_count, truncated_at=None):
    """Rapport PDF A4 paysage : titre, filtres, résumé, tableau des signalements."""
    styles = _pdf_styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=46,
        title="ACCIMAP : export des signalements", author="ACCIMAP",
        pageCompression=1 if getattr(settings, "EXPORT_PDF_COMPRESS", True) else 0,
    )
    story = [
        Paragraph("ACCIMAP", styles["title"]),
        Paragraph("Export des signalements d'accidents de la circulation (Grand Lomé)", styles["subtitle"]),
        Paragraph(f"Document généré le {timezone.localtime():%d/%m/%Y à %H:%M}.", styles["body"]),
        Paragraph("Filtres appliqués", styles["h2"]),
    ]
    filters = describe_filters(form)
    if filters:
        for label, value in filters:
            story.append(Paragraph(f"<b>{pdf_text(label)}</b> : {pdf_text(value)}", styles["body"]))
    else:
        story.append(Paragraph("Aucun filtre : tous les signalements.", styles["body"]))

    story.append(Paragraph("Résumé", styles["h2"]))
    left, right = summary_blocks(stats)
    rows = []
    for i in range(max(len(left[1]), len(right[1]))):
        a = left[1][i] if i < len(left[1]) else ("", "")
        b = right[1][i] if i < len(right[1]) else ("", "")
        rows.append([a[0], str(a[1]), b[0], str(b[1])])
    rows.insert(0, [left[0], "", right[0], ""])
    summary = Table(rows, colWidths=[150, 60, 190, 60], hAlign="LEFT")
    summary.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#" + NAVY)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#" + ACCENT)),
        ("FONT", (1, 1), (1, -1), "Helvetica-Bold", 9), ("FONT", (3, 1), (3, -1), "Helvetica-Bold", 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(summary)
    story.append(Paragraph(pdf_text(zone_note()), ParagraphStyle("z", parent=styles["body"], fontSize=7.5, spaceBefore=4,
                                                               textColor=colors.HexColor("#555555"))))

    story.append(Paragraph(f"Signalements ({total_count})", styles["h2"]))
    if truncated_at is not None:
        story.append(Paragraph(
            f"Le tableau est limité aux {truncated_at} signalements les plus récents. "
            "Utilisez l'export CSV ou Excel pour obtenir l'ensemble des résultats.", styles["body"]))
        story.append(Spacer(1, 4))

    cell = styles["cell"]
    header = ["Référence", "Accident", "Type", "Gravité", "Statut", "Mode", "Véh. / Bless. / Décès",
              "Position (lat, lon)", "Zone", "Description"]
    table_rows = [[Paragraph(pdf_text(h), styles["head"]) for h in header]]
    for r in reports:
        description = (r.description or "").replace("\n", " ")
        if len(description) > 140:
            description = description[:137] + "..."
        table_rows.append([Paragraph(pdf_text(v), cell) for v in (
            r.reference, f"{r.accident_date:%d/%m/%Y} {r.accident_time:%H:%M}", r.get_accident_type_display(),
            r.get_severity_display(), r.get_status_display(), "Anonyme" if r.is_anonymous else "Identifié",
            f"{'-' if r.vehicle_count is None else r.vehicle_count} / {r.injured_count} / {r.death_count}",
            f"{r.location.y:.5f}, {r.location.x:.5f}", "Dans la zone" if r.in_zone else "Hors zone", description)])
    if len(table_rows) == 1:
        table_rows.append([Paragraph("Aucun signalement ne correspond aux filtres.", cell)] + [""] * 9)

    widths = [76, 62, 98, 50, 52, 50, 58, 90, 50, 194]   # 780 pt : la référence tient sur une ligne
    table = Table(table_rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + NAVY)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F5F9")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C9D2DC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(table)
    doc.build(story, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()
