/* MorletDB embedded edition (#351, experimental): lossless waveform recordings in one SQLite file.
 * C interface, ABI 1 (crates/morlet-sqlite/src/ffi.rs). Every function except abi_version, last_error and close
 * returns 0 on success, -1 for invalid input or a refused operation, -2 for an SQLite error and -5 for an internal
 * error; morlet_sqlite_last_error() then gives this thread's message. Samples are float64; intervals are half-open
 * sample ranges [start, end). Apache-2.0. */
#ifndef MORLET_SQLITE_H
#define MORLET_SQLITE_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct morlet_sqlite_store morlet_sqlite_store;

#define MORLET_SQLITE_ABI 1
#define MORLET_SQLITE_DURABILITY_FULL 0   /* an append survives a power loss once it returns (default) */
#define MORLET_SQLITE_DURABILITY_NORMAL 1 /* may lose the newest appends on a power loss, never older ones */

uint32_t morlet_sqlite_abi_version(void);
size_t morlet_sqlite_last_error(char *buf, size_t cap);
int32_t morlet_sqlite_open(const char *path, int32_t durability, morlet_sqlite_store **out);
void morlet_sqlite_close(morlet_sqlite_store *store);
int32_t morlet_sqlite_create_recording(morlet_sqlite_store *store, const char *recording_id, double sample_rate_hz,
                                       const char *channel, const char *unit, double sample_quantum,
                                       const char *metadata_json);
/* Appends at the recording's end in chunks of chunk_size (1..32768); an identical replay is a no-op. */
int32_t morlet_sqlite_append(morlet_sqlite_store *store, const char *recording_id, int64_t start_sample,
                             const double *samples, size_t n, uint32_t chunk_size, int64_t *out_end);
/* As append, with the chunk lengths given (1..32768 each, adding up to n). */
int32_t morlet_sqlite_append_chunks(morlet_sqlite_store *store, const char *recording_id, int64_t start_sample,
                                    const double *samples, size_t n, const uint32_t *lengths, size_t nlen,
                                    int64_t *out_end);
/* out must hold exactly end - start values. */
int32_t morlet_sqlite_read(morlet_sqlite_store *store, const char *recording_id, int64_t start, int64_t end,
                           double *out, size_t cap);
/* out[5] = count, min, max, mean, rms. */
int32_t morlet_sqlite_stats(morlet_sqlite_store *store, const char *recording_id, int64_t start, int64_t end,
                            double *out);
/* The recordings as JSON; *out_len gets the length. If *out_len >= cap, call again with a larger buffer. */
int32_t morlet_sqlite_info_json(morlet_sqlite_store *store, char *buf, size_t cap, size_t *out_len);

#ifdef __cplusplus
}
#endif
#endif
