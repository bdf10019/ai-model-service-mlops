"""Fixed local worker. No model calls, external tracker, credentials or release writes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import mlflow
from mlflow import MlflowClient


def main():
    root = Path(__file__).resolve().parents[1]
    state = Path(sys.argv[1]).resolve()
    state.relative_to(root)
    state.mkdir(parents=True, exist_ok=True)
    artifacts = state / "artifacts"
    artifacts.mkdir(exist_ok=True)
    uri = "sqlite:///" + (state / "tracking.db").as_posix()
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    data = json.loads(sys.stdin.buffer.read(524289))
    name = "console-local-" + data["namespace"]
    experiment = client.get_experiment_by_name(name)
    experiment_id = (
        experiment.experiment_id
        if experiment
        else client.create_experiment(name, artifact_location=artifacts.as_uri())
    )
    source = data["source_sha256"]
    assert len(source) == 64 and all(c in "abcdef0123456789" for c in source)
    existing = client.search_runs(
        [experiment_id], filter_string=f"tags.console_source_sha256 = '{source}'", max_results=2
    )
    if len(existing) > 1:
        raise RuntimeError("duplicate source records")
    run_id = (
        existing[0].info.run_id
        if existing
        else client.create_run(
            experiment_id,
            tags={
                "console_source_sha256": source,
                "console_source_id": data["source_id"],
                "console_evidence_kind": "historical_import"
                if data["historical_import"]
                else data["evidence_kind"],
                "console_release_authority": "none",
                "mlflow.runName": data["namespace"] + "-" + data["source_id"][:8],
            },
        ).info.run_id
    )
    current = client.get_run(run_id)
    if current.data.tags.get("console_sync_complete") != "true":
        for k, v in {
            **data["parameters"],
            **{
                k: data[k]
                for k in ("config_version", "config_sha256", "model_sha256", "provider_id")
            },
        }.items():
            client.log_param(run_id, k, str(v))
        for k, v in data["metrics"].items():
            if isinstance(v, (float, int)):
                client.log_metric(run_id, k, v)
        temporary = state / (source + ".json")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        client.log_artifact(run_id, str(temporary), artifact_path="report-references")
        temporary.unlink()
        client.set_tag(run_id, "console_sync_complete", "true")
        client.set_terminated(run_id)
    actual = client.get_run(run_id)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "experiment_id": experiment_id,
                "mlflow_version": mlflow.__version__,
                "parameters": actual.data.params,
                "metrics": actual.data.metrics,
                "tags": actual.data.tags,
                "artifacts": [a.path for a in client.list_artifacts(run_id, "report-references")],
                "tracking_backend": "local_sqlite_no_server",
                "release_authority": "console_only",
            }
        )
    )


if __name__ == "__main__":
    main()
