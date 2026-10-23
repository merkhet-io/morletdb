.PHONY: build test demo serve
build:
	go build -o bin/morletdb ./cmd/morletdb
test:
	go test -race ./...
	go vet ./...
demo: build
	./bin/morletdb demo
serve: build
	./bin/morletdb serve
