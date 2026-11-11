"""MorletDB Python SDK: store waveform recordings losslessly, read exact intervals, search similar intervals.

    import morletdb
    with morletdb.Client.connect("postgresql://...") as db:
        db.create_collection("public.signals")
        rec = db.ingest("public.signals", "pump-7", samples, sample_rate_hz=25_600, channel="accel-x",
                        unit="m/s^2", start_time=datetime(2026, 3, 1, tzinfo=timezone.utc))
        result = db.search("public.signals", "pump-7", 40_960)          # 4,096-sample window
        best = db.read_ref(result.matches[0].ref)                        # original samples, bit for bit
"""
from ._client import Client
from ._local import LocalStore, open
from ._errors import (ConflictError, DatabaseError, InvalidInputError, MorletError, NotFoundError,
                      UnsupportedError)
from ._types import (Evidence, EvidencePart, Interval, IntervalRef, IntervalStats, Latest, Match, NotifiedRange, Notification,
                     OverviewBucket, Recording, SearchResult, TransferCheck)

__version__ = "0.1.0a4"
__all__ = ["Client", "LocalStore", "open", "Recording", "Interval", "IntervalRef", "IntervalStats", "Latest", "Notification", "NotifiedRange", "OverviewBucket", "Match", "Evidence", "EvidencePart", "SearchResult", "TransferCheck",
           "MorletError", "InvalidInputError", "NotFoundError", "ConflictError", "UnsupportedError",
           "DatabaseError"]
