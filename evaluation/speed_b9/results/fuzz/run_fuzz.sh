#!/bin/sh
# B9 gate 3: fuzz targets on a git archive of the candidate's grizzbit tree (src 0ad2a00, fuzz 5af0615), seeded as
# the nightly workflow seeds them (+ the earlier #394 corpora), nightly workflow limits, CPU time from /usr/bin/time -l.
B=/private/tmp/claude-502/-Users-zin-sophia-morletdb/b9124922-fe68-4e87-a54d-94a8303730c0/scratchpad/b9/fuzz
W=/Users/zin/sophia/morletdb/.claude/worktrees/agent-a1a6d776e16c86e41
S394=/private/tmp/claude-502/-Users-zin-sophia-morletdb/b9124922-fe68-4e87-a54d-94a8303730c0/scratchpad/s394/corpus
SECS=${SECS:-7600}
export PATH=$HOME/.cargo/bin:$PATH
rm -rf $B/src; mkdir -p $B/src; cd $W; git archive e54541a | tar xf - -C $B/src
cd $B/src/crates/grizzbit
for t in decode_sealed decode_v5 decode_v6 roundtrip; do
  C=$B/corpus-$t; rm -rf $C; mkdir -p $C
  for f in $B/src/testdata/rawcodec/*.chunk; do
    b=$(basename "$f"); n=$(( $(wc -c < "$f") - 4 ))
    case "$t" in
      decode_sealed) head -c $n "$f" > "$C/$b" ;;
      decode_v5) case "$b" in v5-*) head -c $n "$f" > "$C/$b" ;; esac ;;
      decode_v6) case "$b" in v6-*) head -c $n "$f" > "$C/$b" ;; esac ;;
    esac
  done
  [ -d $S394/$t ] && cp $S394/$t/* $C/ 2>/dev/null
  echo "$t seeds: $(ls $C | wc -l)"
done
cargo +nightly-2026-10-11 fuzz build -O -a > $B/build.log 2>&1 || { echo build failed; exit 1; }
for t in decode_sealed decode_v5 decode_v6 roundtrip; do
  ml=64; mx=65536; [ $t = roundtrip ] && ml=256 && mx=300000
  mkdir -p $B/artifacts-$t
  ( date -u +%FT%TZ > $B/$t.start
    /usr/bin/time -l cargo +nightly-2026-10-11 fuzz run -O -a $t $B/corpus-$t -- -max_total_time=$SECS -timeout=20 \
      -rss_limit_mb=2048 -malloc_limit_mb=$ml -max_len=$mx -artifact_prefix=$B/artifacts-$t/ > $B/$t.log 2> $B/$t.err
    echo "exit $?" > $B/$t.exit; date -u +%FT%TZ > $B/$t.end ) &
done
wait
echo ALLDONE > $B/done
