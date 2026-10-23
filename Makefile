.PHONY: test test-go test-rust build server pg-test golden legacy-build legacy-server
GO ?= go

test: test-rust

test-go:
	cd apps/server && $(GO) test -race ./... && $(GO) vet ./...

test-rust:
	cargo test --locked -p morlet-core
	cargo fmt --all -- --check

legacy-build:
	mkdir -p bin
	cd apps/server && $(GO) build -o ../../bin/morletdb ./cmd/morletdb

legacy-server: legacy-build
	./bin/morletdb serve

pg-test:
	cd extensions/pg_morlet && cargo pgrx test pg17

golden:
	cd apps/server && $(GO) run ./cmd/golden -out ../../testdata/morlet-energy-v1

.PHONY: benchmark
benchmark:
	python3 benchmarks/run.py


# Compatibility aliases for the frozen Go demo.
build: legacy-build
server: legacy-server
