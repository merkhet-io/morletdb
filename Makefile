.PHONY: test test-go test-rust build server pg-test
GO ?= go

test: test-go test-rust

test-go:
	cd apps/server && $(GO) test -race ./... && $(GO) vet ./...

test-rust:
	cargo test --locked -p morlet-core
	cargo fmt --all -- --check

build:
	mkdir -p bin
	cd apps/server && $(GO) build -o ../../bin/morletdb ./cmd/morletdb

server: build
	./bin/morletdb serve

pg-test:
	cd extensions/pg_morlet && cargo pgrx test pg17
