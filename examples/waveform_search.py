"""Dependency-free example: run the Go server, then python3 examples/waveform_search.py."""
import json
import math
import os
import urllib.request

BASE = os.environ.get("MORLETDB_URL", "http://127.0.0.1:8080")


def request(path, payload=None):
    headers = {"Content-Type": "application/json"}
    if os.environ.get("MORLETDB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["MORLETDB_TOKEN"]
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


query = [math.sin(i * .31) + .4 * math.sin(i * .83) for i in range(512)]
samples = [0.] * 2048
samples[768:1280] = [3 * value for value in query]
recording = request("/api/recordings", {
    "channel": "voltage", "unit": "V", "sample_rate_hz": 4096,
    "start": "2026-01-01T00:00:00Z", "samples": samples,
    "metadata": {"dataset": "electrical-transient-demo"},
})
result = request("/api/search", {
    "samples": query, "sample_rate_hz": 4096, "channel": "voltage", "unit": "V",
    "normalization": "zscore", "top_k": 1, "stride": 128,
    "filter": {"dataset": "electrical-transient-demo"},
})
print(json.dumps(result, indent=2))
assert result["hits"][0]["start_sample"] == 768
# Query an interval instead of uploading query samples; overlapping self-hits are excluded.
print(json.dumps(request("/api/search", {
    "recording_id": recording["id"], "start_sample": 768, "sample_count": 512,
    "sample_rate_hz": 4096, "channel": "voltage", "unit": "V",
    "normalization": "zscore", "top_k": 3, "stride": 128,
}), indent=2))
