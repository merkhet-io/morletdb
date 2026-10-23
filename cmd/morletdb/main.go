package main

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"math/rand"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/merkhet-io/morletdb/internal/httpapi"
	"github.com/merkhet-io/morletdb/internal/monitor"
	sig "github.com/merkhet-io/morletdb/internal/signal"
	"github.com/merkhet-io/morletdb/internal/store"
	"github.com/merkhet-io/morletdb/internal/waveform"
)

func main() {
	if e := run(os.Args[1:]); e != nil {
		log.Print(e)
		os.Exit(1)
	}
}
func output(v any) error { e := json.NewEncoder(os.Stdout); e.SetIndent("", "  "); return e.Encode(v) }
func run(args []string) error {
	if len(args) == 0 {
		return errors.New("usage: morletdb <serve|demo|ingest|import-csv|list|record|recordings|search> [flags]")
	}
	f := flag.NewFlagSet(args[0], flag.ContinueOnError)
	dir := f.String("data", "./data", "capture directory")
	addr := f.String("addr", "127.0.0.1:8080", "HTTP listen address")
	file := f.String("file", "", "JSON capture or CSV file")
	metadata := f.String("metadata", "", "JSON capture metadata for CSV (without samples)")
	if e := f.Parse(args[1:]); e != nil {
		return e
	}
	if f.NArg() != 0 {
		return errors.New("unexpected positional arguments")
	}
	s, e := store.Open(*dir)
	if e != nil {
		return e
	}
	switch args[0] {
	case "serve":
		host, _, e := net.SplitHostPort(*addr)
		if e != nil {
			return e
		}
		token := os.Getenv("MORLETDB_TOKEN")
		ip := net.ParseIP(host)
		if token == "" && (ip == nil || !ip.IsLoopback()) {
			return errors.New("set MORLETDB_TOKEN when binding outside a loopback IP")
		}
		srv := &http.Server{Addr: *addr, Handler: httpapi.New(s, token), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 60 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 << 10}
		ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		go func() {
			<-ctx.Done()
			shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			srv.Shutdown(shutdown)
		}()
		log.Printf("MorletDB listening on http://%s", *addr)
		e = srv.ListenAndServe()
		if errors.Is(e, http.ErrServerClosed) {
			return nil
		}
		return e
	case "record":
		var r waveform.Recording
		if e := readJSON(*file, &r); e != nil {
			return e
		}
		v, e := s.PutRecording(r)
		if e != nil {
			return e
		}
		return output(v)
	case "recordings":
		v, e := s.RecordingSummaries()
		if e != nil {
			return e
		}
		return output(v)
	case "search":
		var q waveform.Query
		if e := readJSON(*file, &q); e != nil {
			return e
		}
		v, e := s.Search(context.Background(), q)
		if e != nil {
			return e
		}
		return output(v)
	case "demo":
		return demo(s)
	case "list":
		v, e := s.List()
		if e != nil {
			return e
		}
		return output(v)
	case "ingest":
		var c sig.Capture
		if e := readJSON(*file, &c); e != nil {
			return e
		}
		v, e := s.Put(c)
		if e != nil {
			return e
		}
		return output(v)
	case "import-csv":
		var c sig.Capture
		if e := readJSON(*metadata, &c); e != nil {
			return e
		}
		if len(c.Samples) != 0 {
			return errors.New("metadata must not contain samples")
		}
		fd, e := os.Open(*file)
		if e != nil {
			return e
		}
		defer fd.Close()
		r := csv.NewReader(io.LimitReader(fd, 2<<20))
		header, e := r.Read()
		if e != nil || len(header) != 1 || header[0] != "value" {
			return errors.New("CSV requires exactly one column with header value")
		}
		for {
			row, e := r.Read()
			if e == io.EOF {
				break
			}
			if e != nil {
				return e
			}
			v, e := strconv.ParseFloat(row[0], 64)
			if e != nil {
				return e
			}
			c.Samples = append(c.Samples, v)
			if len(c.Samples) > sig.MaxSamples {
				return errors.New("CSV exceeds capture sample limit")
			}
		}
		v, e := s.Put(c)
		if e != nil {
			return e
		}
		return output(v)
	default:
		return fmt.Errorf("unknown command %q", args[0])
	}
}
func readJSON(path string, v any) error {
	f, e := os.Open(path)
	if e != nil {
		return e
	}
	defer f.Close()
	d := json.NewDecoder(io.LimitReader(f, 2<<20))
	d.DisallowUnknownFields()
	if e = d.Decode(v); e != nil {
		return e
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return errors.New("expected one JSON object")
	}
	return nil
}
func demo(s *store.Store) error {
	refs := []store.Record{}
	rng := rand.New(rand.NewSource(42))
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	var heldout, anomaly store.Record
	for j := 0; j < 10; j++ {
		c := sig.Capture{Context: sig.Context{Machine: "cnc-demo-01", Sensor: "spindle-x", Operation: "finish-pass", Tool: "endmill-4flute", Unit: "g", RPM: 6000}, Start: start.Add(time.Duration(j) * time.Second), SampleRate: 4096, Samples: make([]float64, 2048), Label: fmt.Sprintf("healthy-%02d", j+1)}
		if j == 9 {
			c.Label = "synthetic-chatter"
		}
		phase := rng.Float64() * 2 * math.Pi
		for i := range c.Samples {
			t := float64(i) / c.SampleRate
			c.Samples[i] = .3*math.Sin(2*math.Pi*100*t+phase) + .15*math.Sin(2*math.Pi*400*t) + .015*rng.NormFloat64()
			if j == 9 {
				c.Samples[i] += .9 * math.Sin(2*math.Pi*950*t) * (1 + .3*math.Sin(2*math.Pi*25*t))
			}
		}
		r, e := s.Put(c)
		if e != nil {
			return e
		}
		if j < 8 {
			refs = append(refs, r)
		} else if j == 8 {
			heldout = r
		} else {
			anomaly = r
		}
	}
	normal, e := monitor.Assess(heldout, refs)
	if e != nil {
		return e
	}
	abnormal, e := monitor.Assess(anomaly, refs)
	if e != nil {
		return e
	}
	return output(map[string]any{"healthy": normal, "synthetic_chatter": abnormal, "message": "Synthetic demonstration only. Open the dashboard; select healthy references explicitly."})
}
