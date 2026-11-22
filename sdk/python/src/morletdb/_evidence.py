"""Portable evidence from one investigation (#575): a versioned manifest, exact sample digests and a static report.

A bundle records what was searched and what was available when it was exported. It is an explicit disclosure by the
caller. It is not a diagnosis, does not pin or restore stored data, and a digest detects a change without preserving
the samples: only intervals exported with `include_samples` can be checked without the database.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html import escape
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

import numpy as np

from ._errors import InvalidInputError, InvestigationLimitError, NotFoundError
from ._incident import _raw_samples
from ._investigation import read_snapshot
from ._types import IntervalRef

FORMAT = "morletdb-evidence"
VERSION = 1
MANIFEST = "manifest.json"
REPORT = "report.html"
LIMITATIONS = (
    "This report records results captured from one read-only snapshot. It is not a diagnosis.",
    "A similar waveform is a comparison, not a cause. Labels describe past occurrences, not this interval.",
    "A digest detects a change to the samples; it does not preserve them. Only bundled intervals can be checked offline.",
    "Re-running the search can differ after new ingestion, label revisions or compaction.",
)


def canonical(value: Any) -> bytes:
    """UTF-8 JSON with sorted keys, no whitespace and no NaN or infinity: the bytes the manifest digest covers."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def manifest_digest(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != "manifest_sha256"})).hexdigest()


def _sample_bytes(samples) -> bytes:
    return np.asarray(samples, dtype=np.float64).astype("<f8").tobytes()      # bit for bit, signed zero included


def _describe(role, rank, ref, rec, raw, digest, bundled, axis):
    name = "query" if role == "query" else f"match-{rank}"
    return {"id": name, "role": role, "rank": rank, "collection": ref.collection, "recording_id": ref.recording_id,
            "start_sample": ref.start_sample, "end_sample": ref.end_sample,
            "specification": {"sample_rate_hz": rec.sample_rate_hz, "channel": rec.channel, "unit": rec.unit,
                              "metadata": rec.metadata, "time_axis_version": axis},
            "raw_status": "available" if raw else "unavailable", "sample_sha256": digest,
            "file": f"samples/{name}.f64le" if bundled and raw else None}


@dataclass(frozen=True)
class EvidenceCheck:
    """Offline verification of a bundle. `ok` is False when any problem was found."""
    ok: bool
    manifest_sha256: str | None
    problems: tuple[str, ...]


class EvidenceBundle:
    """A manifest and, when requested, the exact samples of its intervals."""

    def __init__(self, manifest: dict[str, Any], samples: dict[str, bytes]):
        self.manifest = manifest
        self.samples = samples

    def html(self) -> str:
        return render(self.manifest)

    def write(self, path: str | os.PathLike) -> Path:
        """Write a new directory (manifest.json, report.html, samples/). An existing path is refused. Files appear
        together: the bundle is built beside the target and renamed. The directory is private to its owner (0700)."""
        target = Path(path)
        if target.exists():
            raise InvalidInputError(f"{target} already exists")
        work = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        try:
            (work / MANIFEST).write_bytes(canonical(self.manifest))
            (work / REPORT).write_bytes(self.html().encode("utf-8"))
            if self.samples:
                (work / "samples").mkdir()
            for item in self.manifest["intervals"]:
                if item["file"]:      # never a path from the manifest: a loaded bundle is untrusted
                    _sample_file(work, item).write_bytes(self.samples[item["id"]])
            os.rename(work, target)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        return target


def export_evidence(client, collection, recording_id, start_sample, end_sample, *, include_samples, max_sample_bytes,
                    settings):
    if not isinstance(include_samples, bool):
        raise InvalidInputError("include_samples must be true or false")
    if isinstance(max_sample_bytes, bool) or not isinstance(max_sample_bytes, int) or not 1 <= max_sample_bytes <= 2**31:
        raise InvalidInputError("max_sample_bytes must be an integer in 1..2147483648")
    deadline = time.monotonic() + settings["timeout_ms"] / 1000
    with read_snapshot(client.connection, deadline) as reader:
        result = reader.investigate(collection, recording_id, start_sample, end_sample,
                                    **{**settings, "timeout_ms": max(1, reader.remaining())})
        # the relation itself, not the caller's spelling, which another search_path would resolve elsewhere
        relation = reader._one("SELECT format('%%I.%%I', n.nspname, c.relname) FROM pg_class c "
                               "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=%s::regclass", (collection,))
        refs = [("query", 0, result.search.query)] + [("match", m.rank, m.ref) for m in result.search.matches]
        refs = [(role, rank, IntervalRef(relation, r.recording_id, r.start_sample, r.end_sample)) for role, rank, r in refs]
        needed = sum((r.end_sample - r.start_sample) * 8 for _, _, r in refs)
        if needed > max_sample_bytes:
            raise InvestigationLimitError(f"evidence needs {needed} sample bytes, above max_sample_bytes; "
                                          "no partial bundle was produced")
        intervals, samples = [], {}
        for role, rank, ref in refs:
            rec = reader.recording(ref.collection, ref.recording_id)
            length = ref.end_sample - ref.start_sample
            raw = _raw_samples(reader, ref.collection, ref.recording_id, ref.start_sample, ref.end_sample) == length
            digest = None
            data = None
            if raw:
                data = _sample_bytes(reader.read_ref(ref).samples)
                digest = hashlib.sha256(data).hexdigest()
            item = _describe(role, rank, ref, rec, raw, digest, include_samples, _axis(reader, ref))
            intervals.append(item)
            if include_samples and raw:
                samples[item["id"]] = data
    manifest = {"format": FORMAT, "version": VERSION,
                "digests": {"algorithm": "sha256", "manifest": "canonical JSON of every field except manifest_sha256",
                            "samples": "the interval's samples as little-endian float64 bytes, signed zero preserved"},
                "bundle": ("reference-only" if not include_samples else "exact-samples"
                           if all(i["file"] for i in intervals) else "partial-samples"),
                "intervals_without_samples": [i["id"] for i in intervals if i["raw_status"] != "available"],
                "limitations": list(LIMITATIONS), "investigation": result.to_dict(), "intervals": intervals}
    # refuses a value JSON cannot carry, and holds the whole manifest to the output budget
    if len(canonical(manifest)) > settings["max_output_bytes"]:
        raise InvestigationLimitError("the evidence manifest exceeds max_output_bytes; no partial bundle was produced")
    manifest["manifest_sha256"] = manifest_digest(manifest)
    return EvidenceBundle(manifest, samples)


def load_evidence(path) -> EvidenceBundle:
    """Read a bundle directory. Sample files are found from the interval ids, never from a path in the manifest."""
    root = Path(path)
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
        raise InvalidInputError(f"{root} is not a {FORMAT} version {VERSION} bundle")
    samples = {}
    for item in manifest["intervals"]:
        if item.get("file"):
            file = _sample_file(root, item)
            if file.is_symlink() or not file.is_file():
                raise InvalidInputError(f"{item['id']}: sample file is missing or not a regular file")
            samples[item["id"]] = file.read_bytes()
    return EvidenceBundle(manifest, samples)


def _sample_file(root, item):
    name = str(item.get("id", ""))
    if name != "query" and not (name.startswith("match-") and name[6:].isascii() and name[6:].isdigit()):
        raise InvalidInputError(f"invalid interval id in manifest: {name!r}")
    return root / "samples" / f"{name}.f64le"


def verify_evidence(path, *, expected_manifest_sha256: str | None = None) -> EvidenceCheck:
    """Check a bundle without a database: manifest digest, report and every bundled sample file. A manifest digest
    alone cannot detect someone who also recomputes it: pass a digest kept elsewhere as `expected_manifest_sha256`."""
    root = Path(path)
    problems = []
    try:
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
            raise ValueError("unknown format or version")
        digest = manifest_digest(manifest)
    except (OSError, ValueError, TypeError) as exc:
        return EvidenceCheck(False, None, (f"manifest unreadable: {exc}",))
    if manifest.get("manifest_sha256") != digest:
        problems.append("manifest content does not match manifest_sha256")
    if expected_manifest_sha256 is not None and digest != expected_manifest_sha256:
        problems.append("manifest digest differs from the expected digest")
    try:
        if (root / REPORT).read_bytes() != render(manifest).encode("utf-8"):
            problems.append("report.html differs from the report of this manifest")
    except OSError:
        problems.append("report.html is missing")
    except (KeyError, TypeError, AttributeError, ValueError):
        problems.append("the report cannot be rendered from this manifest")
    try:
        for item in manifest["intervals"]:
            if not item.get("file"):
                continue
            file = _sample_file(root, item)
            expected = (item["end_sample"] - item["start_sample"]) * 8
            if not file.is_file() or file.is_symlink():
                problems.append(f"{item['id']}: sample file is missing")
            elif file.stat().st_size != expected:
                problems.append(f"{item['id']}: sample file has the wrong size")
            elif hashlib.sha256(file.read_bytes()).hexdigest() != item["sample_sha256"]:
                problems.append(f"{item['id']}: samples differ from their digest")
    except (KeyError, TypeError, AttributeError, ValueError, OSError, InvalidInputError) as exc:
        problems.append(f"intervals unreadable: {exc}")
    return EvidenceCheck(not problems, digest, tuple(problems))


def _axis(reader, ref):
    if not reader._has("public.morlet_time_axis_version(regclass,text)"):
        return None
    return reader._one("SELECT public.morlet_time_axis_version(%s::regclass,%s)", (ref.collection, ref.recording_id))


def resolve_evidence(client, bundle, *, timeout_ms, max_sample_bytes, expected_manifest_sha256=None):
    """Each interval against the database now, under the caller's permissions, in one snapshot: `unchanged`, `changed`,
    `specification_changed`, `time_axis_changed`, `metadata_changed`, `samples_unavailable`, `missing` (or not visible to this role), or
    `no_digest` when its raw samples were already unavailable at export."""
    if not isinstance(bundle, EvidenceBundle):
        check = verify_evidence(bundle, expected_manifest_sha256=expected_manifest_sha256)
        if not check.ok:
            raise InvalidInputError("bundle failed verification: " + "; ".join(check.problems))
        bundle = load_evidence(bundle)
    manifest = bundle.manifest
    digest = manifest_digest(manifest)
    if manifest.get("manifest_sha256") != digest or expected_manifest_sha256 not in (None, digest):
        raise InvalidInputError("bundle failed verification: its manifest does not match the digest")
    needed = sum((i["end_sample"] - i["start_sample"]) * 8 for i in manifest["intervals"] if i["sample_sha256"])
    if needed > max_sample_bytes:
        raise InvestigationLimitError(f"resolving needs {needed} sample bytes, above max_sample_bytes")
    out = []
    with read_snapshot(client.connection, time.monotonic() + timeout_ms / 1000) as reader:
        for item in manifest["intervals"]:
            ref = IntervalRef(item["collection"], item["recording_id"], item["start_sample"], item["end_sample"])
            out.append({"id": item["id"], "recording_id": ref.recording_id, "start_sample": ref.start_sample,
                        "end_sample": ref.end_sample, "status": _status(reader, item, ref)})
    return out


def _status(reader, item, ref):
    try:
        rows = reader.recordings(ref.collection, recording_id=ref.recording_id)
    except NotFoundError:
        return "missing"
    if not rows:
        return "missing"
    rec, spec = rows[0], item["specification"]
    if (rec.sample_rate_hz, rec.channel, rec.unit) != (spec["sample_rate_hz"], spec["channel"], spec["unit"]):
        return "specification_changed"
    if "time_axis_version" in spec and spec["time_axis_version"] != _axis(reader, ref):
        return "time_axis_changed"
    if item["sample_sha256"] is None:
        return "no_digest"
    length = ref.end_sample - ref.start_sample
    if ref.end_sample > rec.samples or _raw_samples(reader, ref.collection, ref.recording_id, ref.start_sample,
                                                    ref.end_sample) != length:
        return "samples_unavailable"
    if hashlib.sha256(_sample_bytes(reader.read_ref(ref).samples)).hexdigest() != item["sample_sha256"]:
        return "changed"
    return "metadata_changed" if canonical(rec.metadata) != canonical(spec["metadata"]) else "unchanged"


def _table(headers, rows):
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(_text(c))}</td>" for c in row) + "</tr>" for row in rows)
    return f"<div class=w><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def _text(value):
    if value is None:
        return "not available"
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)


def render(manifest: dict[str, Any]) -> str:
    """The factual report of a manifest: deterministic, escaped, with no script, link or external asset."""
    inv = manifest["investigation"]
    intervals = manifest["intervals"]
    by_id = {i["id"]: i for i in intervals}
    query = by_id["query"]
    parts = [f"<h1>Evidence report</h1><p class=m>{escape(query['collection'])} · {escape(query['recording_id'])} · "
             f"samples [{query['start_sample']}, {query['end_sample']})</p>",
             "<h2>Read this first</h2><ul>" + "".join(f"<li>{escape(x)}</li>" for x in manifest["limitations"]) + "</ul>",
             "<h2>Evidence states</h2><ul>" + "".join(f"<li><code>{escape(s)}</code></li>" for s in inv["evidence_states"])
             + "</ul><ul>" + "".join(f"<li>{escape(n)}</li>" for n in inv["notes"]) + "</ul>",
             "<h2>Query</h2>" + _table(("Rate (Hz)", "Channel", "Unit", "Samples", "Min", "Max", "Mean", "RMS"), [(
                 query["specification"]["sample_rate_hz"], query["specification"]["channel"],
                 query["specification"]["unit"], inv["query_stats"]["sample_count"], inv["query_stats"]["min"],
                 inv["query_stats"]["max"], inv["query_stats"]["mean"], inv["query_stats"]["rms"])])]
    matches = inv["search"]["matches"]
    parts.append(f"<h2>Similar stored windows ({escape(inv['search']['mode'])} search)</h2>")
    parts.append(_table(("Rank", "Recording", "Interval", "Distance", "Raw samples"), [(
        m["rank"], m["ref"]["recording_id"], f"[{m['ref']['start_sample']}, {m['ref']['end_sample']})", m["distance"],
        by_id[f"match-{m['rank']}"]["raw_status"]) for m in matches]) if matches else "<p>No match was returned. "
        "This is not proof of absence.</p>")
    parts.append("<h2>Archive coverage</h2>" + _table(("State", "Samples"), sorted(
        inv["coverage_summary"]["samples_by_state"].items())) + f"<p>Coverage status: {escape(inv['coverage_status'])}.</p>")
    context = inv.get("context")
    if context:
        parts.append("<h2>Operating context</h2><pre>" + escape(json.dumps(context["context"], sort_keys=True,
                                                                           ensure_ascii=False)) + "</pre>")
        parts.append(_table(("Value", "Query", "History n", "History median", "Relative deviation", "Status"), [(
            r["value_name"], r["value"], r["hist_n"], r["hist_median"], r["relative_deviation"], r["status"])
            for r in context["comparisons"]]))
    parts.append("<h2>Integrity</h2>" + _table(("Interval", "Recording", "Raw", "Bundled", "SHA-256 of samples"), [(
        i["id"], i["recording_id"], i["raw_status"], "yes" if i["file"] else "no", i["sample_sha256"]) for i in intervals]))
    parts.append(f"<p class=m>Manifest SHA-256: {escape(str(manifest.get('manifest_sha256')))}<br>Bundle: "
                 f"{escape(manifest['bundle'])}</p>")
    if manifest.get("intervals_without_samples"):
        parts.append("<p>Raw samples were unavailable at export for: "
                     + escape(", ".join(manifest["intervals_without_samples"])) + ".</p>")
    parts.append("<h2>Provenance</h2>" + _table(("Field", "Value"), sorted(
        (k, v) for k, v in inv["provenance"].items())))
    style = ("body{font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:0 auto;padding:1rem;color:#1c2024;"
             "background:#fff}.m{color:#5c6470;overflow-wrap:anywhere}.w{overflow-x:auto}table{border-collapse:collapse;"
             "width:100%}th,td{border-bottom:1px solid #d9dde3;padding:.3rem .5rem;text-align:left;font-size:13px;"
             "overflow-wrap:anywhere}pre{overflow-x:auto}@media(prefers-color-scheme:dark){body{color:#e6e8eb;"
             "background:#16181c}.m{color:#a0a7b2}th,td{border-color:#343840}}")
    return ('<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,'
            f'initial-scale=1"><title>Evidence report</title><style>{style}</style></head><body>'
            + "".join(parts) + "</body></html>\n")
