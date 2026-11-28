"""Versioned, read-only discovery. Catalog availability is not evidence completeness."""
import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from mcp.server.mcpserver.exceptions import ToolError


VERSION = 1


class OperationSummary(BaseModel):
    name: str
    summary: str
    contract_hash: str


class OperationIndex(BaseModel):
    kind: Literal["operation_index"] = "operation_index"
    version: Literal[1] = VERSION
    operations: list[OperationSummary]


class OperationExplanation(BaseModel):
    kind: Literal["operation_explanation"] = "operation_explanation"
    version: Literal[1] = VERSION
    name: str
    contract_hash: str
    path: str
    definition: Any
    definitions: dict[str, Any] = Field(default_factory=dict)


class RelationCapability(BaseModel):
    present: bool
    selectable: bool


class SQLCapability(BaseModel):
    signature: str
    executable: bool


class CollectionDescription(BaseModel):
    kind: Literal["collection_description"] = "collection_description"
    version: Literal[1] = VERSION
    collection: str
    role: str
    sql_version: str | None
    relations: dict[str, RelationCapability]
    probe_tables: dict[str, RelationCapability]
    sql_apis: list[SQLCapability]
    stored_window_sizes: None = None
    coverage: Literal["not_checked"] = "not_checked"
    metadata_schema: Literal["undeclared"] = "undeclared"
    notes: list[str]


# These are semantic qualifications, not replacement JSON Schemas. In particular, old tools
# clamp some arguments and accept strings: discovery must not silently invent a new API.
SEMANTICS = {
    "investigate_interval_v2": {
        "stability": "experimental",
        "temporal_scope": "No temporal cutoff; later occurrences and annotations may participate.",
        "complete": "Call completed; not a claim of full archive coverage or exhaustive probe candidates.",
        "interval": "Half-open; end > start; power-of-two length 512..32768 matching a stored search window.",
        "context": "match_keys and exclude_labels require segment_id and apply only to context history.",
        "distance": "Envelope-shape distance, not probability, diagnosis or sample equality.",
    },
    "read_interval_v2": {
        "stability": "experimental",
        "interval": "Half-open; end > start; at most 2000000 samples.",
        "representation": "data.kind distinguishes original sample values from min/max summary buckets.",
        "transport": "For bit-exact binary transfer use the SDK; JSON consumers must preserve numerical precision.",
    },
    "investigate_interval": {
        "top_k": {"minimum": 1, "maximum": 20, "on_invalid": "error"},
        "interval": "Half-open sample offsets; length must match a stored search window.",
        "temporal_scope": "No temporal cutoff; later occurrences and annotations may participate.",
        "coverage": "Compatible recordings before source exclusion; not probe recall.",
        "limits": "Server time, candidate and output caps apply; failure is not an empty result.",
    },
    "search_similar": {
        "top_k": {"minimum": 1, "maximum": 100, "on_invalid": "clamp"},
        "shifts": {"minimum": 0, "maximum": 4, "on_invalid": "clamp"},
        "interval": "Half-open sample offsets; length must match a stored search window.",
        "eligibility": "Same sample rate, channel and unit; metadata containment filter.",
        "distance": "Envelope shape distance, not probability, diagnosis or sample equality.",
        "coverage": "Inspect scope; no match does not establish absence in unsearched data.",
        "before_only": "Earlier signal intervals; does not establish historical annotation availability.",
    },
    "explain_match": {
        "stability": "experimental; requires SQL 0.3.16 or later and the SDK",
        "interval": "Zero-based, half-open query interval; match starts at match_start and has the same length. "
                    "Length must be a stored power-of-two window of 512..32768 samples. "
                    "Use the query and stored window from the search, with the same sample rate, channel and unit.",
        "query_shift_samples": "Pass the hit's query_shift_samples: it shifts both query endpoints. "
                               "Omitting it for a shifted hit explains another score.",
        "contribution_bins": {"minimum": 1, "maximum": 64, "on_invalid": "error"},
        "interpretation": "Contributions explain the similarity score, not a fault's cause. Envelopes are normalized; "
                          "compare query_stats and match_stats for amplitude. Timing is no finer than step_samples.",
        "lag": "Uses the server search setting max_lag_fraction=0.075; another tolerance explains another score. "
               "Positive lag means the query feature lies that many envelope steps earlier in the match.",
        "interpretation_revisions": "Reported from SQL 0.3.18 only when interpretation history is tracked and "
                                    "completely readable by this role. Absent never means revision 0 or unchanged.",
        "raw_evidence": "match_raw_status=unavailable can accompany an explained score when match samples "
                        "were removed or are hidden; it does not establish raw sample equality.",
        "failures": "evidence_unavailable, budget_exceeded, access_denied and other tool errors are not negative "
                    "results. Server statement timeout and shared admission limits apply.",
    },
    "resolve_incident": {
        "stability": "experimental; requires SQL 0.3.15 or later and the SDK",
        "incident_time": "ISO 8601 with an explicit UTC offset or Z; never infer the plant timezone.",
        "interval": "UTC window [incident - before_s, incident + after_s); each candidate is one recording's "
                    "zero-based, half-open sample interval. Recordings are never concatenated or resampled.",
        "window_seconds": {"per_argument_minimum": 0, "per_argument_maximum": 2678400,
                           "finite": True, "both_zero": "error", "on_invalid": "error"},
        "time_basis": "Use recording time anchors where present, otherwise start + sample rate; candidates report "
                      "their time basis and endpoint uncertainty. Unknown uncertainty is not zero; inspect "
                      "time_uncertainty_unknown, assumes_linear_clock and extrapolated_time.",
        "ambiguity": "Read states before using candidates: ambiguous_asset, asset_identity_not_scoped, "
                     "multiple_sensors and multiple_recordings_per_sensor preserve distinct identities/recordings. "
                     "Clock breaks can produce several intervals; ambiguous_time flags backward-clock ambiguity, "
                     "and partial_window flags incomplete window coverage.",
        "sensor_identity": {
            "multiple_sensors": "Candidates have different channels, or different values of the operator's "
                                "configured sensor metadata key.",
            "sensor_not_recorded": "A sensor metadata key is configured but a candidate has no value for it. "
                                   "Do not treat such candidates as one sensor or a uniquely identified sensor.",
        },
        "coverage": "asset_not_found, recordings_without_time_axis and no_recording_at_time are distinct. "
                    "No candidate is not proof that nothing was recorded or happened: permissions, missing time "
                    "axes and retention can hide evidence. Inspect coverage and unreadable_samples.",
        "identifiers": "Asset, sensor, identity and reference metadata keys are operator-configured. "
                       "References are verified only by equal stored JSON values; claims are not verified joins.",
        "limits": {
            "timeout_ms": {"default": 10000, "maximum": 300000,
                           "setting": "MORLET_MCP_INCIDENT_TIMEOUT_MS"},
            "max_recordings": {"default": 200, "maximum": 100000,
                               "setting": "MORLET_MCP_INCIDENT_MAX_RECORDINGS"},
            "max_output_bytes": {"default": 64000, "maximum": 2000000,
                                 "setting": "MORLET_MCP_INCIDENT_MAX_OUTPUT_BYTES"},
            "scope": "A positive MORLET_MCP_TIMEOUT_MS further caps the deadline. Recording limits count possible "
                     "window overlaps (anchored recordings always count). Output cap includes text and structured "
                     "results together. Shared admission limits apply; exceeding budgets fails, not a negative result.",
        },
        "lists": "Diagnostic lists of identities, recordings_without_time_axis and recordings_outside_window "
                 "show at most 100 entries with lists_truncated and exact *_count totals; candidates are not "
                 "silently truncated, and states consider every recording.",
        "next_step": "Select a power-of-two window of 512..32768 samples inside a candidate for investigate_interval; "
                     "the whole candidate usually is not a valid search window.",
    },
    "read_interval": {
        "representation": "samples if length <= max_points; otherwise min_max buckets, not exact samples.",
        "max_points": {"minimum": 10, "maximum": 20000, "on_invalid": "clamp"},
        "max_span_samples": 2_000_000,
    },
}


def contract(tool):
    result = {"version": VERSION, "name": tool.name, "description": tool.description,
            "input_schema": tool.input_schema, "output_schema": tool.output_schema,
            "annotations": tool.annotations.model_dump(mode="json", by_alias=True) if tool.annotations else None,
            "semantics": SEMANTICS.get(tool.name, {})}
    if tool.name in ("investigate_interval_v2", "read_interval_v2"):
        from evidence_contracts import EvidenceError
        result["error_schema"] = EvidenceError.model_json_schema()
        result["error_transport"] = "Runtime failures are JSON text in MCP tool errors; argument/schema failures are MCP validation errors."
    return result


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                                ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def operation_index(tools):
    return OperationIndex(operations=[OperationSummary(name=t.name,
        summary=(t.description or "").partition("\n")[0], contract_hash=digest(contract(t)))
        for t in sorted(tools, key=lambda t: t.name)])


def explain(tools, name, path=""):
    tool = next((t for t in tools if t.name == name), None)
    if tool is None:
        raise ToolError("Unknown operation. Use list_operations to discover names.")
    document = contract(tool)
    value = document
    if path:
        if not path.startswith("/"):
            raise ToolError("path must be a JSON Pointer, e.g. /input_schema/properties/top_k")
        try:
            for token in path[1:].split("/"):
                # Reject malformed escapes rather than interpreting a different field.
                if re.search(r"~(?![01])", token):
                    raise ValueError()
                key = token.replace("~1", "/").replace("~0", "~")
                if isinstance(value, list):
                    if not re.fullmatch(r"0|[1-9][0-9]*", key):
                        raise ValueError()
                    value = value[int(key)]
                else:
                    value = value[key]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ToolError("Unknown schema path. Explain the operation without a path first.") from None
    definitions = {}
    # A selected fragment can contain local references. Carry its root definitions alongside it.
    for section in ("input_schema", "output_schema", "error_schema"):
        if path.startswith("/" + section + "/") and isinstance(document.get(section), dict):
            definitions = document[section].get("$defs", {})
    return OperationExplanation(name=name, contract_hash=digest(document), path=path,
                                definition=value, definitions=definitions)


def describe_collection(conn, collection):
    """Catalog-only checks in the caller's read-only transaction; never read waveform rows."""
    row = conn.execute("""
        SELECT c.oid, n.nspname, c.relname, format('%%I.%%I', n.nspname, c.relname)
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE c.oid=pg_catalog.to_regclass(%s) AND c.relkind IN ('r','p')
          AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
          AND pg_catalog.has_table_privilege(c.oid, 'SELECT')
          AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid
                      AND a.attname='checkpoint' AND NOT a.attisdropped)
          AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid
                      AND a.attname='sample_rate_hz' AND NOT a.attisdropped)
    """, (collection,)).fetchone()
    if row is None:
        raise ToolError("Collection unavailable to this role or not a stream collection.")
    _, schema, name, qualified = row

    companions = {"chunks": "_chunks", "envelopes": "_envelopes", "time_anchors": "_time_anchors"}
    windows = (512, 1024, 2048, 4096, 8192, 16384, 32768)
    suffixes = list(companions.values()) + [f"_probes_{w}" for w in windows]
    rows = conn.execute("""
        SELECT suffix, oid IS NOT NULL,
               coalesce(pg_catalog.has_table_privilege(oid, 'SELECT'), false)
        FROM (SELECT suffix, pg_catalog.to_regclass(pg_catalog.format('%%I.%%I', %s::text,
                    %s::text || suffix)) AS oid FROM unnest(%s::text[]) AS suffix) r
    """, (schema, name, suffixes)).fetchall()
    capabilities = {suffix: RelationCapability(present=present, selectable=selectable)
                    for suffix, present, selectable in rows}
    relations = {key: capabilities[suffix] for key, suffix in companions.items()}
    if not relations["chunks"].present:
        raise ToolError("Collection unavailable to this role or not a stream collection.")
    probes = {str(w): capabilities[f"_probes_{w}"] for w in windows}
    apis = conn.execute("""
        SELECT p.oid::regprocedure::text, pg_catalog.has_function_privilege(p.oid, 'EXECUTE')
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname = ANY(%s) ORDER BY 1
    """, (["morlet_read_interval", "morlet_search_similar", "morlet_coverage",
           "morlet_interval_stats", "morlet_compare_to_history", "morlet_utc_to_sample"],)).fetchall()
    version = conn.execute("SELECT extversion FROM pg_catalog.pg_extension WHERE extname='pg_morlet'").fetchone()
    role = conn.execute("SELECT current_user").fetchone()[0]
    return CollectionDescription(collection=qualified, role=role, sql_version=version[0] if version else None,
        relations=relations, probe_tables=probes,
        sql_apis=[SQLCapability(signature=s, executable=e) for s, e in apis], notes=[
            "Catalog checks only. SELECT/EXECUTE grants do not prove row visibility or successful execution.",
            "Probe table presence does not prove populated windows, readiness, or candidate completeness.",
            "Use list_collections for caller-visible stored window sizes (it scans rows); coverage for evidence availability.",
            "Metadata is free-form JSON; no enforced field schema is advertised. Filters use JSON containment.",
            "Capabilities are not cached. Execution rechecks permissions and current evidence."])
