''' SQLAlchemy models and ExportTracker for incremental Confluence exports. '''

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (Column, Integer, String, DateTime, Boolean,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class ExportedPageVersion(Base):
    ''' Tracks which page versions have been exported.

    Two flags per record:
      - record exists      → exported from Confluence (checkbox 1)
      - committed_to_git   → processed by git_versioner (checkbox 2)
    '''
    __tablename__ = 'exported_page_versions'

    id = Column(Integer, primary_key=True)
    page_id = Column(String, nullable=False)
    version_number = Column(Integer, nullable=False)
    page_title = Column(String)
    export_format = Column(String)
    exported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    committed_to_git = Column(Boolean, default=False)
    committed_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint('page_id', 'version_number', 'export_format',
                         name='uq_page_version_format'),
    )


class ExportedAttachment(Base):
    ''' Tracks which attachments have been exported. '''
    __tablename__ = 'exported_attachments'

    id = Column(Integer, primary_key=True)
    page_id = Column(String, nullable=False)
    attachment_id = Column(String, nullable=False)
    attachment_title = Column(String)
    attachment_version = Column(Integer)
    exported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    committed_to_git = Column(Boolean, default=False)
    committed_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint('page_id', 'attachment_id',
                         name='uq_page_attachment'),
    )


class CommittedFile(Base):
    ''' Tracks files processed by git_versioner (by source path).

    Works for any file type — versioned pages, attachments, plain files.
    '''
    __tablename__ = 'committed_files'

    id = Column(Integer, primary_key=True)
    source_path = Column(String, nullable=False, unique=True)
    committed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ExportTracker:
    ''' Tracks export state in PostgreSQL to enable incremental exports.

    Used by main.py (export from Confluence) and git_versioner.py
    (commit to git). Provides two "checkboxes" per version:
      1. exported   — record exists in exported_page_versions
      2. committed  — committed_to_git flag is True
    '''

    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)
        self.SessionLocal = sessionmaker(bind=self.engine)

    # ── main.py: export tracking ─────────────────────────────────────

    def get_exported_versions(self, page_id: str, fmt: str) -> set[int]:
        ''' Return set of version numbers already exported for a page+format. '''
        with self.SessionLocal() as session:
            rows = session.query(ExportedPageVersion.version_number).filter_by(
                page_id=page_id, export_format=fmt
            ).all()
            return {r[0] for r in rows}

    def mark_version_exported(self, page_id: str, version_number: int,
                              page_title: str, fmt: str):
        ''' Record that a page version has been exported. '''
        with self.SessionLocal() as session:
            existing = session.query(ExportedPageVersion).filter_by(
                page_id=page_id, version_number=version_number,
                export_format=fmt
            ).first()
            if not existing:
                session.add(ExportedPageVersion(
                    page_id=page_id,
                    version_number=version_number,
                    page_title=page_title,
                    export_format=fmt,
                ))
                session.commit()

    def get_exported_attachments(self, page_id: str) -> dict[str, int | None]:
        ''' Return {attachment_id: version} for already-exported attachments. '''
        with self.SessionLocal() as session:
            rows = session.query(
                ExportedAttachment.attachment_id,
                ExportedAttachment.attachment_version,
            ).filter_by(page_id=page_id).all()
            return {r[0]: r[1] for r in rows}

    def mark_attachment_exported(self, page_id: str, attachment_id: str,
                                 title: str, version: int | None = None):
        ''' Record that an attachment has been exported. '''
        with self.SessionLocal() as session:
            existing = session.query(ExportedAttachment).filter_by(
                page_id=page_id, attachment_id=attachment_id
            ).first()
            if existing:
                existing.attachment_title = title
                existing.attachment_version = version
                existing.exported_at = datetime.now(timezone.utc)
            else:
                session.add(ExportedAttachment(
                    page_id=page_id,
                    attachment_id=attachment_id,
                    attachment_title=title,
                    attachment_version=version,
                ))
            session.commit()

    # ── git_versioner.py: commit tracking ────────────────────────────

    def is_file_committed(self, source_path: str) -> bool:
        ''' Check if a file (by relative path) was already committed. '''
        with self.SessionLocal() as session:
            return session.query(CommittedFile).filter_by(
                source_path=source_path
            ).first() is not None

    def mark_file_committed(self, source_path: str):
        ''' Record that a file has been committed by git_versioner. '''
        with self.SessionLocal() as session:
            existing = session.query(CommittedFile).filter_by(
                source_path=source_path
            ).first()
            if not existing:
                session.add(CommittedFile(source_path=source_path))
                session.commit()

    def mark_version_committed(self, page_id: str, version_number: int,
                                fmt: str):
        ''' Set committed_to_git flag on ExportedPageVersion (checkbox 2). '''
        with self.SessionLocal() as session:
            record = session.query(ExportedPageVersion).filter_by(
                page_id=page_id, version_number=version_number,
                export_format=fmt
            ).first()
            if record and not record.committed_to_git:
                record.committed_to_git = True
                record.committed_at = datetime.now(timezone.utc)
                session.commit()

    def mark_attachment_committed(self, page_id: str, attachment_id: str):
        ''' Set committed_to_git flag on ExportedAttachment (checkbox 2). '''
        with self.SessionLocal() as session:
            record = session.query(ExportedAttachment).filter_by(
                page_id=page_id, attachment_id=attachment_id
            ).first()
            if record and not record.committed_to_git:
                record.committed_to_git = True
                record.committed_at = datetime.now(timezone.utc)
                session.commit()

    @staticmethod
    def _sanitize(s: str) -> str:
        ''' Same logic as Confluence.secure_string — for filename matching. '''
        return ''.join(c for c in s if c.isalnum() or c in '._- ')

    def mark_attachment_committed_by_filename(self, page_id: str,
                                               filename: str):
        ''' Match attachment by page_id + sanitized title, set checkbox 2.

        git_versioner doesn't know the attachment_id — only the filename
        on disk (which is the sanitized attachment_title from main.py).
        '''
        with self.SessionLocal() as session:
            attachments = session.query(ExportedAttachment).filter_by(
                page_id=page_id, committed_to_git=False
            ).all()
            for att in attachments:
                sanitized = self._sanitize(att.attachment_title or '')
                if sanitized == filename:
                    att.committed_to_git = True
                    att.committed_at = datetime.now(timezone.utc)
                    session.commit()
                    return


def init_tracker(database_url: str) -> ExportTracker:
    ''' Create ExportTracker and auto-run Alembic migrations. '''
    from alembic.config import Config
    from alembic import command

    alembic_ini = Path(__file__).parent / 'alembic.ini'
    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option('sqlalchemy.url', database_url)
    command.upgrade(alembic_cfg, 'head')

    return ExportTracker(database_url)
