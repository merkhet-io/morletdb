package monitor

import (
	"math"
	"testing"
	"time"

	"github.com/merkhet-io/morletdb/internal/signal"
	"github.com/merkhet-io/morletdb/internal/store"
)

func record(id string, f float64) store.Record {
	c := signal.Capture{Context: signal.Context{Machine: "cnc", Sensor: "x", Operation: "cut", Tool: "t", Unit: "g", RPM: 6000}, Start: time.Unix(1, 0), SampleRate: 4096, Samples: make([]float64, 2048)}
	for i := range c.Samples {
		v := float64(i) / 4096
		c.Samples[i] = .3*math.Sin(2*math.Pi*100*v) + .15*math.Sin(2*math.Pi*400*v) + f*math.Sin(2*math.Pi*950*v)
	}
	s, _ := signal.Extract(c)
	return store.Record{ID: id, Capture: c, Signature: s}
}
func TestBaselineAndRetrieval(t *testing.T) {
	refs := []store.Record{}
	for _, id := range []string{"a", "b", "c", "d", "e"} {
		refs = append(refs, record(id, 0))
	}
	q := record("q", 0)
	a, e := Assess(q, refs)
	if e != nil || a.Deviation {
		t.Fatalf("healthy: %+v %v", a, e)
	}
	bad := record("bad", .9)
	a, e = Assess(bad, refs)
	if e != nil || !a.Deviation {
		t.Fatalf("chatter: %+v %v", a, e)
	}
	matches := Nearest(q, append(refs, bad), 3)
	if len(matches) != 3 || matches[0].Distance != 0 {
		t.Fatal(matches)
	}
	refs[0].Capture.Context.RPM = 6100
	if _, e = Assess(q, refs); e == nil {
		t.Fatal("mixed conditions accepted")
	}
	if len(Nearest(q, refs[:1], 5)) != 0 {
		t.Fatal("incompatible match")
	}
}
func TestReferenceLeakage(t *testing.T) {
	q := record("q", 0)
	refs := []store.Record{q, q, q, q, q}
	if _, e := Assess(q, refs); e == nil {
		t.Fatal("self baseline accepted")
	}
	for i := range refs {
		refs[i].ID = "other"
	}
	if _, e := Assess(q, refs); e == nil {
		t.Fatal("duplicates accepted")
	}
}
