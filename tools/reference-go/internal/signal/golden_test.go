package signal

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"
)

func TestSharedGoldenFixtures(t *testing.T) {
	paths, e := filepath.Glob("../../../../testdata/morlet-energy-v1/*.json")
	if e != nil || len(paths) != 8 {
		t.Fatalf("expected eight fixtures: %v %d", e, len(paths))
	}
	for _, path := range paths {
		t.Run(filepath.Base(path), func(t *testing.T) {
			b, e := os.ReadFile(path)
			if e != nil {
				t.Fatal(e)
			}
			var f struct {
				Capture   Capture         `json:"capture"`
				Signature json.RawMessage `json:"signature"`
				Features  []float64       `json:"features"`
			}
			if e = json.Unmarshal(b, &f); e != nil {
				t.Fatal(e)
			}
			s, e := Extract(f.Capture)
			if e != nil {
				t.Fatal(e)
			}
			got, _ := json.Marshal(s)
			var actual, expected any
			json.Unmarshal(got, &actual)
			json.Unmarshal(f.Signature, &expected)
			compareGolden(t, actual, expected, "signature")
			for i, v := range s.Features() {
				if math.Abs(v-f.Features[i]) > 1e-10+1e-9*math.Abs(f.Features[i]) {
					t.Fatalf("feature %d differs", i)
				}
			}
		})
	}
}
func compareGolden(t *testing.T, actual, expected any, path string) {
	t.Helper()
	switch e := expected.(type) {
	case float64:
		a, ok := actual.(float64)
		if !ok || math.IsNaN(a) || math.Abs(a-e) > 1e-10+1e-9*math.Abs(e) {
			t.Fatalf("%s: got %v expected %g", path, actual, e)
		}
	case string:
		if actual != e {
			t.Fatalf("%s: got %v expected %s", path, actual, e)
		}
	case []any:
		a, ok := actual.([]any)
		if !ok || len(a) != len(e) {
			t.Fatalf("%s: array size", path)
		}
		for i := range e {
			compareGolden(t, a[i], e[i], path)
		}
	case map[string]any:
		a, ok := actual.(map[string]any)
		if !ok || len(a) != len(e) {
			t.Fatalf("%s: object keys", path)
		}
		for k, v := range e {
			compareGolden(t, a[k], v, path+"."+k)
		}
	default:
		t.Fatalf("unsupported fixture value %T", e)
	}
}
