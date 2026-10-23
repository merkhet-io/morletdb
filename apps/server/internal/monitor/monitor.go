package monitor

import (
	"errors"
	"fmt"
	"math"
	"sort"

	"github.com/merkhet-io/morletdb/internal/signal"
	"github.com/merkhet-io/morletdb/internal/store"
)

type Match struct {
	ID       string  `json:"id"`
	Label    string  `json:"label,omitempty"`
	Distance float64 `json:"distance"`
}

func Nearest(q store.Record, records []store.Record, k int) []Match {
	out := []Match{}
	for _, r := range records {
		if r.ID != q.ID && signal.Compatible(q.Capture, r.Capture) {
			out = append(out, Match{r.ID, r.Capture.Label, signal.Distance(q.Signature, r.Signature)})
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Distance == out[j].Distance {
			return out[i].ID < out[j].ID
		}
		return out[i].Distance < out[j].Distance
	})
	return out[:min(k, len(out))]
}

type Contribution struct {
	Feature string  `json:"feature"`
	Score   float64 `json:"score"`
}
type Assessment struct {
	Target        string         `json:"target"`
	References    []string       `json:"references"`
	Score         float64        `json:"score"`
	Threshold     float64        `json:"threshold"`
	Deviation     bool           `json:"deviation"`
	Contributions []Contribution `json:"contributions"`
}

// Assess computes max robust standardized deviation in log feature space.
// MAD floors prevent an identical baseline from yielding infinite scores.
// This is a heuristic deviation score, not a probability or fault diagnosis.
func Assess(q store.Record, refs []store.Record) (Assessment, error) {
	if len(refs) < 5 || len(refs) > 1000 {
		return Assessment{}, errors.New("supply 5..1000 distinct healthy reference captures")
	}
	seen := map[string]bool{}
	a := Assessment{Target: q.ID, Threshold: 6, References: []string{}, Contributions: []Contribution{}}
	for _, r := range refs {
		if r.ID == q.ID || seen[r.ID] {
			return a, errors.New("references must be distinct and exclude target")
		}
		seen[r.ID] = true
		if !signal.Compatible(q.Capture, r.Capture) {
			return a, errors.New("references must match machine, sensor, operation, tool, unit, RPM, sample rate and sample count")
		}
		a.References = append(a.References, r.ID)
	}
	names := []string{}
	for _, f := range q.Signature.Frequencies {
		names = append(names, fmtFrequency(f))
	}
	names = append(names, "rms", "crest_factor", "kurtosis")
	for i, v := range q.Signature.Features() {
		values := make([]float64, len(refs))
		for j, r := range refs {
			values[j] = r.Signature.Features()[i]
		}
		center := median(values)
		for j := range values {
			values[j] = math.Abs(values[j] - center)
		}
		scale := math.Max(1.4826*median(values), .05)
		z := math.Abs(v-center) / scale
		a.Score = math.Max(a.Score, z)
		a.Contributions = append(a.Contributions, Contribution{names[i], z})
	}
	sort.Slice(a.Contributions, func(i, j int) bool { return a.Contributions[i].Score > a.Contributions[j].Score })
	return withDeviation(a), nil
}
func withDeviation(a Assessment) Assessment { a.Deviation = a.Score > a.Threshold; return a }
func median(x []float64) float64 {
	sort.Float64s(x)
	n := len(x)
	if n%2 == 1 {
		return x[n/2]
	}
	return (x[n/2-1] + x[n/2]) / 2
}

func fmtFrequency(f float64) string { return fmt.Sprintf("band_%.2f_hz", f) }
