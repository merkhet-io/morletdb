package httpapi

import (
	"bytes"
	"encoding/json"
	"github.com/merkhet-io/morletdb/internal/store"
	"github.com/merkhet-io/morletdb/internal/waveform"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestWaveformAPI(t *testing.T) {
	s, _ := store.Open(t.TempDir())
	h := New(s, "secret")
	call := func(path string, v any, auth bool) *httptest.ResponseRecorder {
		b, _ := json.Marshal(v)
		r := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(b))
		if auth {
			r.Header.Set("Authorization", "Bearer secret")
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		return w
	}
	r := waveform.Recording{Channel: "voltage", Unit: "V", SampleRate: 4096, Start: time.Unix(1, 0), Samples: make([]float64, 1024)}
	if w := call("/api/recordings", r, false); w.Code != 401 {
		t.Fatal(w.Code)
	}
	w := call("/api/recordings", r, true)
	if w.Code != 201 {
		t.Fatal(w.Body.String())
	}
	q := waveform.Query{Samples: make([]float64, 512), Channel: "voltage", Unit: "V", SampleRate: 4096, Normalization: "demean", TopK: 2}
	w = call("/api/search", q, true)
	if w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	var result waveform.Result
	if e := json.Unmarshal(w.Body.Bytes(), &result); e != nil || len(result.Hits) != 2 {
		t.Fatalf("%s %v", w.Body, e)
	}
	q.Extractor = "unknown"
	if w = call("/api/search", q, true); w.Code != 400 {
		t.Fatal(w.Code)
	}
}
