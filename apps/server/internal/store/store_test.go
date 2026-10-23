package store

import (
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/merkhet-io/morletdb/internal/signal"
)

func TestPersistenceConcurrentWritesAndCorruption(t *testing.T) {
	dir := t.TempDir()
	s, e := Open(dir)
	if e != nil {
		t.Fatal(e)
	}
	c := signal.Capture{Context: signal.Context{Machine: "m", Sensor: "s", Operation: "o", Tool: "t", Unit: "g", RPM: 1000}, Start: time.Unix(1, 0), SampleRate: 1024, Samples: make([]float64, 512)}
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, e := s.Put(c); e != nil {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	s, _ = Open(dir)
	v, e := s.List()
	if e != nil || len(v) != 8 {
		t.Fatalf("%d %v", len(v), e)
	}
	if _, e := s.Get("../bad"); e == nil {
		t.Fatal("path traversal")
	}
	os.WriteFile(filepath.Join(dir, v[0].ID+".json"), []byte("broken"), 0600)
	if _, e := s.List(); e == nil {
		t.Fatal("silent corruption")
	}
}
