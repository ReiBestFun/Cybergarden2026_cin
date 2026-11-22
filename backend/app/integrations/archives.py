"""Read a bounded ZIP source in an isolated, temporary directory."""

import ntpath
import stat
import zlib
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from ..analyzers.models import ScanError, Snapshot, Warning, safe_path
from .repository import RepositorySnapshot, _training_catalog

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 4096


def _portable_path(name: str) -> bool:
    parts = name.split("/")
    if not safe_path(name) or len(name) > 1024 or len(parts) > 65:
        return False
    # Python 3.12 uses pathlib for Windows device names. Later versions expose ntpath.isreserved.
    reserved = getattr(ntpath, "isreserved", None)
    return all(not part.endswith((" ", ".")) and not (reserved(part) if reserved else PureWindowsPath(part).is_reserved())
               for part in parts)


@contextmanager
def archive_directory(content: bytes):
    if len(content) > MAX_ARCHIVE_BYTES:
        raise ScanError(Warning("ARCHIVE_TOO_LARGE", "ZIP должен быть не больше 8 МиБ."))
    with TemporaryDirectory(prefix="workflow-zip-") as temporary:
        root = Path(temporary)
        try:
            with ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_ENTRIES or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
                    raise ScanError(Warning("ARCHIVE_LIMIT", "В ZIP допускается до 4096 записей и 32 МиБ распакованных данных."))
                seen = set()
                files = 0
                for item in entries:
                    name = item.orig_filename.removesuffix("/")
                    kind = stat.S_IFMT(item.external_attr >> 16)
                    if (not _portable_path(name) or name in seen
                            or kind not in (0, stat.S_IFDIR if item.is_dir() else stat.S_IFREG)
                            or item.flag_bits & 1 or item.compress_type not in (ZIP_STORED, ZIP_DEFLATED)):
                        raise ScanError(Warning("INVALID_ARCHIVE_ENTRY", "ZIP должен содержать обычные файлы и папки без ссылок и шифрования."))
                    seen.add(name)
                    target = root / name
                    if item.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as source, target.open("xb") as output:
                        written = 0
                        while block := source.read(64 * 1024):
                            written += len(block)
                            if written > item.file_size:
                                raise BadZipFile("size")
                            output.write(block)
                        if written != item.file_size:
                            raise BadZipFile("size")
                    files += 1
                if not files:
                    raise ScanError(Warning("EMPTY_ARCHIVE", "В ZIP нет файлов."))
        except (BadZipFile, OSError, EOFError, ValueError, RuntimeError, NotImplementedError, zlib.error):
            raise ScanError(Warning("INVALID_ARCHIVE", "Не удалось прочитать ZIP. Проверьте целостность архива.")) from None

        # GitHub downloads include one outer folder around the training project and catalog.
        children = list(root.iterdir())
        if len(children) == 1 and children[0].is_dir() and (children[0] / "training-project").is_dir():
            root = children[0]
        yield root


@contextmanager
def prepare_archive(content: bytes, digest: str, catalog_path: Path | None):
    with archive_directory(content) as root:
        project = root / "training-project"
        snapshot = Snapshot.from_directory(project if project.is_dir() else root, digest)
        yield RepositorySnapshot(snapshot, catalog_path or _training_catalog(root, pinned=True))
