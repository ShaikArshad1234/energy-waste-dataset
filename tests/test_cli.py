import hashlib
import json
from pathlib import Path

from energygen.cli import main


def test_generate_validate_repro_roundtrip(tmp_path):
    out = tmp_path / "d"
    assert main(["generate", "--preset", "starter", "--out", str(out)]) == 0
    for rel in ["source/energy_reading.csv", "derived/interval_cost.csv", "ground_truth/ground_truth.csv",
                "ground_truth/ground_truth.json", "sql/seed.sql", "sql/schema.sql", "json/plant.json",
                "source_manifest.json", "reports/validation_summary.md", "generator_command.txt"]:
        assert (out / rel).exists(), rel
    man = json.loads((out / "source_manifest.json").read_text())
    assert man["provenance"]["external_data_used"] is False
    for f in man["files"][:10]:
        assert hashlib.sha256((out / f["path"]).read_bytes()).hexdigest() == f["sha256"]
    assert main(["validate", "--data", str(out)]) == 0            # re-derive + validate from the files on disk
    rep = tmp_path / "repro.json"
    assert main(["repro-check", "--preset", "starter", "--out", str(rep)]) == 0
    assert json.loads(rep.read_text())["identical"] is True


def test_cli_scale_overrides(tmp_path):
    out = tmp_path / "s"
    assert main(["generate", "--preset", "starter", "--meters", "9", "--days", "20", "--batches", "80", "--formats", "csv", "--out", str(out)]) == 0
    assert sum(1 for _ in open(out / "source" / "meter.csv")) - 1 == 9
    assert sum(1 for _ in open(out / "source" / "production_batch.csv")) - 1 == 80
    assert not (out / "sql").exists()


def test_dictionary_generation(tmp_path):
    p = tmp_path / "dd.md"
    assert main(["dictionary", "--out", str(p)]) == 0
    text = p.read_text()
    for t in ["plant", "meter", "machine", "energy_reading", "production_batch", "shift", "tariff",
              "weather_observation", "maintenance_event", "recommendation"]:
        assert f"### `{t}`" in text
