// Command golden regenerates the reviewed cross-language extractor fixtures.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"github.com/merkhet-io/morletdb/internal/signal"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"time"
)

func main() {
	out := flag.String("out", "../../testdata/morlet-energy-v1", "fixture directory")
	flag.Parse()
	if err := generate(*out); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func generate(dir string) error {
	if e := os.MkdirAll(dir, 0755); e != nil {
		return e
	}
	cases := []struct {
		name string
		n    int
		rate float64
	}{
		{"silence", 512, 4096}, {"constant", 512, 4096}, {"tone", 512, 4096}, {"mixed-noise", 2048, 4096},
		{"transient-odd-length", 777, 4096}, {"edge-impulses", 512, 128}, {"chirp", 1024, 200000}, {"scaled-dc", 512, 4096},
	}
	for _, tc := range cases {
		c := signal.Capture{Context: signal.Context{Machine: "golden", Sensor: "x", Operation: "fixture", Tool: "none", Unit: "arb", RPM: 1}, Start: time.Unix(1, 0).UTC(), SampleRate: tc.rate, Samples: make([]float64, tc.n)}
		rng := rand.New(rand.NewSource(73))
		for i := range c.Samples {
			t := float64(i) / tc.rate
			switch tc.name {
			case "constant":
				c.Samples[i] = 3
			case "tone":
				c.Samples[i] = math.Sin(2 * math.Pi * 400 * t)
			case "mixed-noise":
				c.Samples[i] = .3*math.Sin(2*math.Pi*100*t+.7) + .15*math.Sin(2*math.Pi*950*t) + .01*rng.NormFloat64()
			case "transient-odd-length":
				if i >= 256 && i < 512 {
					c.Samples[i] = math.Sin(2 * math.Pi * 700 * t)
				}
			case "edge-impulses":
				if i == 0 || i == tc.n-1 {
					c.Samples[i] = 1
				}
				if i == tc.n/2 {
					c.Samples[i] = -2
				}
			case "chirp":
				u := float64(i) / float64(tc.n)
				c.Samples[i] = math.Sin(2 * math.Pi * (10*u + 100*u*u))
			case "scaled-dc":
				c.Samples[i] = 100 + 2*math.Sin(2*math.Pi*400*t)
			}
		}
		s, e := signal.Extract(c)
		if e != nil {
			return e
		}
		b, e := json.MarshalIndent(struct {
			Name      string           `json:"name"`
			Capture   signal.Capture   `json:"capture"`
			Signature signal.Signature `json:"signature"`
			Features  []float64        `json:"features"`
		}{tc.name, c, s, s.Features()}, "", "  ")
		if e != nil {
			return e
		}
		if e = os.WriteFile(filepath.Join(dir, tc.name+".json"), append(b, '\n'), 0644); e != nil {
			return e
		}
	}
	return nil
}
