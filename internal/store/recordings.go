package store

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"github.com/merkhet-io/morletdb/internal/waveform"
	"os"
	"path/filepath"
)

// Recording storage is separate from the legacy CNC capture directory.
func (s *Store) PutRecording(r waveform.Recording) (waveform.Recording, error) {
	if r.ID != "" {
		return r, errors.New("recording ID is server assigned")
	}
	if e := r.Validate(); e != nil {
		return r, e
	}
	dir := filepath.Join(s.dir, "recordings")
	if e := os.MkdirAll(dir, 0700); e != nil {
		return r, e
	}
	var id [16]byte
	if _, e := rand.Read(id[:]); e != nil {
		return r, e
	}
	r.ID = hex.EncodeToString(id[:])
	b, e := json.Marshal(r)
	if e != nil {
		return r, e
	}
	f, e := os.CreateTemp(dir, ".recording-*")
	if e != nil {
		return r, e
	}
	defer os.Remove(f.Name())
	defer f.Close()
	if _, e = f.Write(b); e != nil {
		return r, e
	}
	if e = f.Sync(); e != nil {
		return r, e
	}
	if e = f.Close(); e != nil {
		return r, e
	}
	if e = os.Rename(f.Name(), filepath.Join(dir, r.ID+".json")); e != nil {
		return r, e
	}
	d, e := os.Open(dir)
	if e != nil {
		return r, e
	}
	defer d.Close()
	return r, d.Sync()
}
func (s *Store) GetRecording(id string) (waveform.Recording, error) {
	var r waveform.Recording
	if !ValidID(id) {
		return r, errors.New("invalid recording id")
	}
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
	return r, r.Validate()
}
func (s *Store) Recordings() ([]waveform.Recording, error) {
	out := []waveform.Recording{}
	entries, e := os.ReadDir(filepath.Join(s.dir, "recordings"))
	if errors.Is(e, os.ErrNotExist) {
		return out, nil
	}
	if e != nil {
		return nil, e
	}
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		id := entry.Name()[:len(entry.Name())-5]
		r, e := s.GetRecording(id)
		if e != nil {
			return nil, e
		}
		out = append(out, r)
	}
	return out, nil
}
