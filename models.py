''' SQLAlchemy models and ExportTracker for incremental Confluence exports. '''

from datetime import datetime, timezone

from sqlalchemy import (Column, Integer, String, DateTime, UniqueConstraint,
                        create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class ExportedPageVersion(Base):
    ''' Tracks which page versions have been exported. '''
    __tablename__ = 'exported_page_versions'

    id = Column(Integer, primary_key=True)
    page_id = Column(String, nullable=False)
    version_number = Column(Integer, nullable=False)
    page_title = Column(String)
    export_format = Column(String)
    exported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

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

    __table_args__ = (
        UniqueConstraint('page_id', 'attachment_id',
                         name='uq_page_attachment'),
    )


class ExportTracker:
    ''' Tracks export state in PostgreSQL to enable incremental exports.

    When a database_url is configured, the exporter checks which pages/versions
    and attachments have already been exported and skips them on re-runs.
    '''

    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)
        self.SessionLocal = sessionmaker(bind=self.engine)

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
