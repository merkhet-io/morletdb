.PHONY: test test-reference test-rust pg-test golden benchmark
GO ?= go

test: test-rust

test-reference:
	cd tools/reference-go && $(GO) test -race ./... && $(GO) vet ./...

test-rust:
	cargo test --locked -p morlet-core
	cargo fmt --all -- --check

pg-test:
	cd extensions/pg_morlet && cargo pgrx test pg17

# Explicit maintenance only; review all changes to frozen expectations.
golden:
	cd tools/reference-go && $(GO) run ./cmd/golden -out ../../testdata/morlet-energy-v1

benchmark:
	python3 benchmarks/run.py
