set -u
cd /Users/zin/sophia/morletdb/research/multiscale_memory
py() { ../../.venv-research/bin/python -I experiments/ptbxl_pilot2.py "$@"; }
O=/Users/zin/sophia/morletdb/data/msmem-runs/pilot2-73d852e
py link --out $O --source ../../data/msmem-runs/pilot-590445f > $O/stages.log 2>&1 || { echo STAGE_FAILED >> $O/phase1.log; exit 1; }
py encoders --out $O >> $O/stages.log 2>&1 || { echo STAGE_FAILED >> $O/phase1.log; exit 1; }
py retrieve --out $O >> $O/stages.log 2>&1 || { echo STAGE_FAILED >> $O/phase1.log; exit 1; }
for s in 0 1 2; do for a in A0 P L1 L3 L3n; do
  t=$(date +%s); if py train --out $O --arm $a --seed $s >> $O/train.log 2>&1; then echo "done $a seed$s $(($(date +%s)-t))s" >> $O/phase1.log; else echo "FAILED $a seed$s" >> $O/phase1.log; fi
done; done
echo PHASE1_COMPLETE >> $O/phase1.log
py gate --out $O > $O/gate.log 2>&1
if grep -q '"phase2": true' $O/gate.json; then
  for s in 0 1 2; do for a in R64n W64n C64n R64 W64 C64; do
    t=$(date +%s); if py train --out $O --arm $a --seed $s >> $O/train.log 2>&1; then echo "done $a seed$s $(($(date +%s)-t))s" >> $O/phase2.log; else echo "FAILED $a seed$s" >> $O/phase2.log; fi
  done; done
  echo PHASE2_COMPLETE >> $O/phase2.log
else echo "GATE_STOP" >> $O/phase2.log; fi
echo ALL_DONE >> $O/phase2.log
