// Package signal extracts versioned, deterministic Morlet wavelet fingerprints.
package signal

import (
	"errors"
	"fmt"
	"math"
	"time"
)

const Version = "morlet-energy-v1"
const MaxSamples = 32768

type Context struct {
	Machine   string  `json:"machine"`
	Sensor    string  `json:"sensor"`
	Operation string  `json:"operation"`
	Tool      string  `json:"tool"`
	Unit      string  `json:"unit"`
	RPM       float64 `json:"rpm"`
}
type Capture struct {
	Context    Context   `json:"context"`
	Start      time.Time `json:"start"`
	SampleRate float64   `json:"sample_rate_hz"`
	Samples    []float64 `json:"samples"`
	Label      string    `json:"label,omitempty"`
}
type Token struct {
	Offset    float64 `json:"offset_seconds"`
	Duration  float64 `json:"duration_seconds"`
	Frequency float64 `json:"frequency_hz"`
	Energy    float64 `json:"energy"`
}
type Signature struct {
	Version     string    `json:"version"`
	Frequencies []float64 `json:"frequencies_hz"`
	Energy      []float64 `json:"energy"`
	RMS         float64   `json:"rms"`
	Peak        float64   `json:"peak"`
	Crest       float64   `json:"crest_factor"`
	Kurtosis    float64   `json:"kurtosis"`
	Tokens      []Token   `json:"tokens"`
}

func (c Capture) Validate() error {
	if c.Start.IsZero() {
		return errors.New("start timestamp required")
	}
	for _, s := range []string{c.Context.Machine, c.Context.Sensor, c.Context.Operation, c.Context.Tool, c.Context.Unit} {
		if len(s) == 0 || len(s) > 128 {
			return errors.New("machine, sensor, operation, tool and unit must contain 1..128 bytes")
		}
	}
	if len(c.Label) > 128 {
		return errors.New("label too long")
	}
	if !finite(c.Context.RPM) || c.Context.RPM <= 0 || c.Context.RPM > 100000 {
		return errors.New("rpm must be in (0,100000]")
	}
	if !finite(c.SampleRate) || c.SampleRate < 128 || c.SampleRate > 200000 {
		return errors.New("sample_rate_hz must be in [128,200000]")
	}
	if len(c.Samples) < 512 || len(c.Samples) > MaxSamples {
		return fmt.Errorf("capture requires 512..%d uniformly sampled values", MaxSamples)
	}
	for _, v := range c.Samples {
		if !finite(v) || math.Abs(v) > 1e9 {
			return errors.New("samples must be finite and within +/-1e9")
		}
	}
	return nil
}
func finite(x float64) bool { return !math.IsNaN(x) && !math.IsInf(x, 0) }

// Extract uses 12 logarithmic bands, omega0=6 and +/-3 sigma support.
// Complex kernels are corrected to zero DC and normalized to unit L2 energy.
// Boundary coefficients are discarded; token energies are mean |coefficient|^2.
func Extract(c Capture) (Signature, error) {
	if err := c.Validate(); err != nil {
		return Signature{}, err
	}
	s := Signature{Version: Version, Frequencies: make([]float64, 12), Energy: make([]float64, 12), Tokens: []Token{}}
	n := len(c.Samples)
	mean := 0.0
	for _, v := range c.Samples {
		mean += v / float64(n)
	}
	x := make([]float64, n)
	sum2, sum4 := 0.0, 0.0
	for i, v := range c.Samples {
		x[i] = v - mean
		a := math.Abs(x[i])
		s.Peak = math.Max(s.Peak, a)
		sum2 += x[i] * x[i]
		sum4 += math.Pow(x[i], 4)
	}
	s.RMS = math.Sqrt(sum2 / float64(n))
	if s.RMS > 0 {
		s.Crest = s.Peak / s.RMS
		s.Kurtosis = (sum4 / float64(n)) / math.Pow(s.RMS, 4)
	}
	for b := range s.Energy {
		f := c.SampleRate / 64 * math.Pow((c.SampleRate*.4)/(c.SampleRate/64), float64(b)/11)
		s.Frequencies[b] = f
		scale := 6 * c.SampleRate / (2 * math.Pi * f)
		radius := int(math.Ceil(3 * scale))
		size := 2*radius + 1
		re, im := make([]float64, size), make([]float64, size)
		mr, mi := 0.0, 0.0
		for k := -radius; k <= radius; k++ {
			t := float64(k) / scale
			g := math.Exp(-t * t / 2)
			re[k+radius] = g * math.Cos(6*t)
			im[k+radius] = g * math.Sin(6*t)
			mr += re[k+radius]
			mi += im[k+radius]
		}
		norm := 0.0
		for k := range re {
			re[k] -= mr / float64(size)
			im[k] -= mi / float64(size)
			norm += re[k]*re[k] + im[k]*im[k]
		}
		norm = math.Sqrt(norm)
		for k := range re {
			re[k] /= norm
			im[k] /= norm
		}
		total, count := 0.0, 0
		// Non-overlapping 128-coefficient blocks; all valid coefficients are retained in summaries.
		for start := radius; start < n-radius; start += 128 {
			end := min(start+128, n-radius)
			energy := 0.0
			for i := start; i < end; i++ {
				r, j := 0.0, 0.0
				for k := range re {
					r += x[i+k-radius] * re[k]
					j += x[i+k-radius] * im[k]
				}
				energy += r*r + j*j
			}
			total += energy
			count += end - start
			s.Tokens = append(s.Tokens, Token{Offset: float64(start) / c.SampleRate, Duration: float64(end-start) / c.SampleRate, Frequency: f, Energy: energy / float64(end-start)})
		}
		s.Energy[b] = total / float64(count)
	}
	return s, nil
}

// Features retain amplitude as well as spectral shape. Units must match.
func (s Signature) Features() []float64 {
	f := make([]float64, 0, len(s.Energy)+3)
	for _, e := range s.Energy {
		f = append(f, math.Log1p(e))
	}
	return append(f, math.Log1p(s.RMS), math.Log1p(s.Crest), math.Log1p(s.Kurtosis))
}
func Compatible(a, b Capture) bool {
	return a.Context == b.Context && a.SampleRate == b.SampleRate && len(a.Samples) == len(b.Samples)
}
func Distance(a, b Signature) float64 {
	x, y := a.Features(), b.Features()
	sum := 0.0
	for i := range x {
		d := x[i] - y[i]
		sum += d * d
	}
	return math.Sqrt(sum / float64(len(x)))
}
