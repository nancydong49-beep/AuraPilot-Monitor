from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from monitor_dashboard.monitor import ProjectMonitor


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def create_run(root: Path, project: str = "demo_ifld", run_id: str = "run_001") -> Path:
    run = root / project / "runs" / run_id
    run.mkdir(parents=True)
    write_json(
        run / "project_input.json",
        {"profile": "inverse_folding_and_library_design/v1.0.0"},
    )
    write_json(run / "resolved_config.json", {"task": "target_wt"})
    write_json(
        run / "profile.lock.json",
        {"profile": "inverse_folding_and_library_design/v1.0.0"},
    )
    (run / "command.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return run


def create_partial_project(
    root: Path,
    project: str = "demo_partial_denovo",
) -> Path:
    path = root / project
    write_json(
        path / "project_config.json",
        {
            "project_id": project,
            "workflow": "denovo_design.boltzgen.partial_denovo_library_design",
            "antigen": {"name": "CD98"},
            "framework": {"name": "demo_VHH"},
            "branches": [{"name": "target_1", "execution_enabled": True}],
            "partial_scaffold": {"count": 5000},
            "generation": {"node_count": 2, "num_designs_total": 120000},
            "library_design": {"library_size": 12000},
        },
    )
    (path / "input").mkdir(parents=True)
    (path / "input" / "denovo_info.csv").write_text("name\nCD98\n", encoding="utf-8")
    write_json(path / "target_prep" / "structure_prep_status.json", {"status": "complete"})
    scaffold_manifest = path / "boltzgen" / "fab_scaffolds" / "demo_vhh" / "manifest.csv"
    scaffold_manifest.parent.mkdir(parents=True)
    scaffold_manifest.write_text("id\nscaffold_1\n", encoding="utf-8")
    return path


def create_run_scoped_partial_project(
    root: Path,
    project: str = "demo_huoshan_partial",
    run_id: str = "20260725_001_partial",
) -> Path:
    run = root / project / "runs" / run_id
    run.mkdir(parents=True)
    (run / "required_config.yaml").write_text(
        "\n".join(
            (
                f"project_id: {project}",
                f"run_id: {run_id}",
                "workflow: denovo_design.boltzgen",
                "server_profile: huoshan/v1.0.0",
                "target:",
                "  target: CD98",
                "scaffold:",
                "  framework:",
                "    name: CD98_VHH",
                "  partial_scaffold:",
                "    count: 5000",
                "remote_launch:",
                "  num_designs: 24000",
                "  node_count: 2",
                "  num_designs_total: 48000",
                "partial_denovo_library_design:",
                "  enabled: false",
                "  library_size: 12000",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    structure_root = run / "steps" / "02_structure_prediction"
    write_json(structure_root / "antigen_structure_manifest.json", {"status": "complete"})
    write_json(structure_root / "vhh_structure_manifest.json", {"status": "complete"})
    write_json(
        structure_root / "structure_qc_manifest.json",
        {"status": "passed", "partial_scaffolds": {"yaml_count": 5000}},
    )
    write_json(
        structure_root / "target_prep_manifest.json",
        {"status": "complete", "scaffold_yaml_count": 5000},
    )
    for node in ("huoshan_A800_01", "huoshan_A800_02"):
        final = (
            run
            / "outputs"
            / "boltzgen"
            / "workbench"
            / f"CD98_{node}"
            / "final_ranked_designs"
        )
        final.mkdir(parents=True)
        (final / "all_designs_metrics.csv").write_text("id\n1\n2\n", encoding="utf-8")
        (final / "final_designs_metrics_500.csv").write_text("id\n1\n", encoding="utf-8")
    aggregation = run / "steps" / "04_boltzgen_aggregation"
    write_json(aggregation / "boltzgen_aggregation_manifest.json", {"status": "complete"})
    (aggregation / "all_designs_2500.csv").write_text("id\n1\n", encoding="utf-8")
    failed_log = (
        run
        / "steps"
        / "05_developability_af3"
        / "nodes"
        / "huoshan_A800_02"
        / "node.log"
    )
    failed_log.parent.mkdir(parents=True)
    failed_log.write_text(
        "Traceback (most recent call last):\nRuntimeError: AF3 shard failures\n",
        encoding="utf-8",
    )
    return run


class MonitorDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_complete_ifld_run(self) -> None:
        run = create_run(self.root)
        output = run / "outputs" / "output"
        write_json(
            output / "target_wt_workflow_status.json",
            {
                "workflow": "inverse_folding_and_library_design",
                "task": "target_wt",
                "updated_at": "2026-07-28T12:00:00+00:00",
                "stages": {
                    "structure_prediction": {"status": "complete"},
                    "structure_clustering": {"status": "complete"},
                    "mutation_library": {"status": "complete"},
                    "candidate_filter": {"status": "complete"},
                    "library_design": {"status": "complete"},
                },
            },
        )
        selected = output / "selected_structures" / "selected_000.cif"
        selected.parent.mkdir(parents=True)
        selected.write_text("data_test\n", encoding="utf-8")
        write_json(
            output / "candidate_filter" / "target_wt_candidate_filter_manifest.json",
            {
                "status": "complete",
                "n_candidates": 100,
                "n_selected": 20,
                "rmsd_completed": 100,
                "rmsd_failed": 0,
                "rmsd_pending": 0,
            },
        )
        write_json(
            output / "library_design" / "target_wt_library_design_manifest.json",
            {"status": "complete"},
        )
        write_json(
            output / "library_design" / "target_wt_expression_selection_status.json",
            {"complete": True, "selected": 10, "target": 10, "evaluated": 30},
        )

        status = ProjectMonitor(self.root).get_run_status("demo_ifld", "run_001")

        self.assertEqual(status["status"], "complete")
        self.assertEqual(len(status["steps"]), 6)
        self.assertEqual(
            status["steps"][4]["summary"],
            "100 candidates processed · 20 selected",
        )
        self.assertEqual(status["steps"][5]["progress"]["fraction"], 1.0)

    def test_existing_structures_and_cross_run_reuse(self) -> None:
        run = create_run(self.root, run_id="run_002")
        output_root = run / "outputs"
        output = output_root / "output"
        write_json(
            output / "target_wt_workflow_status.json",
            {
                "workflow": "inverse_folding_and_library_design",
                "stages": {
                    "structure_prediction": {"status": "skipped"},
                    "structure_clustering": {"status": "skipped"},
                    "mutation_library": {"status": "skipped"},
                    "candidate_filter": {"status": "complete"},
                    "library_design": {"status": "complete"},
                }
            },
        )
        write_json(
            output_root / "work" / "existing_selected_structures" / "bootstrap_manifest.json",
            {"status": "complete"},
        )
        write_json(
            output_root / "work" / "existing_selected_structures" / "import_manifest.json",
            {"status": "complete"},
        )
        selected = output / "selected_structures" / "selected_000.cif"
        selected.parent.mkdir(parents=True)
        selected.write_text("data_test\n", encoding="utf-8")
        reused_mutation_dir = (
            self.root
            / "demo_ifld"
            / "runs"
            / "run_001"
            / "outputs"
            / "output"
            / "mutation_library"
        )
        write_json(
            reused_mutation_dir / "target_wt_mutation_library_manifest.json",
            {"status": "complete"},
        )
        write_json(
            output / "library_design" / "target_wt_library_design_manifest.json",
            {
                "status": "complete",
                "config": {
                    "mutation_library_dir": str(reused_mutation_dir)
                },
            },
        )
        write_json(
            output / "candidate_filter" / "target_wt_candidate_filter_manifest.json",
            {"status": "complete", "n_candidates": 20, "n_selected": 5},
        )

        status = ProjectMonitor(self.root).get_run_status("demo_ifld", "run_002")

        self.assertEqual(status["steps"][1]["status"], "reused")
        self.assertEqual(status["steps"][2]["status"], "reused")
        self.assertEqual(status["steps"][3]["status"], "reused")
        self.assertIn("run_001", status["steps"][3]["reused_from"])
        reused_artifact = next(
            artifact
            for artifact in status["artifacts"]
            if artifact["step_id"] == "03_mutation_library"
        )
        self.assertEqual(reused_artifact["source_run"], "run_001")
        self.assertEqual(reused_artifact["purpose"], "Reused next-step input")

    def test_failed_step_reports_error(self) -> None:
        run = create_run(self.root)
        write_json(
            run / "outputs" / "output" / "target_wt_workflow_status.json",
            {
                "workflow": "inverse_folding_and_library_design",
                "stages": {
                    "structure_prediction": {
                        "status": "failed",
                        "error": "GPU worker exited with code 1",
                    }
                },
            },
        )

        status = ProjectMonitor(self.root).get_run_status("demo_ifld", "run_001")

        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["steps"][1]["status"], "failed")
        self.assertEqual(
            status["steps"][1]["error"],
            "GPU worker exited with code 1",
        )

    def test_run_scoped_huoshan_partial_workflow(self) -> None:
        create_run_scoped_partial_project(self.root)
        monitor = ProjectMonitor(self.root)

        projects = monitor.list_projects()
        status = monitor.get_run_status(
            "demo_huoshan_partial",
            "20260725_001_partial",
        )

        self.assertEqual(projects[0]["workflow_family"], "partial_denovo")
        self.assertEqual(status["workflow_family"], "partial_denovo")
        self.assertEqual(status["task"], "CD98 · CD98_VHH")
        self.assertEqual(status["steps"][2]["status"], "complete")
        self.assertEqual(status["steps"][3]["status"], "complete")
        self.assertEqual(len(status["steps"][3]["units"]), 2)
        self.assertEqual(status["steps"][4]["status"], "failed")
        self.assertIn("AF3 shard failures", status["steps"][4]["error"])
        self.assertTrue(
            any(
                artifact["name"] == "all_designs_2500.csv"
                for artifact in status["artifacts"]
            )
        )

    def test_run_scoped_manifest_artifacts_include_real_file_metadata(self) -> None:
        run = create_run_scoped_partial_project(self.root)
        library = run / "steps" / "06_partial_library" / "library"
        library.mkdir(parents=True)
        heavy_csv = library / "oligo_pool_design_H.csv"
        empty_fasta = library / "oligo_pool_design_L.fasta"
        library_manifest = library / "library_design_manifest.json"
        missing_csv = library / "missing_candidates.csv"
        outside_run = self.root / "outside_run.csv"
        heavy_csv.write_text("id,sequence\n1,AAAA\n", encoding="utf-8")
        empty_fasta.write_text("", encoding="utf-8")
        write_json(library_manifest, {"status": "complete"})
        outside_run.write_text("must not be exposed\n", encoding="utf-8")
        canonical_manifest = (
            run
            / "steps"
            / "06_partial_library"
            / "full_downstream_manifest.json"
        )
        write_json(
            canonical_manifest,
            {
                "outputs": {
                    "oligo_pool_design_H_csv": str(heavy_csv),
                    "oligo_pool_design_L_fasta": str(empty_fasta),
                    "manifest_json": str(library_manifest),
                    "missing_candidates_csv": str(missing_csv),
                    "outside_run_csv": str(outside_run),
                },
                "sha256": {
                    heavy_csv.name: "abc123",
                },
            },
        )

        status = ProjectMonitor(self.root).get_run_status(
            "demo_huoshan_partial",
            "20260725_001_partial",
        )
        artifacts = {
            artifact["name"]: artifact
            for artifact in status["artifacts"]
            if artifact["step_id"] == "05_final_library"
        }

        self.assertEqual(artifacts[heavy_csv.name]["size"], heavy_csv.stat().st_size)
        self.assertTrue(artifacts[heavy_csv.name]["exists"])
        self.assertFalse(artifacts[heavy_csv.name]["empty"])
        self.assertTrue(artifacts[heavy_csv.name]["listed_by_manifest"])
        self.assertEqual(artifacts[heavy_csv.name]["sha256"], "abc123")
        self.assertEqual(artifacts[empty_fasta.name]["size"], 0)
        self.assertTrue(artifacts[empty_fasta.name]["empty"])
        self.assertTrue(artifacts[library_manifest.name]["is_manifest"])
        self.assertTrue(artifacts["full_downstream_manifest.json"]["is_manifest"])
        self.assertTrue(artifacts[missing_csv.name]["missing"])
        self.assertIsNone(artifacts[missing_csv.name]["size"])
        self.assertFalse(artifacts[missing_csv.name]["previewable"])
        self.assertNotIn(outside_run.name, artifacts)
        self.assertTrue(
            all(artifact["step_label"] == "Step 6" for artifact in artifacts.values())
        )

    def test_new_af3_activity_supersedes_stale_downstream_error(self) -> None:
        run = create_run_scoped_partial_project(self.root)
        failed_log = (
            run
            / "steps"
            / "05_developability_af3"
            / "nodes"
            / "huoshan_A800_02"
            / "node.log"
        )
        output = (
            run
            / "outputs"
            / "output"
            / "design"
            / "CD98"
            / "boltzgen_huoshan_A800_02"
            / "af3"
            / "output"
        )
        output.mkdir(parents=True)
        os.utime(failed_log, (100.0, 100.0))
        os.utime(output, (200.0, 200.0))

        status = ProjectMonitor(self.root).get_run_status(
            "demo_huoshan_partial",
            "20260725_001_partial",
        )
        downstream = status["steps"][4]
        resumed_node = next(
            unit
            for unit in downstream["units"]
            if unit["id"] == "huoshan_A800_02"
        )

        self.assertEqual(downstream["status"], "running")
        self.assertEqual(downstream["error"], "")
        self.assertEqual(resumed_node["status"], "running")
        self.assertEqual(resumed_node["error"], "")
        self.assertIn(
            "boltzgen_huoshan_A800_02/af3",
            resumed_node["runtime_evidence"],
        )

    def test_downstream_exposes_full_stage_and_gpu_detail(self) -> None:
        run = create_run_scoped_partial_project(self.root)
        node_id = "huoshan_A800_02"
        output = (
            run
            / "outputs"
            / "output"
            / "design"
            / "CD98"
            / f"boltzgen_{node_id}"
        )
        output.mkdir(parents=True)
        (output / "all_designs.csv").write_text(
            "name,heavy_chain,light_chain\n"
            "design_1,AAA,\n"
            "design_2,BBB,\n"
            "design_3,CCC,\n"
            "design_4,DDD,\n",
            encoding="utf-8",
        )
        (output / "developability_tap.csv").write_text(
            "name,TAP_filter\n"
            "design_1,pass\n"
            "design_2,pass\n"
            "design_3,fail\n"
            "design_4,pass\n",
            encoding="utf-8",
        )
        (output / "developability_biophi_solubility.csv").write_text(
            "name,PI_filter,BioPhi_filter,humanness_filter,"
            "liability_filter,solubility_filter\n"
            "design_1,pass,pass,pass,pass,pass\n"
            "design_2,pass,pass,pass,pass,pass\n"
            "design_3,fail,pass,pass,fail,pass\n"
            "design_4,pass,fail,fail,pass,fail\n",
            encoding="utf-8",
        )
        (output / "developability.csv").write_text(
            "name,all_filter_pass\n"
            "design_1,pass\n"
            "design_2,pass\n"
            "design_3,fail\n"
            "design_4,fail\n",
            encoding="utf-8",
        )
        shard_dir = output / "af3" / "shards"
        shard_dir.mkdir(parents=True)
        (shard_dir / "input_gpu0.csv").write_text(
            "name\ndesign_1\ndesign_2\n",
            encoding="utf-8",
        )
        for path in (
            output / "af3" / "input" / "gpu0" / "design_1",
            output / "af3" / "input" / "gpu0" / "design_2",
            output / "af3" / "input_with_msa" / "gpu0" / "design_1",
            output / "af3" / "output" / "design_1",
        ):
            path.mkdir(parents=True)
        failed_log = (
            run
            / "steps"
            / "05_developability_af3"
            / "nodes"
            / node_id
            / "node.log"
        )
        os.utime(failed_log, (100.0, 100.0))

        status = ProjectMonitor(self.root).get_run_status(
            "demo_huoshan_partial",
            "20260725_001_partial",
        )
        downstream = status["steps"][4]
        node = next(unit for unit in downstream["units"] if unit["id"] == node_id)
        stages = {stage["id"]: stage for stage in node["substeps"]}
        gpu0 = next(shard for shard in node["shards"] if shard["id"] == "gpu0")

        self.assertEqual(len(node["substeps"]), 13)
        self.assertEqual(len(node["shards"]), 8)
        self.assertEqual(stages["input_validation"]["status"], "complete")
        self.assertEqual(
            stages["developability_filter"]["metrics"],
            [
                {"label": "passed", "value": 2, "tone": "success"},
                {"label": "filtered", "value": 2, "tone": "muted"},
            ],
        )
        self.assertEqual(stages["af3_refolding"]["progress"]["completed"], 1)
        self.assertEqual(stages["af3_refolding"]["progress"]["total"], 2)
        self.assertEqual(gpu0["input_ready"], 2)
        self.assertEqual(gpu0["msa_ready"], 1)
        self.assertEqual(gpu0["refolded"], 1)
        self.assertEqual(gpu0["status"], "running")
        self.assertEqual(
            downstream["stage_totals"][-1]["id"],
            "cross_node_aggregation",
        )

    def test_preview_rejects_path_escape(self) -> None:
        monitor = ProjectMonitor(self.root)
        outside = self.root.parent / "outside.log"
        outside.write_text("secret", encoding="utf-8")
        self.addCleanup(outside.unlink, missing_ok=True)

        with self.assertRaisesRegex(ValueError, "outside"):
            monitor.preview_file(str(outside))

    def test_artifacts_only_include_workflow_deliverables(self) -> None:
        run = create_run(self.root)
        output = run / "outputs" / "output"
        selected_structure = output / "selected_structures" / "selected_000.cif"
        selected_structure.parent.mkdir(parents=True)
        selected_structure.write_text("data_test\n", encoding="utf-8")
        write_json(
            output / "mutation_library" / "target_wt_mutation_library_manifest.json",
            {"status": "complete"},
        )
        (output / "candidate_filter").mkdir(parents=True)
        (output / "candidate_filter" / "target_wt_selected_candidates.csv").write_text(
            "name,selected\ncandidate_1,true\n",
            encoding="utf-8",
        )
        (output / "candidate_filter" / "target_wt_candidate_filter_summary.csv").write_text(
            "selected,total\n1,1\n",
            encoding="utf-8",
        )
        write_json(
            output / "candidate_filter" / "target_wt_candidate_filter_manifest.json",
            {"status": "complete"},
        )
        excluded_log = output / "library_design" / "developability" / "logs" / "worker.log"
        excluded_log.parent.mkdir(parents=True)
        excluded_log.write_text("intermediate", encoding="utf-8")
        excluded_checkpoint = (
            output / "library_design" / "target_wt_expression_selection_checkpoint.csv"
        )
        excluded_checkpoint.write_text("intermediate", encoding="utf-8")
        (output / "library_design" / "target_wt_expression_design.csv").write_text(
            "name\ncandidate_1\n",
            encoding="utf-8",
        )
        deliverables = (
            output
            / "library_design"
            / "target_wt_library_design_deliverables"
        )
        deliverables.mkdir()
        (deliverables / "expression_library.csv").write_text(
            "name\ncandidate_1\n",
            encoding="utf-8",
        )
        sequences = deliverables / "sequences"
        sequences.mkdir()
        (sequences / "expression_library.fasta").write_text(
            ">candidate_1\nQVQLV\n",
            encoding="utf-8",
        )
        similarly_named_intermediate = (
            output
            / "library_design"
            / "target_wt_library_design_deliverables_work"
        )
        similarly_named_intermediate.mkdir()
        (similarly_named_intermediate / "checkpoint.csv").write_text(
            "intermediate\n",
            encoding="utf-8",
        )

        artifacts = ProjectMonitor(self.root).list_artifacts("demo_ifld", "run_001")
        paths = {artifact["relative_path"] for artifact in artifacts}

        self.assertIn(
            "outputs/output/selected_structures/selected_000.cif",
            paths,
        )
        self.assertIn(
            "outputs/output/mutation_library/target_wt_mutation_library_manifest.json",
            paths,
        )
        self.assertIn(
            "outputs/output/candidate_filter/target_wt_selected_candidates.csv",
            paths,
        )
        self.assertIn(
            "outputs/output/library_design/target_wt_expression_design.csv",
            paths,
        )
        self.assertIn(
            "outputs/output/library_design/target_wt_library_design_deliverables/"
            "expression_library.csv",
            paths,
        )
        self.assertIn(
            "outputs/output/library_design/target_wt_library_design_deliverables/"
            "sequences/expression_library.fasta",
            paths,
        )
        self.assertNotIn(str(excluded_log.relative_to(run)), paths)
        self.assertNotIn(str(excluded_checkpoint.relative_to(run)), paths)
        self.assertNotIn(
            str(
                (
                    similarly_named_intermediate / "checkpoint.csv"
                ).relative_to(run)
            ),
            paths,
        )
        final_deliverables = [
            artifact
            for artifact in artifacts
            if artifact["artifact_category"] == "final_deliverable"
        ]
        self.assertEqual(len(final_deliverables), 2)
        self.assertEqual(
            {artifact["group_id"] for artifact in final_deliverables},
            {"ifld_final_deliverables"},
        )
        self.assertEqual(
            {artifact["group_label"] for artifact in final_deliverables},
            {"Final deliverables"},
        )
        self.assertEqual(
            {artifact["group_purpose"] for artifact in final_deliverables},
            {"Ready for delivery"},
        )
        self.assertTrue(
            all(
                "_library_design_deliverables/" in artifact["relative_path"]
                for artifact in final_deliverables
            )
        )
        step_five_outputs = [
            artifact
            for artifact in artifacts
            if artifact["step_number"] == 5
            and artifact["artifact_category"] == "step_output"
        ]
        self.assertTrue(step_five_outputs)
        self.assertEqual(
            {artifact["group_id"] for artifact in step_five_outputs},
            {"05_library_design"},
        )
        self.assertEqual(
            {artifact["purpose"] for artifact in step_five_outputs},
            {"Step output"},
        )

    def test_partial_denovo_is_a_separate_running_workflow(self) -> None:
        project = create_partial_project(self.root)
        for node in ("ln01", "ln02"):
            metrics = (
                project
                / "boltzgen"
                / "workbench"
                / f"CD98_{node}"
                / "final_ranked_designs"
                / "final_designs_metrics_500.csv"
            )
            metrics.parent.mkdir(parents=True)
            metrics.write_text("name,score\ndesign_1,0.9\n", encoding="utf-8")
        status_dir = project / "postprocess" / "status"
        status_dir.mkdir(parents=True)
        (status_dir / "ln01.status").write_text(
            "attempt_1 2026-07-28T12:00:00+08:00\n",
            encoding="utf-8",
        )
        (status_dir / "ln02.status").write_text(
            "waiting_for_idle_gpus 2026-07-28T12:00:00+08:00\n",
            encoding="utf-8",
        )
        (status_dir / "final_library.status").write_text(
            "waiting_for_all_node_postprocess 2026-07-28T12:00:00+08:00\n",
            encoding="utf-8",
        )

        monitor = ProjectMonitor(self.root)
        runs = monitor.list_runs(project.name)
        status = monitor.get_run_status(project.name, "workspace")

        self.assertEqual(runs[0]["id"], "workspace")
        self.assertEqual(status["workflow_family"], "partial_denovo")
        self.assertEqual(status["status"], "running")
        self.assertEqual(len(status["steps"]), 6)
        self.assertEqual(status["steps"][3]["status"], "complete")
        self.assertEqual(
            [stage["title"] for stage in status["steps"][3]["units"][0]["substeps"]],
            ["Design", "Inverse folding", "Folding", "Analysis", "Filtering"],
        )
        self.assertEqual(len(status["steps"][3]["stage_totals"]), 5)
        self.assertEqual(status["steps"][4]["status"], "running")
        self.assertEqual(status["steps"][4]["detail_kind"], "downstream")
        self.assertEqual(len(status["steps"][4]["units"][0]["substeps"]), 13)
        self.assertEqual(len(status["steps"][4]["units"][0]["shards"]), 8)
        self.assertTrue(status["steps"][4]["units"][0]["shards"][0]["unavailable"])
        self.assertEqual(status["steps"][5]["progress"]["total"], 12000)
        self.assertEqual(status["steps"][5]["title"], "Final 12,000 Library Selection")
        self.assertEqual(
            {artifact["step_id"] for artifact in status["artifacts"]},
            {
                "01_target_preparation",
                "02_partial_scaffolds",
                "03_boltzgen_generation",
            },
        )

    def test_boltzgen_log_progress_finds_stage_before_large_tail(self) -> None:
        project = create_partial_project(self.root, "large_log_partial")
        log_path = project / "logs" / "boltzgen_generation_ln10.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text(
            "[Step 3/5] Folding - start\n"
            + ("earlier output without a stage marker\n" * 30_000)
            + "40%|████      | 9,519/24,000 [01:00<02:00]\n".replace(",", ""),
            encoding="utf-8",
        )

        progress = ProjectMonitor(self.root)._boltzgen_log_progress(project, "ln10")

        self.assertEqual(progress["stage_index"], 3)
        self.assertEqual(progress["completed"], 9519)
        self.assertEqual(progress["total"], 24000)
        self.assertAlmostEqual(progress["fraction"], 9519 / 24000)

    def test_boltzgen_log_progress_supports_processing_samples(self) -> None:
        project = create_partial_project(self.root, "processing_samples_partial")
        log_path = project / "logs" / "boltzgen_generation_ln10.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text(
            "Processing samples: 40%|████      | 9519/24000 [01:00<02:00]\n",
            encoding="utf-8",
        )

        progress = ProjectMonitor(self.root)._boltzgen_log_progress(project, "ln10")

        self.assertEqual(progress["stage_index"], 4)
        self.assertEqual(progress["stage"], "analysis")
        self.assertEqual(progress["completed"], 9519)
        self.assertEqual(progress["total"], 24000)
        self.assertTrue(progress["exact_count"])

    def test_workspace_boltzgen_progress_falls_back_to_output_files(self) -> None:
        project = create_partial_project(self.root, "workspace_output_partial")
        config = json.loads((project / "project_config.json").read_text(encoding="utf-8"))
        config["generation"] = {
            "node_count": 1,
            "num_designs_per_node": 24,
            "num_designs_total": 24,
        }
        write_json(project / "project_config.json", config)
        write_json(
            project / "downstream_config.json",
            {"shards": [{"key": "ln10", "node": "ln10", "num_designs": 24}]},
        )
        workbench = project / "boltzgen" / "workbench" / "CD98_ln10"
        for directory, suffix in (
            (
                workbench
                / "intermediate_designs_inverse_folded"
                / "fold_out_npz",
                ".npz",
            ),
            (
                workbench
                / "intermediate_designs_inverse_folded"
                / "refold_cif",
                ".cif",
            ),
        ):
            directory.mkdir(parents=True)
            for index in range(12):
                (directory / f"design_{index:03d}{suffix}").write_text(
                    "result\n",
                    encoding="utf-8",
                )

        status = ProjectMonitor(self.root).get_run_status(project.name, "workspace")
        unit = status["steps"][3]["units"][0]
        folding = next(stage for stage in unit["substeps"] if stage["id"] == "folding")

        self.assertEqual(unit["id"], "ln10")
        self.assertEqual(unit["status"], "running")
        self.assertEqual(unit["current_stage"], "Folding")
        self.assertEqual(folding["status"], "running")
        self.assertEqual(folding["progress"]["completed"], 12)
        self.assertEqual(folding["progress"]["total"], 24)
        self.assertTrue(unit["updated_at"])

    def test_run_scoped_boltzgen_progress_falls_back_to_output_files(self) -> None:
        run = create_run_scoped_partial_project(
            self.root,
            "run_scoped_output_partial",
        )
        workbench = run / "outputs" / "boltzgen" / "workbench" / "CD98_ln10"
        for directory, suffix in (
            (
                workbench
                / "intermediate_designs_inverse_folded"
                / "fold_out_npz",
                ".npz",
            ),
            (
                workbench
                / "intermediate_designs_inverse_folded"
                / "refold_cif",
                ".cif",
            ),
        ):
            directory.mkdir(parents=True)
            for index in range(7):
                (directory / f"design_{index:03d}{suffix}").write_text(
                    "result\n",
                    encoding="utf-8",
                )

        monitor = ProjectMonitor(self.root)
        status = monitor.get_run_status(run.parent.parent.name, run.name)
        unit = next(item for item in status["steps"][3]["units"] if item["id"] == "ln10")

        self.assertEqual(unit["status"], "running")
        self.assertEqual(unit["current_stage"], "Folding")
        self.assertEqual(unit["progress"]["completed"], 7)
        self.assertEqual(unit["progress"]["total"], 24000)

    def test_boltzgen_nfs_error_keeps_last_nonzero_progress(self) -> None:
        project = create_partial_project(self.root, "nfs_cache_partial")
        workbench = project / "boltzgen" / "workbench" / "CD98_ln10"
        directories = (
            (
                workbench
                / "intermediate_designs_inverse_folded"
                / "fold_out_npz",
                ".npz",
            ),
            (
                workbench
                / "intermediate_designs_inverse_folded"
                / "refold_cif",
                ".cif",
            ),
        )
        for directory, suffix in directories:
            directory.mkdir(parents=True)
            for index in range(5):
                (directory / f"design_{index:03d}{suffix}").write_text(
                    "result\n",
                    encoding="utf-8",
                )
        monitor = ProjectMonitor(self.root)
        first = monitor._boltzgen_substeps(
            project,
            workbench,
            unit_id="ln10",
            target=24,
            cancelled=False,
        )
        for directory, _ in directories:
            os.utime(directory)

        with mock.patch(
            "monitor_dashboard.monitor.os.scandir",
            side_effect=OSError("Input/output error"),
        ):
            second = monitor._boltzgen_substeps(
                project,
                workbench,
                unit_id="ln10",
                target=24,
                cancelled=False,
            )

        first_folding = next(stage for stage in first if stage["id"] == "folding")
        second_folding = next(stage for stage in second if stage["id"] == "folding")
        self.assertEqual(first_folding["progress"]["completed"], 5)
        self.assertEqual(second_folding["progress"]["completed"], 5)
        self.assertEqual(second_folding["status"], "running")

    def test_boltzgen_final_metrics_override_live_log_progress(self) -> None:
        project = create_partial_project(self.root, "final_metrics_partial")
        workbench = project / "boltzgen" / "workbench" / "CD98_ln10"
        ranked = workbench / "final_ranked_designs"
        ranked.mkdir(parents=True)
        (ranked / "all_designs_metrics.csv").write_text(
            "id\n1\n2\n",
            encoding="utf-8",
        )
        (ranked / "final_designs_metrics_500.csv").write_text(
            "id\n1\n",
            encoding="utf-8",
        )
        log_path = project / "logs" / "boltzgen_generation_ln10.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text(
            "Processing samples: 10%|█         | 1/10 [00:01<00:09]\n",
            encoding="utf-8",
        )

        stages = ProjectMonitor(self.root)._boltzgen_substeps(
            project,
            workbench,
            unit_id="ln10",
            target=10,
            cancelled=False,
        )

        self.assertEqual({stage["status"] for stage in stages}, {"complete"})
        filtering = next(stage for stage in stages if stage["id"] == "filtering")
        self.assertEqual(filtering["result_count"], 1)

    def test_configured_downstream_groups_logical_shards_by_physical_node(self) -> None:
        project = create_partial_project(self.root, "configured_partial_denovo")
        write_json(
            project / "downstream_config.json",
            {
                "target": "CD98",
                "library": {"per_node_budget": 500},
                "shards": [
                    {"key": "ln06", "node": "ln06", "gpus": list(range(8))},
                    {"key": "ln07", "node": "ln07", "gpus": [3, 4]},
                    {
                        "key": "ln07_topup",
                        "node": "ln07",
                        "gpus": [0, 1, 2, 5, 6, 7],
                        "depends_on": "ln07",
                    },
                ],
            },
        )
        for shard in ("ln06", "ln07", "ln07_topup"):
            write_json(
                project / "state" / "downstream" / shard / "status.json",
                {"phase": "waiting_for_boltzgen"},
            )

        status = ProjectMonitor(self.root).get_run_status(project.name, "workspace")
        downstream = status["steps"][4]

        self.assertEqual(downstream["status"], "pending")
        self.assertEqual(
            [unit["id"] for unit in downstream["units"]],
            ["ln06", "ln07"],
        )
        ln07 = downstream["units"][1]
        self.assertEqual(ln07["current_stage"], "Waiting for BoltzGen")
        self.assertEqual(
            [shard["id"] for shard in ln07["shards"]],
            ["ln07", "ln07_topup"],
        )
        self.assertEqual(ln07["shards"][0]["assigned_gpus"], [3, 4])
        self.assertEqual(ln07["shards"][1]["dependency"], "ln07")
        self.assertEqual(len(ln07["substeps"]), 13)
        af3_input = next(
            stage
            for stage in downstream["stage_totals"]
            if stage["id"] == "af3_sharding"
        )
        self.assertEqual(af3_input["progress"]["total"], 1500)

    def test_reported_progress_is_exposed_to_run_and_project_summaries(self) -> None:
        project = create_partial_project(self.root, "reported_partial_denovo")
        write_json(
            project / "postprocess" / "status" / "progress.json",
            {
                "schema_version": 1,
                "attempt": 2,
                "status": "running",
                "current_stage": "af3_refolding",
                "started_at": "2099-07-29T12:00:00+00:00",
                "heartbeat_at": "2099-07-29T12:01:00+00:00",
                "stages": {
                    "af3_refolding": {
                        "title": "AF3 refolding",
                        "status": "running",
                        "completed": 12,
                        "total": 20,
                    }
                },
            },
        )

        monitor = ProjectMonitor(self.root)
        status = monitor.get_run_status(project.name, "workspace")
        listing = next(
            item for item in monitor.list_projects() if item["name"] == project.name
        )

        self.assertEqual(status["runtime"]["progress_source"], "reported")
        self.assertEqual(status["runtime"]["current_stage"], "AF3 refolding")
        self.assertEqual(status["runtime"]["progress"]["completed"], 12)
        self.assertEqual(status["runtime"]["attempt"], 2)
        self.assertEqual(listing["current_stage"], "AF3 refolding")
        self.assertEqual(listing["progress"]["total"], 20)

    def test_inferred_attempt_history_uses_previous_postprocess_logs(self) -> None:
        project = create_partial_project(self.root, "attempt_history_partial")
        status_path = project / "postprocess" / "status" / "ln01.status"
        status_path.parent.mkdir(parents=True)
        status_path.write_text(
            "attempt_3 2026-07-29T12:00:00+08:00\n",
            encoding="utf-8",
        )
        log_dir = project / "logs" / "postprocess"
        log_dir.mkdir(parents=True)
        (log_dir / "ln01.attempt_1.log").write_text("failed\n", encoding="utf-8")
        (log_dir / "ln01.attempt_2.log").write_text("failed\n", encoding="utf-8")

        status = ProjectMonitor(self.root).get_run_status(project.name, "workspace")

        self.assertEqual(status["runtime"]["attempt"], 3)
        self.assertEqual(
            [item["attempt"] for item in status["runtime"]["attempts"]],
            [1, 2],
        )
        self.assertTrue(status["runtime"]["attempts"][0]["inferred"])

    def test_user_terminated_partial_denovo_is_cancelled_not_failed(self) -> None:
        project = create_partial_project(self.root, "stopped_partial_denovo")
        workbench = project / "boltzgen" / "workbench" / "CD98_ln08"
        (workbench / "intermediate_designs").mkdir(parents=True)
        write_json(
            project / "execution_plan.json",
            {
                "status": "terminated_by_user",
                "nodes": ["ln08", "ln10"],
                "final_library": {
                    "total_boltzgen_designs": 120000,
                    "library_size": 12000,
                },
                "runtime": {
                    "termination_reason": "user_requested",
                    "termination_outputs": {
                        "ln08": {"cif_count": 23460},
                        "ln10": {"cif_count": 23410},
                    },
                },
                "preflight": {"input_validation": "passed"},
            },
        )

        status = ProjectMonitor(self.root).get_run_status(project.name, "workspace")

        self.assertEqual(status["status"], "cancelled")
        self.assertEqual(status["steps"][3]["status"], "cancelled")
        self.assertEqual(status["steps"][3]["error"], "")
        self.assertIn("46,870", status["steps"][3]["summary"])
        self.assertEqual(status["steps"][4]["status"], "cancelled")
        self.assertEqual(status["steps"][5]["progress"]["total"], 12000)

    def test_project_listing_exposes_workflow_families(self) -> None:
        create_run(self.root)
        create_partial_project(self.root)

        projects = ProjectMonitor(self.root).list_projects()
        families = {project["name"]: project["workflow_family"] for project in projects}

        self.assertEqual(families["demo_ifld"], "ifld")
        self.assertEqual(families["demo_partial_denovo"], "partial_denovo")


if __name__ == "__main__":
    unittest.main()
