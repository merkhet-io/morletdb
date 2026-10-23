// Package waveform provides domain-neutral, exact windowed signal retrieval.
package waveform

import (
	"context"
	"errors"
	"fmt"
	"math"
	"sort"
	"time"

	"github.com/merkhet-io/morletdb/internal/signal"
)

const Version = "morlet-window-v1"

type Recording struct {
	ID         string            `json:"id"`
	Channel    string            `json:"channel"`
	Unit       string            `json:"unit"`
	SampleRate float64           `json:"sample_rate_hz"`
	Start      time.Time         `json:"start"`
	Metadata   map[string]string `json:"metadata,omitempty"`
	Samples    []float64         `json:"samples"`
}
type Segment struct {
	RecordingID string `json:"recording_id"`
	Channel     string `json:"channel"`
	StartSample int    `json:"start_sample"`
	SampleCount int    `json:"sample_count"`
}
type Query struct {
	Samples       []float64         `json:"samples,omitempty"`
	RecordingID   string            `json:"recording_id,omitempty"`
	StartSample   int               `json:"start_sample,omitempty"`
	SampleCount   int               `json:"sample_count,omitempty"`
	SampleRate    float64           `json:"sample_rate_hz"`
	Unit          string            `json:"unit"`
	Channel       string            `json:"channel"`
	Extractor     string            `json:"extractor"`
	Normalization string            `json:"normalization"`
	Stride        int               `json:"stride"`
	TopK          int               `json:"top_k"`
	Filter        map[string]string `json:"filter,omitempty"`
}
type Hit struct {
	Segment
	Distance     float64 `json:"distance"`
	StartSeconds float64 `json:"start_seconds"`
}
type Result struct {
	Extractor     string `json:"extractor"`
	Normalization string `json:"normalization"`
	Stride        int    `json:"stride"`
	Hits          []Hit  `json:"hits"`
}

func (r Recording) Validate() error {
	if r.Channel == "" || len(r.Channel) > 128 || r.Unit == "" || len(r.Unit) > 128 || r.Start.IsZero() {
		return errors.New("channel, unit and start required (names <=128 bytes)")
	}
	if len(r.Metadata) > 32 {
		return errors.New("at most 32 metadata entries")
	}
	for k, v := range r.Metadata {
		if k == "" || len(k) > 128 || len(v) > 512 {
			return errors.New("invalid metadata entry")
		}
	}
	return validateSamples(r.Samples, r.SampleRate, signal.MaxSamples)
}
func validateSamples(x []float64, rate float64, limit int) error {
	if math.IsNaN(rate) || math.IsInf(rate, 0) || rate < 128 || rate > 200000 {
		return errors.New("sample_rate_hz must be in [128,200000]")
	}
	if len(x) < 512 || len(x) > limit {
		return fmt.Errorf("requires 512..%d samples", limit)
	}
	for _, v := range x {
		if math.IsNaN(v) || math.IsInf(v, 0) || math.Abs(v) > 1e9 {
			return errors.New("samples must be finite and within +/-1e9")
		}
	}
	return nil
}
func features(x []float64, rate float64, normalization string) ([]float64, error) {
	// This extractor always removes DC; raw amplitude is retained in demean mode.
	y := append([]float64(nil), x...)
	mean := 0.0
	for _, v := range y {
		mean += v / float64(len(y))
	}
	sum := 0.0
	for i := range y {
		y[i] -= mean
		sum += y[i] * y[i]
	}
	if normalization == "zscore" && sum > 0 {
		scale := math.Sqrt(sum / float64(len(y)))
		for i := range y {
			y[i] /= scale
		}
	}
	c := signal.Capture{Context: signal.Context{Machine: "waveform", Sensor: "waveform", Operation: "waveform", Tool: "waveform", Unit: "waveform", RPM: 1}, Start: time.Unix(1, 0), SampleRate: rate, Samples: y}
	s, e := signal.Extract(c)
	if e != nil {
		return nil, e
	}
	return s.Features(), nil
}

// Search is exact over the configured window grid, not all possible offsets.
// Overlapping results are suppressed in distance order, retaining the best interval.
func Search(ctx context.Context, records []Recording, q Query) (Result, error) {
	out := Result{Extractor: Version, Normalization: q.Normalization, Hits: []Hit{}}
	if q.Extractor != "" && q.Extractor != Version {
		return out, errors.New("unsupported extractor")
	}
	if q.Normalization != "demean" && q.Normalization != "zscore" {
		return out, errors.New("normalization must be demean or zscore")
	}
	if q.TopK < 1 || q.TopK > 100 {
		return out, errors.New("top_k must be 1..100")
	}
	if q.Channel == "" || q.Unit == "" {
		return out, errors.New("channel and unit required")
	}
	if q.RecordingID != "" {
		if len(q.Samples) > 0 {
			return out, errors.New("choose samples or recording interval")
		}
		found := false
		for _, r := range records {
			if r.ID == q.RecordingID {
				if q.StartSample < 0 || q.SampleCount < 512 || q.StartSample > len(r.Samples) || q.SampleCount > len(r.Samples)-q.StartSample {
					return out, errors.New("invalid query interval")
				}
				if q.SampleRate != r.SampleRate || q.Channel != r.Channel || q.Unit != r.Unit {
					return out, errors.New("query metadata does not match recording")
				}
				q.Samples = r.Samples[q.StartSample : q.StartSample+q.SampleCount]
				found = true
				break
			}
		}
		if !found {
			return out, errors.New("query recording not found")
		}
	} else if q.StartSample != 0 || q.SampleCount != 0 {
		return out, errors.New("interval requires recording_id")
	}
	if e := validateSamples(q.Samples, q.SampleRate, 4096); e != nil {
		return out, e
	}
	n := len(q.Samples)
	if q.Stride == 0 {
		q.Stride = n / 2
	}
	if q.Stride < 1 || q.Stride > n {
		return out, errors.New("stride must be 1..query length")
	}
	out.Stride = q.Stride
	target, e := features(q.Samples, q.SampleRate, q.Normalization)
	if e != nil {
		return out, e
	}
	candidates := []Hit{}
	work := 0
	for _, r := range records {
		if r.SampleRate != q.SampleRate || r.Channel != q.Channel || r.Unit != q.Unit {
			continue
		}
		compatible := true
		for k, v := range q.Filter {
			actual, ok := r.Metadata[k]
			if !ok || actual != v {
				compatible = false
				break
			}
		}
		if !compatible {
			continue
		}
		for offset := 0; offset+n <= len(r.Samples); offset += q.Stride {
			if e := ctx.Err(); e != nil {
				return out, e
			}
			work++
			if work > 4096 {
				return out, errors.New("search exceeds 4096-window budget; increase stride or narrow filters")
			}
			if r.ID == q.RecordingID && offset < q.StartSample+n && q.StartSample < offset+n {
				continue
			}
			f, e := features(r.Samples[offset:offset+n], r.SampleRate, q.Normalization)
			if e != nil {
				return out, e
			}
			d := 0.0
			for i, v := range target {
				delta := v - f[i]
				d += delta * delta
			}
			candidates = append(candidates, Hit{Segment: Segment{r.ID, r.Channel, offset, n}, Distance: math.Sqrt(d / float64(len(target))), StartSeconds: float64(offset) / r.SampleRate})
		}
	}
	sort.Slice(candidates, func(i, j int) bool {
		a, b := candidates[i], candidates[j]
		if a.Distance != b.Distance {
			return a.Distance < b.Distance
		}
		if a.RecordingID != b.RecordingID {
			return a.RecordingID < b.RecordingID
		}
		return a.StartSample < b.StartSample
	})
	for _, h := range candidates {
		overlap := false
		for _, keep := range out.Hits {
			if h.RecordingID == keep.RecordingID && h.StartSample < keep.StartSample+n && keep.StartSample < h.StartSample+n {
				overlap = true
				break
			}
		}
		if !overlap {
			out.Hits = append(out.Hits, h)
			if len(out.Hits) == q.TopK {
				break
			}
		}
	}
	return out, nil
}
