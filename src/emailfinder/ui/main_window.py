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
    headers = ["Company", "Domain", "Contact", "Title", "Email", "Verification", "ICP Score", "Buyer Score", "Confidence", "Status"]

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.brief_path = root / "examples" / "company_brief.example.yaml"
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
        self.status = QLabel("Company Brief: ready • deterministic mock mode")
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
            pipeline = MockPipeline(self.engine, MockCompanyDiscoveryProvider(), MockReasoningProvider(), MockEmailDiscoveryProvider(), MockEmailVerificationProvider())
            pipeline.run(brief); self.refresh()
            QMessageBox.information(self, "Complete", "Mock-only list build completed. No external services were called.")
        except Exception as exc:
            QMessageBox.critical(self, "Build failed", str(exc))

    def refresh(self):
        with Session(self.engine) as session:
            rows = list(session.scalars(select(Prospect).order_by(Prospect.id)))
            self.table.setRowCount(len(rows))
            for row, p in enumerate(rows):
                values = [p.company.name, p.company.domain, p.person.full_name, p.person.title, p.email.email if p.email else "", p.email.verification_status if p.email else "", p.icp_score, p.buyer_score, p.confidence_score, p.status]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(str(value)); item.setData(Qt.UserRole, p.id); self.table.setItem(row, col, item)
            accepted = sum(p.status == "READY_FOR_REVIEW" for p in rows)
            self.summary.setText(f"Today's target: 5   Last job: {'completed' if rows else 'none'}   Accepted: {accepted}   Rejected: {len(rows)-accepted}")
            self.table.resizeColumnsToContents()

    def show_details(self):
        items = self.table.selectedItems()
        if not items: return
        prospect_id = items[0].data(Qt.UserRole)
        with Session(self.engine) as session:
            p = session.get(Prospect, prospect_id)
            evidence = session.scalar(select(Evidence).where(Evidence.entity_type == "prospect", Evidence.entity_id == prospect_id))
            self.details.setPlainText(f"Qualification evidence: {evidence.excerpt if evidence else 'None'}\nSource URL: {evidence.source_url if evidence else 'None'}\nNeed hypothesis: {p.need_hypothesis}\nPersonalization angle: {p.personalization_angle}\nRejection reason: {p.rejection_reason or 'None'}")

