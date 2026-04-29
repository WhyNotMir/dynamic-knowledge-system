import shutil
import uuid
import aiofiles
from pathlib import Path
from typing import Any
from loguru import logger
from app.config import settings


class FileStorage:
    def __init__(self):
        self.base_dir = Path(settings.upload_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, file: Any, project_id: uuid.UUID) -> str:
        project_dir = self.base_dir / str(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)

        ext = Path(file.filename).suffix.lower()
        unique_name = f"{uuid.uuid4()}{ext}"
        dest = project_dir / unique_name

        async with aiofiles.open(dest, "wb") as out:
            content = await file.read()
            await out.write(content)

        return str(dest)

    async def save_bytes(
        self,
        *,
        project_id: uuid.UUID,
        content: bytes,
        ext: str,
    ) -> str:
        project_dir = self.base_dir / str(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)

        normalised_ext = ext if ext.startswith(".") else f".{ext}"
        dest = project_dir / f"{uuid.uuid4()}{normalised_ext.lower()}"
        async with aiofiles.open(dest, "wb") as out:
            await out.write(content)
        return str(dest)

    def project_path(self, project_id: uuid.UUID, filename: str) -> Path:
        return self.base_dir / str(project_id) / filename

    def delete_file(self, storage_path: str) -> None:
        """Remove a single stored file. Safe to call if the file is already gone
        — we only care about the end state, not about historical accidents."""
        p = Path(storage_path)
        try:
            if p.is_file():
                p.unlink()
        except OSError as e:
            # Don't fail the DB delete over a stuck filesystem; log loudly so
            # an operator can reconcile later.
            logger.warning(f"Could not delete file {p}: {e}")

    def delete_project_dir(self, project_id: uuid.UUID) -> None:
        """Remove the project's entire upload subdirectory, including any files
        we might not have tracked in the DB."""
        project_dir = self.base_dir / str(project_id)
        if not project_dir.exists():
            return
        try:
            shutil.rmtree(project_dir)
        except OSError as e:
            logger.warning(f"Could not delete project dir {project_dir}: {e}")


file_storage = FileStorage()
