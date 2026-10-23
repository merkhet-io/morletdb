package httpapi

import (
	"crypto/subtle"
	"embed"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"

	"github.com/merkhet-io/morletdb/internal/monitor"
	"github.com/merkhet-io/morletdb/internal/signal"
	"github.com/merkhet-io/morletdb/internal/store"
	"github.com/merkhet-io/morletdb/internal/waveform"
)

//go:embed dashboard.html
var assets embed.FS

type API struct {
	store *store.Store
	slots chan struct{}
}

func New(s *store.Store, token string) http.Handler {
	a := &API{store: s, slots: make(chan struct{}, 2)}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		write(w, 200, map[string]string{"status": "ok", "signature_version": signal.Version})
	})
	mux.HandleFunc("GET /", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		b, _ := assets.ReadFile("dashboard.html")
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(b)
	})
	mux.HandleFunc("POST /api/captures", a.ingest)
	mux.HandleFunc("GET /api/captures", a.list)
	mux.HandleFunc("GET /api/captures/{id}", a.get)
	mux.HandleFunc("GET /api/captures/{id}/similar", a.similar)
	mux.HandleFunc("POST /api/assess", a.assess)
	mux.HandleFunc("POST /api/recordings", a.recordingIngest)
	mux.HandleFunc("GET /api/recordings/{id}", a.recordingGet)
	mux.HandleFunc("POST /api/search", a.search)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
		if strings.HasPrefix(r.URL.Path, "/api/") && token != "" {
			got := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
			if subtle.ConstantTimeCompare([]byte(got), []byte(token)) != 1 {
				write(w, 401, map[string]string{"error": "bearer token required"})
				return
			}
		}
		mux.ServeHTTP(w, r)
	})
}
func write(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if e := json.NewEncoder(w).Encode(v); e != nil {
		log.Printf("response encode: %v", e)
	}
}
func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 2<<20)
	d := json.NewDecoder(r.Body)
	d.DisallowUnknownFields()
	if err := d.Decode(v); err != nil {
		write(w, 400, map[string]string{"error": err.Error()})
		return false
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		write(w, 400, map[string]string{"error": "expected one JSON object"})
		return false
	}
	return true
}
func (a *API) ingest(w http.ResponseWriter, r *http.Request) {
	select {
	case a.slots <- struct{}{}:
		defer func() { <-a.slots }()
	default:
		write(w, 429, map[string]string{"error": "ingestion busy; retry later"})
		return
	}
	var c signal.Capture
	if !decode(w, r, &c) {
		return
	}
	if err := c.Validate(); err != nil {
		write(w, 400, map[string]string{"error": err.Error()})
		return
	}
	record, err := a.store.Put(c)
	if err != nil {
		internal(w, err)
		return
	}
	write(w, 201, record)
}
func internal(w http.ResponseWriter, err error) {
	log.Print(err)
	write(w, 500, map[string]string{"error": "storage failure; see server logs"})
}
func (a *API) load(w http.ResponseWriter, id string) (store.Record, bool) {
	if !store.ValidID(id) {
		write(w, 400, map[string]string{"error": "invalid capture id"})
		return store.Record{}, false
	}
	v, err := a.store.Get(id)
	if errors.Is(err, os.ErrNotExist) {
		write(w, 404, map[string]string{"error": "capture not found"})
		return v, false
	}
	if err != nil {
		internal(w, err)
		return v, false
	}
	return v, true
}
func (a *API) get(w http.ResponseWriter, r *http.Request) {
	if v, ok := a.load(w, r.PathValue("id")); ok {
		write(w, 200, v)
	}
}
func (a *API) list(w http.ResponseWriter, r *http.Request) {
	records, err := a.store.List()
	if err != nil {
		internal(w, err)
		return
	}
	type Summary struct {
		ID      string         `json:"id"`
		Context signal.Context `json:"context"`
		Start   string         `json:"start"`
		Label   string         `json:"label"`
		RMS     float64        `json:"rms"`
	}
	out := []Summary{}
	for _, v := range records {
		if m := r.URL.Query().Get("machine"); m != "" && v.Capture.Context.Machine != m {
			continue
		}
		out = append(out, Summary{v.ID, v.Capture.Context, v.Capture.Start.Format("2006-01-02T15:04:05.999999999Z07:00"), v.Capture.Label, v.Signature.RMS})
	}
	write(w, 200, out)
}
func (a *API) similar(w http.ResponseWriter, r *http.Request) {
	q, ok := a.load(w, r.PathValue("id"))
	if !ok {
		return
	}
	k := 5
	if v := r.URL.Query().Get("k"); v != "" {
		n, e := strconv.Atoi(v)
		if e != nil || n < 1 || n > 100 {
			write(w, 400, map[string]string{"error": "k must be 1..100"})
			return
		}
		k = n
	}
	records, err := a.store.List()
	if err != nil {
		internal(w, err)
		return
	}
	write(w, 200, monitor.Nearest(q, records, k))
}
func (a *API) assess(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Target     string   `json:"target"`
		References []string `json:"references"`
	}
	if !decode(w, r, &req) {
		return
	}
	if len(req.References) < 5 || len(req.References) > 1000 {
		write(w, 400, map[string]string{"error": "supply 5..1000 references"})
		return
	}
	q, ok := a.load(w, req.Target)
	if !ok {
		return
	}
	refs := []store.Record{}
	for _, id := range req.References {
		v, ok := a.load(w, id)
		if !ok {
			return
		}
		refs = append(refs, v)
	}
	aresult, err := monitor.Assess(q, refs)
	if err != nil {
		write(w, 400, map[string]string{"error": err.Error()})
		return
	}
	write(w, 200, aresult)
}

func (a *API) recordingIngest(w http.ResponseWriter, r *http.Request) {
	var v waveform.Recording
	if !decode(w, r, &v) {
		return
	}
	if v.ID != "" {
		write(w, 400, map[string]string{"error": "recording ID is server assigned"})
		return
	}
	if e := v.Validate(); e != nil {
		write(w, 400, map[string]string{"error": e.Error()})
		return
	}
	v, e := a.store.PutRecording(v)
	if e != nil {
		internal(w, e)
		return
	}
	write(w, 201, v)
}
func (a *API) recordingGet(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !store.ValidID(id) {
		write(w, 400, map[string]string{"error": "invalid recording id"})
		return
	}
	v, e := a.store.GetRecording(id)
	if errors.Is(e, os.ErrNotExist) {
		write(w, 404, map[string]string{"error": "recording not found"})
		return
	}
	if e != nil {
		internal(w, e)
		return
	}
	write(w, 200, v)
}
func (a *API) search(w http.ResponseWriter, r *http.Request) {
	select {
	case a.slots <- struct{}{}:
		defer func() { <-a.slots }()
	default:
		write(w, 429, map[string]string{"error": "search busy; retry later"})
		return
	}
	var q waveform.Query
	if !decode(w, r, &q) {
		return
	}
	records, e := a.store.Recordings()
	if e != nil {
		internal(w, e)
		return
	}
	result, e := waveform.Search(r.Context(), records, q)
	if e != nil {
		write(w, 400, map[string]string{"error": e.Error()})
		return
	}
	write(w, 200, result)
}
