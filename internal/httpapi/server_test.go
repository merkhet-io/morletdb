package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/merkhet-io/morletdb/internal/signal"
	"github.com/merkhet-io/morletdb/internal/store"
)

func TestAPI(t *testing.T) {
	s, _ := store.Open(t.TempDir())
	h := New(s, "secret")
	req := func(method, path string, body []byte, token string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, bytes.NewReader(body))
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		return w
	}
	if w := req("GET", "/api/captures", nil, ""); w.Code != 401 {
		t.Fatal(w.Code)
	}
	c := signal.Capture{Context: signal.Context{Machine: "m", Sensor: "s", Operation: "o", Tool: "t", Unit: "g", RPM: 1000}, Start: time.Unix(1, 0), SampleRate: 1024, Samples: make([]float64, 512)}
	b, _ := json.Marshal(c)
	w := req("POST", "/api/captures", b, "secret")
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	var record store.Record
	json.Unmarshal(w.Body.Bytes(), &record)
	for _, path := range []string{"/api/captures", "/api/captures/" + record.ID, "/api/captures/" + record.ID + "/similar", "/healthz", "/"} {
		if w := req("GET", path, nil, "secret"); w.Code != http.StatusOK {
			t.Fatal(path, w.Code)
		}
	}
	for _, body := range [][]byte{[]byte(`{"unexpected":1}`), append(b, []byte(` {}`)...), bytes.Repeat([]byte("x"), 3<<20)} {
		if w := req("POST", "/api/captures", body, "secret"); w.Code != 400 {
			t.Fatal(w.Code)
		}
	}
	if w := req("GET", "/api/captures/"+record.ID+"/similar?k=-1", nil, "secret"); w.Code != 400 {
		t.Fatal(w.Code)
	}
	if w := req("POST", "/api/assess", []byte(`{"target":"`+record.ID+`","references":[]}`), "secret"); w.Code != 400 {
		t.Fatal(w.Code)
	}
}
