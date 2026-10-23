package store

import (
	"context"
	"encoding/json"
	"github.com/merkhet-io/morletdb/internal/waveform"
	"math"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestPersistentSearch(t *testing.T) {
	dir := t.TempDir()
	s, _ := Open(dir)
	query := make([]float64, 512)
	for i := range query {
		query[i] = math.Sin(float64(i)*.31) + .4*math.Sin(float64(i)*.83)
	}
	samples := make([]float64, 2048)
	copy(samples[768:], query)
	r, e := s.PutRecording(waveform.Recording{Channel: "x", Unit: "g", SampleRate: 4096, Start: time.Unix(1, 0), Samples: samples})
	if e != nil {
		t.Fatal(e)
	}
	q := waveform.Query{Samples: query, Channel: "x", Unit: "g", SampleRate: 4096, Normalization: "zscore", Stride: 128, TopK: 1}
	cold, e := s.Search(context.Background(), q)
	if e != nil || len(cold.Hits) != 1 || cold.Hits[0].StartSample != 768 {
		t.Fatalf("cold: %+v %v", cold, e)
	}
	indexFiles, _ := os.ReadDir(filepath.Join(dir, "indexes"))
	if len(indexFiles) != 1 {
		t.Fatal("missing persistent index")
	}
	indexPath := filepath.Join(dir, "indexes", indexFiles[0].Name())
	before, _ := os.Stat(indexPath)
	s, _ = Open(dir)
	warm, e := s.Search(context.Background(), q)
	if e != nil || warm.Hits[0] != cold.Hits[0] {
		t.Fatalf("restart changed search: %+v %v", warm, e)
	}
	after, _ := os.Stat(indexPath)
	if !before.ModTime().Equal(after.ModTime()) {
		t.Fatal("warm index rewritten")
	}
	original, e := s.GetRecording(r.ID)
	if e != nil {
		t.Fatal(e)
	}
	for i, v := range samples {
		if original.Samples[i] != v {
			t.Fatal("binary roundtrip")
		}
	}
	os.Remove(indexPath)
	var wg sync.WaitGroup
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, e := s.Search(context.Background(), q); e != nil {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	os.WriteFile(indexPath, []byte("broken"), 0600)
	if _, e = s.Search(context.Background(), q); e == nil {
		t.Fatal("silent index corruption")
	}
	os.Remove(indexPath)
	if _, e = s.Search(context.Background(), q); e != nil {
		t.Fatal("rebuild failed", e)
	}
	os.WriteFile(filepath.Join(dir, "recordings", r.ID+".f64"), []byte("broken"), 0600)
	if _, e = s.GetRecording(r.ID); e == nil {
		t.Fatal("silent sample corruption")
	}
}
func TestLegacyRecordingAndOrphans(t *testing.T) {
	dir := t.TempDir()
	s, _ := Open(dir)
	os.MkdirAll(filepath.Join(dir, "recordings"), 0700)
	r := waveform.Recording{ID: "0123456789abcdef0123456789abcdef", Channel: "x", Unit: "g", SampleRate: 4096, Start: time.Unix(1, 0), Samples: make([]float64, 1024)}
	b, _ := json.Marshal(r)
	os.WriteFile(filepath.Join(dir, "recordings", r.ID+".json"), b, 0600)
	os.WriteFile(filepath.Join(dir, "recordings", "orphan.f64"), []byte("ignored"), 0600)
	list, e := s.RecordingSummaries()
	if e != nil || len(list) != 1 || list[0].SampleCount != 1024 || len(list[0].Samples) != 0 {
		t.Fatalf("legacy summary %+v %v", list, e)
	}
	q := waveform.Query{Samples: make([]float64, 512), Channel: "x", Unit: "g", SampleRate: 4096, Normalization: "demean", TopK: 1}
	if _, e = s.Search(context.Background(), q); e != nil {
		t.Fatal(e)
	}
}
func BenchmarkWarmSearch(b *testing.B) {
	s, _ := Open(b.TempDir())
	x := make([]float64, 4096)
	for i := range x {
		x[i] = math.Sin(float64(i) * .31)
	}
	s.PutRecording(waveform.Recording{Channel: "x", Unit: "g", SampleRate: 4096, Start: time.Unix(1, 0), Samples: x})
	q := waveform.Query{Samples: x[:512], Channel: "x", Unit: "g", SampleRate: 4096, Normalization: "zscore", TopK: 5, Stride: 128}
	if _, e := s.Search(context.Background(), q); e != nil {
		b.Fatal(e)
	}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, e := s.Search(context.Background(), q); e != nil {
			b.Fatal(e)
		}
	}
}
