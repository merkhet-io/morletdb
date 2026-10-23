package waveform

import (
	"context"
	"math"
	"testing"
	"time"
)

func event(n int, amplitude float64) []float64 {
	x := make([]float64, n)
	for i := range x {
		x[i] = amplitude * (math.Sin(float64(i)*.31) + .4*math.Sin(float64(i)*.83))
	}
	return x
}
func recording(id string, x []float64) Recording {
	return Recording{ID: id, Channel: "x", Unit: "g", SampleRate: 4096, Start: time.Unix(1, 0), Samples: x, Metadata: map[string]string{"family": "test"}}
}
func TestRetrieval(t *testing.T) {
	query := event(512, 1)
	x := make([]float64, 2048)
	copy(x[768:], event(512, 3))
	records := []Recording{recording("target", x), recording("distractor", event(2048, .05))}
	q := Query{Samples: query, SampleRate: 4096, Unit: "g", Channel: "x", Normalization: "zscore", TopK: 1, Stride: 128, Filter: map[string]string{"family": "test"}}
	got, e := Search(context.Background(), records, q)
	if e != nil {
		t.Fatal(e)
	}
	if len(got.Hits) != 1 || got.Hits[0].RecordingID != "target" || got.Hits[0].StartSample != 768 || got.Hits[0].Distance > 1e-10 {
		t.Fatalf("incorrect localization: %+v", got)
	}
	q.Normalization = "demean"
	got, e = Search(context.Background(), []Recording{recording("scaled", event(512, 3))}, q)
	if e != nil || got.Hits[0].Distance < .01 {
		t.Fatalf("amplitude lost: %+v %v", got, e)
	}
	q.Filter = map[string]string{"missing": ""}
	got, e = Search(context.Background(), records, q)
	if e != nil || len(got.Hits) != 0 {
		t.Fatal("missing metadata matched")
	}
}
func TestIntervalAndValidation(t *testing.T) {
	r := recording("source", event(2048, 1))
	q := Query{RecordingID: "source", StartSample: 512, SampleCount: 512, SampleRate: 4096, Unit: "g", Channel: "x", Normalization: "demean", TopK: 100, Stride: 128}
	got, e := Search(context.Background(), []Recording{r}, q)
	if e != nil {
		t.Fatal(e)
	}
	for _, h := range got.Hits {
		if h.StartSample < 1024 && h.StartSample+512 > 512 {
			t.Fatal("self overlap")
		}
	}
	q.Normalization = "none"
	if _, e = Search(context.Background(), []Recording{r}, q); e == nil {
		t.Fatal("unsupported normalization")
	}
	q.Normalization = "demean"
	q.StartSample = math.MaxInt
	if _, e = Search(context.Background(), []Recording{r}, q); e == nil {
		t.Fatal("out of bounds")
	}
}
func TestNoiseAndSuppression(t *testing.T) {
	query := event(512, 1)
	x := make([]float64, 4096)
	for i, v := range query {
		x[1536+i] = v + .01*math.Sin(float64(i)*1.71)
	}
	q := Query{Samples: query, SampleRate: 4096, Channel: "x", Unit: "g", Normalization: "zscore", Stride: 64, TopK: 5}
	got, e := Search(context.Background(), []Recording{recording("noisy", x)}, q)
	if e != nil {
		t.Fatal(e)
	}
	if got.Hits[0].StartSample != 1536 {
		t.Fatalf("noisy event not localized: %+v", got)
	}
	for i, a := range got.Hits {
		for _, b := range got.Hits[i+1:] {
			if a.StartSample < b.StartSample+512 && b.StartSample < a.StartSample+512 {
				t.Fatal("overlapping hits")
			}
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, e = Search(ctx, []Recording{recording("noisy", x)}, q); e == nil {
		t.Fatal("ignored cancellation")
	}
}
