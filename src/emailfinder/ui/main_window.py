import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget
from sqlalchemy import select
from sqlalchemy.orm import Session

from emailfinder.config.brief import load_company_brief
from emailfinder.persistence.database import Evidence, Prospect, create_database
from emailfinder.providers.mock import MockCompanyDiscoveryProvider, MockEmailDiscoveryProvider, MockEmailVerificationProvider, MockReasoningProvider
from emailfinder.services.pipeline import MockPipeline


class MainWindow(QMainWindow):
    headers = ["Company", "Domain", "Industry", "Location", "ICP Score", "Qualification", "Confidence", "Need Hypothesis", "Evidence Count", "Mode"]

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.mode = os.getenv("RUN_MODE", "MOCK").upper()
        self.brief_path = Path(os.getenv("COMPANY_BRIEF_PATH") or root / "examples" / ("company_brief.appifyu.yaml" if self.mode == "REAL" else "company_brief.example.yaml"))
        db_path = os.getenv("DATABASE_PATH") or str(root / "emailfinder.db")
        self.engine = create_database(db_path)
        self.setWindowTitle("EmailFinder — Phase 1 Mock")
        self.resize(1200, 720)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        outer = QWidget(); layout = QHBoxLayout(outer)
        nav = QListWidget(); nav.addItems(["Dashboard", "Company Brief", "Prospects", "Jobs", "Settings"]); nav.setFixedWidth(160); nav.setCurrentRow(0)
        content = QWidget(); main = QVBoxLayout(content)
        self.status = QLabel(f"Company Brief: {self.brief_path.name} • {self.mode} mode")
        self.summary = QLabel("Today's target: —   Last job: —   Accepted: —   Rejected: —")
        button = QPushButton("Build Today's List"); button.clicked.connect(self.run_mock)
        self.table = QTableWidget(0, len(self.headers)); self.table.setHorizontalHeaderLabels(self.headers); self.table.setSelectionBehavior(QTableWidget.SelectRows); self.table.itemSelectionChanged.connect(self.show_details)
        self.details = QTextEdit(); self.details.setReadOnly(True); self.details.setPlaceholderText("Select a prospect to inspect mock evidence and reasoning.")
        split = QSplitter(Qt.Vertical); split.addWidget(self.table); split.addWidget(self.details); split.setSizes([430, 180])
        main.addWidget(self.status); main.addWidget(self.summary); main.addWidget(button); main.addWidget(split)
        layout.addWidget(nav); layout.addWidget(content); self.setCentralWidget(outer)

    def run_mock(self):
        try:
            brief = load_company_brief(self.brief_path)
            if self.mode == "REAL":
                from emailfinder.providers.evidence import PublicWebsiteEvidenceProvider
                from emailfinder.providers.nvidia import NVIDIAReasoningProvider
                from emailfinder.services.phase2_pipeline import Phase2APipeline
                if os.getenv("SEARCH_PROVIDER", "BRAVE").upper() == "WIKIDATA":
                    from emailfinder.providers.wikidata import WikidataCompanyDiscoveryProvider
                    discovery = WikidataCompanyDiscoveryProvider()
                else:
                    from emailfinder.providers.brave import BraveCompanyDiscoveryProvider
                    discovery = BraveCompanyDiscoveryProvider()
                job = Phase2APipeline(self.engine, discovery, PublicWebsiteEvidenceProvider(), NVIDIAReasoningProvider()).run(brief)
                message = f"REAL Phase 2A complete. Discovered {job.discovered_count}; evidence {job.evidence_count}; evaluated {job.evaluated_count}; accepted {job.accepted_count}; rejected {job.rejected_count}; insufficient {job.insufficient_count}; errors {job.error_count}."
            else:
                pipeline = MockPipeline(self.engine, MockCompanyDiscoveryProvider(), MockReasoningProvider(), MockEmailDiscoveryProvider(), MockEmailVerificationProvider())
                pipeline.run(brief); message = "MOCK list build completed. No external services were called."
            self.refresh()
            QMessageBox.information(self, "Complete", message)
        except Exception as exc:
            QMessageBox.critical(self, "Build failed", str(exc))

    def refresh(self):
        with Session(self.engine) as session:
            rows = list(session.scalars(select(Prospect).order_by(Prospect.id)))
            self.table.setRowCount(len(rows))
            for row, p in enumerate(rows):
                count = session.query(Evidence).filter(Evidence.entity_type.in_(["company", "prospect"]), Evidence.entity_id.in_([p.company_id, p.id])).count()
                mode = "REAL" if p.person_id is None else "MOCK"
                values = [p.company.name, p.company.domain, p.company.industry or "", p.company.country or "", p.icp_score, p.status, p.confidence_score, p.need_hypothesis or "", count, mode]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(str(value)); item.setData(Qt.UserRole, p.id); self.table.setItem(row, col, item)
            accepted = sum(p.status in {"READY_FOR_REVIEW", "ACCEPT"} for p in rows)
            self.summary.setText(f"Today's target: 5   Last job: {'completed' if rows else 'none'}   Accepted: {accepted}   Rejected: {len(rows)-accepted}")
            self.table.resizeColumnsToContents()

    def show_details(self):
        items = self.table.selectedItems()
        if not items: return
        prospect_id = items[0].data(Qt.UserRole)
        with Session(self.engine) as session:
            p = session.get(Prospect, prospect_id)
            evidence = list(session.scalars(select(Evidence).where(((Evidence.entity_type == "prospect") & (Evidence.entity_id == prospect_id)) | ((Evidence.entity_type == "company") & (Evidence.entity_id == p.company_id)))))
            details = [f"Qualification: {p.status}", f"Need hypothesis: {p.need_hypothesis or 'None'}", f"Rejection reason: {p.rejection_reason or 'None'}"]
            for item in evidence: details.append(f"\n[{item.source_quality}] {item.source_title}\n{item.source_url}\n{item.excerpt}")
            self.details.setPlainText("\n".join(details))
