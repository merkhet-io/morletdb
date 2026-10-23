package store

import (
	"github.com/merkhet-io/morletdb/internal/waveform"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestRecordingRoundtrip(t *testing.T) {
	dir := t.TempDir()
	s, e := Open(dir)
	if e != nil {
		t.Fatal(e)
	}
	r, e := s.PutRecording(waveform.Recording{Channel: "voltage", Unit: "V", SampleRate: 1024, Start: time.Unix(1, 0), Samples: make([]float64, 1024)})
	if e != nil {
		t.Fatal(e)
	}
	s, _ = Open(dir)
	all, e := s.Recordings()
	if e != nil || len(all) != 1 || all[0].ID != r.ID {
		t.Fatalf("restart: %v %v", all, e)
	}
	legacy, e := s.List()
	if e != nil || len(legacy) != 0 {
		t.Fatal("recording leaked into legacy store")
	}
	if _, e = s.GetRecording("../bad"); e == nil {
		t.Fatal("invalid id accepted")
	}
	os.WriteFile(filepath.Join(dir, "recordings", r.ID+".json"), []byte("broken"), 0600)
	if _, e = s.Recordings(); e == nil {
		t.Fatal("silent corruption")
	}
}
