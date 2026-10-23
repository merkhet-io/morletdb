package store

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"

	"github.com/merkhet-io/morletdb/internal/waveform"
)

const recordingVersion = "recording-f64le-v1"

type manifest struct {
	Version   string             `json:"storage_version"`
	SHA256    string             `json:"samples_sha256"`
	Recording waveform.Recording `json:"recording"`
}

// atomicFile publishes a synced file; concurrent rebuilds produce equivalent immutable content.
func atomicFile(dir, name string, b []byte) error {
	if e := os.MkdirAll(dir, 0700); e != nil {
		return e
	}
	f, e := os.CreateTemp(dir, ".pending-*")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	defer f.Close()
	if _, e = f.Write(b); e != nil {
		return e
	}
	if e = f.Sync(); e != nil {
		return e
	}
	if e = f.Close(); e != nil {
		return e
	}
	if e = os.Rename(f.Name(), filepath.Join(dir, name)); e != nil {
		return e
	}
	d, e := os.Open(dir)
	if e != nil {
		return e
	}
	defer d.Close()
	return d.Sync()
}
func (s *Store) PutRecording(r waveform.Recording) (waveform.Recording, error) {
	if r.ID != "" {
		return r, errors.New("recording ID is server assigned")
	}
	if e := r.Validate(); e != nil {
		return r, e
	}
	var id [16]byte
	if _, e := rand.Read(id[:]); e != nil {
		return r, e
	}
	r.ID = hex.EncodeToString(id[:])
	r.SampleCount = len(r.Samples)
	raw := make([]byte, 8*len(r.Samples))
	for i, v := range r.Samples {
		binary.LittleEndian.PutUint64(raw[i*8:], math.Float64bits(v))
	}
	sum := sha256.Sum256(raw)
	meta := r
	meta.Samples = nil
	b, e := json.Marshal(manifest{recordingVersion, hex.EncodeToString(sum[:]), meta})
	if e != nil {
		return r, e
	}
	dir := filepath.Join(s.dir, "recordings")
	// Manifest is the commit marker. An interrupted write can leave an ignored orphan sample file.
	if e = atomicFile(dir, r.ID+".f64", raw); e != nil {
		return r, e
	}
	return r, atomicFile(dir, r.ID+".meta", b)
}
func (s *Store) readManifest(id string) (manifest, error) {
	var m manifest
	if !ValidID(id) {
		return m, errors.New("invalid recording id")
	}
	b, e := os.ReadFile(filepath.Join(s.dir, "recordings", id+".meta"))
	if e != nil {
		return m, e
	}
	if e = json.Unmarshal(b, &m); e != nil {
		return m, e
	}
	r := m.Recording
	if m.Version != recordingVersion || r.ID != id || r.SampleCount < 512 || r.SampleCount > 32768 || len(m.SHA256) != 64 || len(r.Samples) != 0 {
		return m, errors.New("invalid recording manifest")
	}
	// Reuse domain validation without loading raw samples.
	r.Samples = make([]float64, r.SampleCount)
	return m, r.Validate()
}
func (s *Store) GetRecording(id string) (waveform.Recording, error) {
	var r waveform.Recording
	if !ValidID(id) {
		return r, errors.New("invalid recording id")
	}
	m, e := s.readManifest(id)
	if errors.Is(e, os.ErrNotExist) {
		b, e := os.ReadFile(filepath.Join(s.dir, "recordings", id+".json"))
		if e != nil {
			return r, e
		}
		if e = json.Unmarshal(b, &r); e != nil {
			return r, e
		}
		if r.ID != id {
			return r, errors.New("recording ID mismatch")
		}
		r.SampleCount = len(r.Samples)
		return r, r.Validate()
	}
	if e != nil {
		return r, e
	}
	r = m.Recording
	b, e := os.ReadFile(filepath.Join(s.dir, "recordings", id+".f64"))
	if e != nil {
		return r, e
	}
	sum := sha256.Sum256(b)
	if len(b) != 8*r.SampleCount || hex.EncodeToString(sum[:]) != m.SHA256 {
		return r, errors.New("recording sample checksum/length mismatch")
	}
	r.Samples = make([]float64, r.SampleCount)
	for i := range r.Samples {
		r.Samples[i] = math.Float64frombits(binary.LittleEndian.Uint64(b[i*8:]))
	}
	return r, r.Validate()
}

// RecordingSummaries never reads binary samples. Legacy JSON remains readable.
func (s *Store) RecordingSummaries() ([]waveform.Recording, error) {
	out := []waveform.Recording{}
	dir := filepath.Join(s.dir, "recordings")
	entries, e := os.ReadDir(dir)
	if errors.Is(e, os.ErrNotExist) {
		return out, nil
	}
	if e != nil {
		return nil, e
	}
	seen := map[string]bool{}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".meta") {
			continue
		}
		id := strings.TrimSuffix(entry.Name(), ".meta")
		m, e := s.readManifest(id)
		if e != nil {
			return nil, e
		}
		out = append(out, m.Recording)
		seen[id] = true
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		id := strings.TrimSuffix(entry.Name(), ".json")
		if seen[id] {
			continue
		}
		r, e := s.GetRecording(id)
		if e != nil {
			return nil, e
		}
		r.Samples = nil
		out = append(out, r)
	}
	return out, nil
}
func (s *Store) Recordings() ([]waveform.Recording, error) {
	list, e := s.RecordingSummaries()
	if e != nil {
		return nil, e
	}
	for i, r := range list {
		list[i], e = s.GetRecording(r.ID)
		if e != nil {
			return nil, e
		}
	}
	return list, nil
}

type featureFile struct {
	Version  string      `json:"version"`
	Source   string      `json:"source"`
	Values   [][]float64 `json:"values"`
	Checksum string      `json:"checksum"`
}

// Search uses persisted per-recording feature grids. Missing grids rebuild atomically.
func (s *Store) Search(ctx context.Context, q waveform.Query) (waveform.Result, error) {
	records, e := s.RecordingSummaries()
	if e != nil {
		return waveform.Result{}, e
	}
	if q.RecordingID != "" {
		for i, r := range records {
			if r.ID == q.RecordingID {
				records[i], e = s.GetRecording(r.ID)
				if e != nil {
					return waveform.Result{}, e
				}
			}
		}
	}
	grids := map[string][][]float64{}
	provider := func(r waveform.Recording, offset, n int, mode string) ([]float64, error) {
		stride := q.Stride
		if stride == 0 {
			stride = n / 2
		}
		key := fmt.Sprintf("%s-%s-%d-%d-%s", r.ID, waveform.Version, n, stride, mode)
		if v, ok := grids[key]; ok {
			return v[offset/stride], nil
		}
		if e := ctx.Err(); e != nil {
			return nil, e
		}
		dir := filepath.Join(s.dir, "indexes")
		path := filepath.Join(dir, key+".json")
		source := "legacy-json"
		m, me := s.readManifest(r.ID)
		if me == nil {
			source = m.SHA256
		} else if !errors.Is(me, os.ErrNotExist) {
			return nil, me
		}
		var index featureFile
		b, e := os.ReadFile(path)
		if e == nil {
			if e = json.Unmarshal(b, &index); e != nil {
				return nil, e
			}
			payload, _ := json.Marshal(index.Values)
			sum := sha256.Sum256(payload)
			if index.Version != waveform.Version || index.Source != source || index.Checksum != hex.EncodeToString(sum[:]) || len(index.Values) != (r.SampleCount-n)/stride+1 {
				return nil, errors.New("invalid feature index; remove index file to rebuild")
			}
			for _, v := range index.Values {
				if len(v) != 15 {
					return nil, errors.New("invalid feature dimension")
				}
				for _, x := range v {
					if math.IsNaN(x) || math.IsInf(x, 0) {
						return nil, errors.New("non-finite feature")
					}
				}
			}
		} else if errors.Is(e, os.ErrNotExist) {
			full, e := s.GetRecording(r.ID)
			if e != nil {
				return nil, e
			}
			index = featureFile{Version: waveform.Version, Source: source, Values: [][]float64{}}
			if (len(full.Samples)-n)/stride+1 > 4096 {
				return nil, errors.New("feature grid exceeds 4096 windows")
			}
			for start := 0; start+n <= len(full.Samples); start += stride {
				if e := ctx.Err(); e != nil {
					return nil, e
				}
				v, e := waveform.Features(full.Samples[start:start+n], r.SampleRate, mode)
				if e != nil {
					return nil, e
				}
				index.Values = append(index.Values, v)
			}
			payload, _ := json.Marshal(index.Values)
			sum := sha256.Sum256(payload)
			index.Checksum = hex.EncodeToString(sum[:])
			b, e = json.Marshal(index)
			if e != nil {
				return nil, e
			}
			if e = atomicFile(dir, key+".json", b); e != nil {
				return nil, e
			}
		} else {
			return nil, e
		}
		grids[key] = index.Values
		return index.Values[offset/stride], nil
	}
	return waveform.SearchIndexed(ctx, records, q, provider)
}
