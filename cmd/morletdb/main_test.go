package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/merkhet-io/morletdb/internal/monitor"
	"github.com/merkhet-io/morletdb/internal/store"
)

func TestDemo(t *testing.T) {
	s, _ := store.Open(t.TempDir())
	if e := demo(s); e != nil {
		t.Fatal(e)
	}
	records, e := s.List()
	if e != nil || len(records) != 10 {
		t.Fatal(e)
	}
	a, e := monitor.Assess(records[8], records[:8])
	if e != nil || a.Deviation {
		t.Fatal("healthy holdout", a, e)
	}
	a, e = monitor.Assess(records[9], records[:8])
	if e != nil || !a.Deviation {
		t.Fatal("synthetic chatter", a, e)
	}
}
func TestCSVImport(t *testing.T) {
	dir := t.TempDir()
	meta := filepath.Join(dir, "meta.json")
	csv := filepath.Join(dir, "capture.csv")
	os.WriteFile(meta, []byte(`{"context":{"machine":"m","sensor":"s","operation":"o","tool":"t","unit":"g","rpm":6000},"start":"2026-01-01T00:00:00Z","sample_rate_hz":1024}`), 0600)
	data := "value\n"
	for i := 0; i < 512; i++ {
		data += "0.1\n"
	}
	os.WriteFile(csv, []byte(data), 0600)
	if e := run([]string{"import-csv", "-data", filepath.Join(dir, "db"), "-file", csv, "-metadata", meta}); e != nil {
		t.Fatal(e)
	}
}
