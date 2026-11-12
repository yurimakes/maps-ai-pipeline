"""File boundaries for prepared inputs and immutable request envelopes."""

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from .contracts import (RequestConflict, StorageError, ValidationError, digest,
                        read_json, require, validate_bundle)


class PreparedInputs(Protocol):
    def load(self, request_path: Path) -> dict: ...


class ResultStore(Protocol):
    def save(self, envelope: dict) -> tuple[Path, bool]: ...


class LocalPreparedInputs:
    """Reads ready metadata only; never invokes ingestion, profiling or DQ."""

    def load(self, request_path):
        path = Path(request_path).resolve()
        request = read_json(path)
        require(type(request) is dict, "invalid request")
        bundle = {"request": request}
        for name in ("metadata", "dictionary", "business_definition", "target"):
            ref = request.get(name)
            require(type(ref) is dict, "missing document reference")
            if name == "target" and ref.get("provided") is False:
                bundle[name] = None
                continue
            relative = ref.get("path")
            require(type(relative) is str and relative, "missing reference path")
            target = (path.parent / relative).resolve()
            require(not Path(relative).is_absolute() and target.is_relative_to(path.parent),
                    "reference must stay within request folder")
            bundle[name] = read_json(target)
        try:
            validate_bundle(bundle)
        except (TypeError, KeyError):
            raise ValidationError("invalid input field type") from None
        return bundle


class LocalResultStore:
    """One file per scoped request; hard-link publication never replaces a result.

    Requires a local filesystem supporting atomic hard links (e.g. NTFS).
    Cloud object storage requires its own conditional-create adapter.
    """

    def __init__(self, root):
        self.root = Path(root).resolve()

    def path_for(self, bundle):
        req = bundle["request"]
        parts = [req[k] for k in ("customer_id", "project_id", "batch_id")]
        result = self.root.joinpath(*parts, req["request_id"] + ".json")
        if not result.is_relative_to(self.root):
            raise StorageError("result path outside storage root")
        # Do not resolve a concurrently published hard link. Validate IDs before
        # constructing this path and reject redirection below the storage root.
        for candidate in (result, *result.parents):
            if candidate == self.root:
                break
            if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
                raise StorageError("result storage redirection is not allowed")
        return result

    def save(self, envelope):
        from .pipeline import validate_envelope

        validate_envelope(envelope)
        path = self.path_for(envelope["input"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return self._existing(path, envelope)
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(envelope, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                return self._existing(path, envelope)
            except OSError:
                raise StorageError("atomic local result publication failed") from None
        finally:
            Path(temporary).unlink(missing_ok=True)
        return path, False

    @staticmethod
    def _existing(path, envelope):
        from .pipeline import validate_envelope

        saved = read_json(path)
        validate_envelope(saved)
        if digest(saved["input"]) != digest(envelope["input"]):
            raise RequestConflict("request ID already has different input")
        if saved != envelope:
            raise RequestConflict("request ID already has different processing version or output")
        return path, True
