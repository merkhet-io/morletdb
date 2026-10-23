// Package store provides immutable, crash-safe capture files. It is an MVP
// row-oriented store, not yet the planned columnar wave-token storage engine.
package store

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/merkhet-io/morletdb/internal/signal"
)

type Record struct {
	ID        string           `json:"id"`
	Capture   signal.Capture   `json:"capture"`
	Signature signal.Signature `json:"signature"`
}
type Store struct{ dir string }

func Open(dir string) (*Store, error) {
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, err
	}
	return &Store{dir}, nil
}
func (s *Store) Put(c signal.Capture) (Record, error) {
	sig, err := signal.Extract(c)
	if err != nil {
		return Record{}, err
	}
	var id [16]byte
	if _, err = rand.Read(id[:]); err != nil {
		return Record{}, err
	}
	r := Record{hex.EncodeToString(id[:]), c, sig}
	data, err := json.Marshal(r)
	if err != nil {
		return Record{}, err
	}
	f, err := os.CreateTemp(s.dir, ".capture-*")
	if err != nil {
		return Record{}, err
	}
	defer os.Remove(f.Name())
	defer f.Close()
	if _, err = f.Write(data); err != nil {
		return Record{}, err
	}
	if err = f.Sync(); err != nil {
		return Record{}, err
	}
	if err = f.Close(); err != nil {
		return Record{}, err
	}
	if err = os.Rename(f.Name(), filepath.Join(s.dir, r.ID+".json")); err != nil {
		return Record{}, err
	}
	d, err := os.Open(s.dir)
	if err != nil {
		return Record{}, err
	}
	defer d.Close()
	if err = d.Sync(); err != nil {
		return Record{}, err
	}
	return r, nil
}
func ValidID(id string) bool {
	b, e := hex.DecodeString(id)
	return e == nil && len(b) == 16 && id == strings.ToLower(id)
}
func (s *Store) Get(id string) (Record, error) {
	if !ValidID(id) {
		return Record{}, errors.New("invalid capture id")
	}
	b, err := os.ReadFile(filepath.Join(s.dir, id+".json"))
	if err != nil {
		return Record{}, err
	}
	var r Record
	if err = json.Unmarshal(b, &r); err != nil {
		return Record{}, fmt.Errorf("corrupt capture %s: %w", id, err)
	}
	if r.ID != id || r.Signature.Version != signal.Version || len(r.Signature.Energy) != 12 {
		return Record{}, errors.New("invalid record or unsupported signature version")
	}
	if err = r.Capture.Validate(); err != nil {
		return Record{}, err
	}
	return r, nil
}
func (s *Store) List() ([]Record, error) {
	entries, err := os.ReadDir(s.dir)
	if err != nil {
		return nil, err
	}
	out := []Record{}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		r, err := s.Get(strings.TrimSuffix(e.Name(), ".json"))
		if err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Capture.Start.Equal(out[j].Capture.Start) {
			return out[i].ID < out[j].ID
		}
		return out[i].Capture.Start.Before(out[j].Capture.Start)
	})
	return out, nil
}
