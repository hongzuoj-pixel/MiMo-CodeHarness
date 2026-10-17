"""Bounded, hash-indexed evidence ZIPs; archives are inspected without extraction."""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import zipfile
from pathlib import Path

from mimo_harness.evidence import (
    MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, inspect_evidence, json_bytes,
    object_from_bytes, portable_name,
)
from mimo_harness.manifests import digest
from mimo_harness.offline_report import render_report


def metadata(payload):
    return {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def make_report(reader):
    report = inspect_evidence(reader)
    report["files"] = [{"name": name, "href": name} for name in sorted(reader.names)]
    report["excluded"] = ["Source worktrees and input snapshot contents", "Dependency environments",
                          "Full model responses and authentication configuration"]
    return report


def create_bundle(reader, output: Path) -> dict:
    output = output.expanduser().resolve()
    if output.exists():
        raise ValueError(f"Refusing to overwrite an existing export: {output}")
    if len(reader.names) + 3 > MAX_FILES:
        raise ValueError("Too many evidence files to export.")
    report = make_report(reader)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".mimo-export-", suffix=".zip", dir=output.parent)
    os.close(fd)
    index, total = {}, 0
    names = set()
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            def add(name, payload):
                nonlocal total
                portable_name(name)
                if name.casefold() in names:
                    raise ValueError("Evidence names collide on case-insensitive filesystems.")
                names.add(name.casefold())
                total += len(payload)
                if len(payload) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                    raise ValueError("Evidence exceeds archive size limits; no partial export was published.")
                index[name] = metadata(payload)
                archive.writestr(name, payload)
            for name in sorted(reader.names):
                add(name, reader.read(name))
            add("report.json", json_bytes(report))
            add("index.html", render_report(report).encode("utf-8"))
            inventory = json_bytes({"schema_version": 1, "renderer_version": 1,
                "format": "mimo-evidence", "run_id": reader.manifest.get("run_id"), "files": index})
            if len(inventory) > MAX_FILE_BYTES or total + len(inventory) > MAX_TOTAL_BYTES:
                raise ValueError("Archive inventory exceeds size limits.")
            archive.writestr("bundle_manifest.json", inventory)
        # Publish atomically and exclusively; never replace an existing user file.
        os.link(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return report["gate"]


class BundleReader:
    def __init__(self, path: Path):
        self.archive = zipfile.ZipFile(path)
        try:
            infos = self.archive.infolist()
            if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
                raise ValueError("Evidence archive exceeds size/file-count limits.")
            names = [portable_name(i.filename) for i in infos]
            if len({name.casefold() for name in names}) != len(names):
                raise ValueError("Duplicate or case-colliding archive entries.")
            if any(i.is_dir() or stat.S_ISLNK(i.external_attr >> 16) or i.flag_bits & 1 for i in infos):
                raise ValueError("Directory, symlink and encrypted archive entries are not supported.")
            if any(i.file_size > MAX_FILE_BYTES for i in infos):
                raise ValueError("Evidence file exceeds the per-file limit.")
            self.info = {i.filename: i for i in infos}
            index = object_from_bytes(self.read("bundle_manifest.json"))
            if not isinstance(index, dict) or index.get("schema_version") != 1 or index.get("renderer_version") != 1 or index.get("format") != "mimo-evidence" or not isinstance(index.get("files"), dict):
                raise ValueError("Unsupported evidence bundle schema.")
            if set(index["files"]) != set(names) - {"bundle_manifest.json"}:
                raise ValueError("Bundle inventory does not match its entries.")
            for name, expected in index["files"].items():
                if metadata(self.read(name)) != expected:
                    raise ValueError(f"Bundle integrity failure: {name}")
            self.manifest = object_from_bytes(self.read("provenance/run_manifest.json"))
            m = self.manifest
            if not isinstance(m, dict) or m.get("schema_version") != 1 or m.get("kind") not in {"run", "verify"} or not isinstance(m.get("inputs"), dict) or digest(m.get("inputs")) != m.get("fingerprint"):
                raise ValueError("Invalid run provenance.")
            if m.get("status") not in {"RUNNING", "COMPLETED", "ERROR", "CANCELLED"} or index["run_id"] != m.get("run_id"):
                raise ValueError("Invalid run state/identity.")
            if m.get("options") != m["inputs"].get("options"):
                raise ValueError("Run options disagree with the input fingerprint.")
            if digest(object_from_bytes(self.read("provenance/source_manifest.json"))) != m["inputs"].get("source_sha256"):
                raise ValueError("Source identity does not match bundle provenance.")
            self.names = set(names) - {"bundle_manifest.json", "index.html", "report.json"}
            for name in self.names:
                if name.startswith("evidence/"):
                    original = name[len("evidence/"):]
                    if m["status"] != "RUNNING" and index["files"][name]["sha256"] != m["artifacts"].get(original):
                        raise ValueError("Exported evidence does not match the original run seal.")
                elif name not in {"provenance/run_manifest.json", "provenance/source_manifest.json"}:
                    raise ValueError("Unexpected evidence entry.")
            if m["kind"] == "verify" and metadata(self.read("evidence/input.patch"))["sha256"] != m["inputs"].get("patch_sha256"):
                raise ValueError("Exported patch does not match run identity.")
            expected_report = make_report(self)
            if expected_report != object_from_bytes(self.read("report.json")):
                raise ValueError("Portable report disagrees with recomputed evidence.")
            if self.read("index.html") != render_report(expected_report).encode("utf-8"):
                raise ValueError("Offline HTML disagrees with verified report data.")
        except BaseException:
            self.archive.close()
            raise

    def read(self, name):
        portable_name(name)
        if name not in self.archive.namelist():
            raise ValueError(f"Missing bundle entry: {name}")
        with self.archive.open(name) as stream:
            payload = stream.read(MAX_FILE_BYTES + 1)
        if len(payload) > MAX_FILE_BYTES:
            raise ValueError("Decompressed evidence exceeds the per-file limit.")
        return payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.archive.close()
