#!/bin/sh
# Fetches the Elf test series (Spatio-Temporal-Lab/elf at a pinned commit) into data/b4-elf and checks SHA-256s.
# The repository states no license, so the files are used locally and not redistributed.
set -e
COMMIT=64e0d6004be322d9d8eaf9931e371df202f1a0eb
D=data/b4-elf
mkdir -p "$D"
cut -c67- evaluation/storage_b4/elf-sha256.txt | while read -r f; do
  curl -sSfL -o "$D/$f" "https://raw.githubusercontent.com/Spatio-Temporal-Lab/elf/$COMMIT/src/test/resources/ElfTestData/$f"
done
(cd "$D" && shasum -a 256 -c ../../evaluation/storage_b4/elf-sha256.txt)
