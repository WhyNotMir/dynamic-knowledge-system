import uuid
import aiofiles
from pathlib import Path
from fastapi import UploadFile
from app.config import settings


class FileStorage:
    def __init__(self):
        self.base_dir = Path(settings.upload_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, file: UploadFile, project_id: uuid.UUID) -> str:
        project_dir = self.base_dir / str(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)

        ext = Path(file.filename).suffix.lower()
        unique_name = f"{uuid.uuid4()}{ext}"
        dest = project_dir / unique_name

        async with aiofiles.open(dest, "wb") as out:
            content = await file.read()
            await out.write(content)

        return str(dest)


file_storage = FileStorage()