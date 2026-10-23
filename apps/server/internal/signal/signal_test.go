package signal

import (
	"math"
	"testing"
	"time"
)

func capture(freq float64) Capture {
	c := Capture{Context: Context{"cnc", "x", "cut", "t1", "g", 6000}, Start: time.Unix(1, 0), SampleRate: 4096, Samples: make([]float64, 2048)}
	for i := range c.Samples {
		c.Samples[i] = math.Sin(2 * math.Pi * freq * float64(i) / c.SampleRate)
	}
	return c
}
func TestToneLocalizationAndAmplitude(t *testing.T) {
	c := capture(400)
	s, e := Extract(c)
	if e != nil {
		t.Fatal(e)
	}
	peak := 0
	for i, v := range s.Energy {
		if v > s.Energy[peak] {
			peak = i
		}
	}
	if math.Abs(math.Log(s.Frequencies[peak]/400)) > .2 {
		t.Fatalf("peak %g", s.Frequencies[peak])
	}
	for i := range c.Samples {
		c.Samples[i] *= 2
	}
	d, _ := Extract(c)
	for i := range s.Energy {
		if math.Abs(d.Energy[i]-4*s.Energy[i]) > 1e-9 {
			t.Fatal("energy must scale quadratically")
		}
	}
	if math.Abs(s.RMS-math.Sqrt(.5)) > 1e-9 {
		t.Fatal(s.RMS)
	}
}
func TestDCAndSilence(t *testing.T) {
	c := capture(400)
	a, _ := Extract(c)
	for i := range c.Samples {
		c.Samples[i] += 100
	}
	b, _ := Extract(c)
	if Distance(a, b) > 1e-10 {
		t.Fatal("DC offset changes fingerprint")
	}
	for i := range c.Samples {
		c.Samples[i] = 3
	}
	s, e := Extract(c)
	if e != nil {
		t.Fatal(e)
	}
	for _, v := range s.Features() {
		if v != 0 {
			t.Fatal("constant signal must be zero after centering")
		}
	}
}
func TestInvalid(t *testing.T) {
	for _, v := range []float64{math.NaN(), math.Inf(1), 1e10} {
		c := capture(100)
		c.Samples[0] = v
		if _, e := Extract(c); e == nil {
			t.Fatal("invalid sample accepted")
		}
	}
	c := capture(100)
	c.Samples = c.Samples[:511]
	if _, e := Extract(c); e == nil {
		t.Fatal("short signal accepted")
	}
}
func TestTokenCoverage(t *testing.T) {
	c := capture(400)
	s, _ := Extract(c)
	for b, f := range s.Frequencies {
		total, duration := 0.0, 0.0
		last := -1.0
		for _, v := range s.Tokens {
			if v.Frequency != f {
				continue
			}
			if v.Offset <= last || v.Offset+v.Duration > float64(len(c.Samples))/c.SampleRate {
				t.Fatal("invalid token extent")
			}
			last = v.Offset
			total += v.Energy * v.Duration
			duration += v.Duration
		}
		if math.Abs(total/duration-s.Energy[b]) > 1e-9 {
			t.Fatal("tokens and global energy disagree")
		}
	}
}
func BenchmarkExtract(b *testing.B) {
	c := capture(400)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		Extract(c)
	}
}
