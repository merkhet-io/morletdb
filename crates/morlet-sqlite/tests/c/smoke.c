/* C smoke test of the release archive: cc smoke.c -I include -L lib -lmorlet_sqlite && ./a.out FILE */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "morlet_sqlite.h"

static int check(int32_t rc, const char *what) {
    if (rc != 0) {
        char msg[512];
        morlet_sqlite_last_error(msg, sizeof msg);
        fprintf(stderr, "%s failed (%d): %s\n", what, rc, msg);
        exit(1);
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2 || morlet_sqlite_abi_version() != MORLET_SQLITE_ABI) return 2;
    morlet_sqlite_store *s = NULL;
    check(morlet_sqlite_open(argv[1], MORLET_SQLITE_DURABILITY_FULL, &s), "open");
    check(morlet_sqlite_create_recording(s, "bench-1/force", 10000.0, "force", "N", 0.001, "{\"bench\":1}"), "create");
    enum { N = 100000 };
    double *x = malloc(N * sizeof *x), *y = malloc(N * sizeof *y), st[5];
    for (int i = 0; i < N; i++) x[i] = round(1000.0 * sin(i / 50.0)) / 1000.0;
    int64_t end = -1;
    check(morlet_sqlite_append(s, "bench-1/force", 0, x, N, 4096, &end), "append");
    check(morlet_sqlite_read(s, "bench-1/force", 0, N, y, N), "read");
    check(morlet_sqlite_stats(s, "bench-1/force", 0, N, st), "stats");
    if (end != N || memcmp(x, y, N * sizeof *x) != 0 || st[0] != N || st[1] != -1.0 || st[2] != 1.0) {
        fprintf(stderr, "wrong result\n");
        return 1;
    }
    if (morlet_sqlite_append(s, "bench-1/force", 5, x, 10, 4096, &end) != -1) return 1;   /* a gap is refused */
    morlet_sqlite_close(s);
    printf("C smoke: PASS (%d samples, mean %.6f, rms %.6f)\n", N, st[3], st[4]);
    free(x);
    free(y);
    return 0;
}
