# MorletDB metrics in OpenTelemetry and Prometheus

Sends the [steward's](../../tools/steward/README.md) operational metrics to an OpenTelemetry Collector, and optionally
to Prometheus: database reachability, steward state, ingest hold, disk space, integrity passes, backlogs and table
health. No MorletDB code is added. The collector scrapes the steward's existing `/metrics` endpoint.

## Run it

```sh
cd examples/opentelemetry
export MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.4.0-beta.4
export MORLET_DB_PASSWORD=change-me MORLET_STEWARD_PASSWORD=change-me-too
docker compose up -d --build                     # database, steward, metrics endpoint, collector
docker compose logs -f otelcol                   # one "Metrics" line per batch
```

To see the metric names and values, set `verbosity: detailed` under `exporters.debug` in `otelcol.yaml`.

To use Prometheus as well, start it with its profile and open <http://127.0.0.1:9090>:

```sh
docker compose --profile prometheus up -d
```

`docker compose down -v` removes the stack **and its database volume**.

## Send it to your backend

In `otelcol.yaml`, uncomment the `otlphttp` exporter, set its endpoint and put it in the pipeline's `exporters`.
Keep TLS and credentials in the collector's configuration, not in this repository.

## Scope

- **Metrics only.** There are no traces or logs, and MCP tool calls are not instrumented.
- **Not an ingest path.** Waveforms do not go through OpenTelemetry, which is not a lossless store for recordings.
- **What leaves the box.** Labels include collection and table names. Check that before sending them to a third party.
  No samples or recording metadata are exported.
- **Network.** The metrics endpoint has no authentication. In this example it is reachable only on the Compose network.
  Do not publish its port.
- **Versions tested.** OpenTelemetry Collector Contrib 0.165.0 and Prometheus 3.5.0, with MorletDB 0.4.0-beta.4.
