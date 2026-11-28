"""Versioned wire contracts for native MCP evidence tools; no SQL or numerical implementation."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


Offset = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
Positive = Annotated[int, Field(strict=True, ge=1)]
Name = Annotated[str, Field(strict=True, min_length=1)]
Number = Annotated[float, Field(allow_inf_nan=False)]
JSONObject = dict[str, JsonValue]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class IntervalRef(Contract):
    """Exact zero-based, half-open sample coordinates; end_sample must exceed start_sample."""
    collection: Name
    recording_id: Name
    start_sample: Offset
    end_sample: Offset

    @model_validator(mode="after")
    def ordered(self):
        if self.end_sample <= self.start_sample:
            raise ValueError("end_sample must be greater than start_sample (half-open interval)")
        return self


class InvestigationRequest(Contract):
    query: IntervalRef
    metadata_filter: JSONObject = Field(default_factory=dict, description="JSON containment filter, not SQL.")
    top_k: Annotated[int, Field(strict=True, ge=1, le=20)] = 5
    segment_id: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)] | None = None
    match_keys: list[Name] = Field(default_factory=list, description="Context-history keys; require segment_id.")
    exclude_labels: list[Name] = Field(default_factory=list, description="Context-history labels; require segment_id.")
    detailed_coverage: Annotated[bool, Field(strict=True)] = False
    max_candidates: Annotated[int, Field(strict=True, ge=1, le=1_000_000)] | None = Field(
        default=None, description="May only lower the server cap; null uses that cap.")
    max_history: Annotated[int, Field(strict=True, ge=1, le=1_000_000)] | None = Field(
        default=None, description="May only lower the independent server context-history cap.")

    @model_validator(mode="after")
    def compatible(self):
        n = self.query.end_sample - self.query.start_sample
        if n < 512 or n > 32768 or n & (n - 1):
            raise ValueError("query length must be a power of two in 512..32768 and match a stored window")
        if self.segment_id is None and (self.match_keys or self.exclude_labels):
            raise ValueError("match_keys and exclude_labels require segment_id")
        return self


class ReadRequest(Contract):
    query: IntervalRef
    max_points: Annotated[int, Field(strict=True, ge=10, le=20000)] = 1000

    @model_validator(mode="after")
    def bounded(self):
        if self.query.end_sample - self.query.start_sample > 2_000_000:
            raise ValueError("read span exceeds 2000000 samples")
        return self


class Recording(Contract):
    collection: str
    recording_id: str
    sample_rate_hz: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    channel: str
    unit: str
    samples: Offset
    start_ns: int | None
    metadata: JSONObject


class IntervalStats(Contract):
    sample_count: Positive
    min: Number
    max: Number
    mean: Number
    rms: Number


class Match(Contract):
    rank: Positive
    ref: IntervalRef
    distance: Number = Field(description="Envelope-shape distance, not probability or diagnosis.")
    metadata: JSONObject
    evidence: None = None  # This composed investigation does not request SDK search(evidence=True).
    interpretation_revision: int | None = None


class Search(Contract):
    query: IntervalRef
    matches: list[Match]
    mode: Literal["exact", "probes"]
    max_lag_fraction: Number
    interpretation_revisions: dict[str, int] | None = None


CoverageState = Literal["searchable", "not_indexed", "not_searchable", "represented", "summarized", "gone", "missing"]


class CoverageRange(Contract):
    recording_id: str
    start_sample: Offset
    end_sample: Offset
    state: CoverageState
    windows: int | None


class Scope(Contract):
    status: Literal["available", "unknown"]
    basis: Literal["compatible_recordings_before_source_exclusion"]
    samples_by_state: dict[CoverageState, int]
    range_count: int
    ranges_included: bool
    candidate_completeness: Literal["unknown", "all_eligible_stored_windows"]
    ranges: list[CoverageRange] | None = None


class Comparison(Contract):
    value_name: str
    value: Number | None
    hist_n: int | None
    hist_median: Number | None
    relative_deviation: Number | None
    robust_z: Number | None
    quantile: Number | None
    spread: Number | None
    calibrated_deviation: Number | None
    status: str
    query_occurrence: JSONObject


class Label(Contract):
    label_id: int
    label: str
    note: str | None


class Neighbor(Contract):
    neighbor_segment_id: int
    recording_id: str
    start_sample: Offset
    end_sample: Offset
    occurrence: JSONObject
    distance: Number | None
    vals: list[Number | None]
    labels: list[Label]
    ref: IntervalRef


class LabelDefinition(Contract):
    label: str
    definition: str
    unit: str | None
    source: str | None
    defined_by: str
    defined_at: str


class Context(Contract):
    segment_id: int
    ref: IntervalRef
    context: JSONObject
    occurrence: JSONObject
    spec: JSONObject
    value_rules: str
    comparisons: list[Comparison]
    neighbors: list[Neighbor]
    label_definitions: list[LabelDefinition]


class Settings(Contract):
    metadata_filter: JSONObject
    top_k: int
    max_lag_fraction: Number
    max_candidates: int
    match_keys: list[str]
    exclude_labels: list[str]
    min_history: int
    max_history: int
    timeout_ms: int
    max_recordings: int
    max_coverage_ranges: int
    max_output_bytes: int
    segment_id: int | None
    exclude_source: Literal[True]
    suppress_overlaps: Literal[True]
    before_only: Literal[False]
    coverage_ranges: bool


class Provenance(Contract):
    database: str
    role: str
    isolation: str
    transaction_started_at: str
    snapshot: str = Field(description="Diagnostic identifier; cannot reopen this snapshot later.")
    extension_version: str | None
    collection_oid: int
    collection_schema: str
    collection_name: str
    compatible_recordings: int
    envelope_representation: str
    context_representation: str | None = None
    interpretation_revisions: dict[str, int] | None = None


class ToolLimits(Contract):
    timeout_ms: int
    max_output_bytes: int
    max_candidates: int
    max_history: int
    max_recordings: int
    max_coverage_ranges: int


EvidenceState = Literal["no_temporal_cutoff", "coverage_unknown", "unsearched_ranges", "approximate_candidates_possible",
                        "no_matches_returned", "context_not_requested", "insufficient_history", "undefined_labels",
                        "labelled_history_not_exhaustively_searched"]


class InvestigationEvidence(Contract):
    kind: Literal["investigation"] = "investigation"
    version: Literal[2] = 2
    status: Literal["complete"] = "complete"
    recording: Recording
    search: Search
    query_stats: IntervalStats
    context: Context | None
    settings: Settings
    provenance: Provenance
    notes: list[str]
    scope: Scope
    evidence_states: list[EvidenceState]
    tool_limits: ToolLimits

    @model_validator(mode="after")
    def evidence_consistent(self):
        if "no_temporal_cutoff" not in self.evidence_states:
            raise ValueError("retrospective investigation must declare no_temporal_cutoff")
        expected = "unknown" if self.search.mode == "probes" else "all_eligible_stored_windows"
        if self.scope.candidate_completeness != expected:
            raise ValueError("candidate completeness disagrees with search mode")
        if self.scope.status == "unknown" and "coverage_unknown" not in self.evidence_states:
            raise ValueError("unknown coverage must be explicit")
        if self.scope.ranges_included != (self.scope.ranges is not None):
            raise ValueError("ranges_included disagrees with coverage ranges")
        if self.scope.ranges is not None and len(self.scope.ranges) != self.scope.range_count:
            raise ValueError("coverage range count disagrees with returned ranges")
        if not self.search.matches and "no_matches_returned" not in self.evidence_states:
            raise ValueError("empty matches must be explicit")
        if self.search.mode == "probes" and "approximate_candidates_possible" not in self.evidence_states:
            raise ValueError("probe candidate uncertainty must be explicit")
        if self.context is None and "context_not_requested" not in self.evidence_states:
            raise ValueError("unrequested context must be explicit")
        q = self.search.query
        if (q.collection, q.recording_id) != (self.recording.collection, self.recording.recording_id):
            raise ValueError("query and recording identities disagree")
        if q.end_sample > self.recording.samples or self.query_stats.sample_count != q.end_sample - q.start_sample:
            raise ValueError("query statistics must cover the requested recording interval")
        return self


class SampleValues(Contract):
    kind: Literal["samples"] = "samples"
    values: list[Number]


class Bucket(Contract):
    start_sample: Offset
    end_sample: Offset
    min: Number
    max: Number


class MinMaxSummary(Contract):
    kind: Literal["min_max_summary"] = "min_max_summary"
    buckets: list[Bucket]


class ReadProvenance(Contract):
    database: str
    role: str
    isolation: str
    transaction_started_at: str
    snapshot: str = Field(description="Diagnostic identifier; not a durable snapshot handle.")
    extension_version: str | None


class ReadLimits(Contract):
    timeout_ms: int
    max_output_bytes: int
    max_points: int
    max_span_samples: Literal[2000000] = 2_000_000


class ReadEvidence(Contract):
    kind: Literal["interval_read"] = "interval_read"
    version: Literal[2] = 2
    ref: IntervalRef
    recording: Recording
    stats: IntervalStats
    data: Annotated[SampleValues | MinMaxSummary, Field(discriminator="kind")]
    provenance: ReadProvenance
    tool_limits: ReadLimits
    notes: list[str]

    @model_validator(mode="after")
    def complete_interval(self):
        length = self.ref.end_sample - self.ref.start_sample
        if (self.ref.collection, self.ref.recording_id) != (self.recording.collection, self.recording.recording_id):
            raise ValueError("reference and recording identities disagree")
        if self.ref.end_sample > self.recording.samples:
            raise ValueError("reference exceeds recording length")
        if self.stats.sample_count != length:
            raise ValueError("statistics do not cover the requested interval")
        if isinstance(self.data, SampleValues):
            if len(self.data.values) != length:
                raise ValueError("sample count does not cover the requested interval")
        else:
            start = self.ref.start_sample
            for bucket in self.data.buckets:
                if bucket.start_sample != start or bucket.end_sample <= start or bucket.min > bucket.max:
                    raise ValueError("invalid or noncontiguous summary bucket")
                start = bucket.end_sample
            if start != self.ref.end_sample:
                raise ValueError("summary buckets do not cover the requested interval")
        return self


class EvidenceError(Contract):
    code: Literal["busy", "budget_exceeded", "response_too_large", "invalid_input", "evidence_unavailable", "unsupported",
                  "access_denied", "database_error", "sdk_unavailable", "configuration_error", "contract_error"]
    message: str
    suggestion: str


def investigation_result(payload):
    return InvestigationEvidence.model_validate({**payload, "version": 2})
