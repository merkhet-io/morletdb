"""Provenance for run and evaluation reports: which code, data and server produced them."""
import hashlib
import subprocess

from .dataset import REPO, dataset_dir


def git_revision():
    def git(*args):
        result = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    status = git("status", "--porcelain", "--untracked-files=no")
    return {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            # Uncommitted tracked changes make the commit an incomplete description.
            "dirty": None if status is None else bool(status)}


def collect(definition, manifest, data_root, conn=None):
    manifest_path = dataset_dir(definition, data_root) / "manifest.json"
    report = {"code": git_revision(), "dataset_id": definition["id"],
              "definition_sha256": manifest["definition_sha256"],
              "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              "manifest_created_utc": manifest["created_utc"],
              "representation": definition["representation"],
              "detector_version": definition["detector"]["version"],
              "association_version": definition["association"]["version"]}
    if conn is not None:
        server, extension, database = conn.execute(
            "SELECT version(), (SELECT extversion FROM pg_extension WHERE extname='pg_morlet'),"
            " current_database()").fetchone()
        versions = [r[0] for r in conn.execute(
            "SELECT DISTINCT checkpoint->>'extractor_version' FROM public.seismic_waveforms").fetchall()]
        report.update({"postgres": server, "pg_morlet_extversion": extension, "database": database,
                       "stored_extractor_versions": sorted(v for v in versions if v)})
    return report
