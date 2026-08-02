from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SUCCESS = {"complete", "completed", "success", "succeeded"}
FAILED = {"failed", "error"}
RUNNING = {"running", "in_progress", "active", "executing"}
BLOCKED = {"blocked", "stalled"}
SKIPPED = {"skipped", "disabled"}
CANCELLED = {"cancelled", "canceled", "stopped", "terminated", "terminated_by_user"}
TEXT_EXTENSIONS = {
    ".csv",
    ".fasta",
    ".fa",
    ".json",
    ".log",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
}

STEP_DEFINITIONS = (
    ("00_initialize", "Initialize & Validate", "配置、Profile 与运行环境校验"),
    ("01_structure_prediction", "Structure Prediction", "WT 结构预测或已有结构导入"),
    ("02_structure_clustering", "Structure Clustering", "结构聚类与代表结构选择"),
    ("03_mutation_library", "Mutation Library & Scoring", "候选生成与突变效应评分"),
    ("04_candidate_filter", "Refolding & Candidate Filter", "流式复折叠、RMSD 与候选筛选"),
    ("05_library_design", "Library Design", "表达候选、可开发性与文库交付"),
)

PARTIAL_STEP_DEFINITIONS = (
    ("00_initialize", "Initialize & Validate", "项目配置、输入与执行前检查"),
    ("01_target_preparation", "Target & Framework Prep", "抗原结构、VHH framework 与位点校验"),
    ("02_partial_scaffolds", "Partial Scaffold Library", "生成并校验 partial scaffold 组合"),
    ("03_boltzgen_generation", "BoltzGen Generation", "多节点生成、分析与过滤"),
    ("04_downstream_screening", "Downstream Screening", "可开发性筛选与结构复折叠"),
    ("05_final_library", "Final Partial Library", "跨节点聚合与最终文库交付"),
)

ARTIFACT_STEP_META = {
    "01_structure_prediction": ("Step 1", "Next-step input"),
    "02_structure_clustering": ("Step 2", "Next-step input"),
    "03_mutation_library": ("Step 3", "Next-step input"),
    "04_candidate_filter": ("Step 4", "Next-step input"),
    "05_library_design": ("Step 5", "Final result"),
}

FALLBACK_EXPECTED_OUTPUTS = {
    "01_structure_prediction": [
        "outputs/output/<task>/<task>/<task>_ranking_scores.csv",
        "outputs/output/<task>_structure_manifest.json",
    ],
    "02_structure_clustering": [
        "outputs/output/summary/<task>_structure_clusters.csv",
        "outputs/output/summary/<task>_structure_cluster_manifest.json",
        "outputs/output/selected_structures/",
    ],
    "03_mutation_library": [
        "outputs/output/mutation_library/mutation_library_manifest.json",
    ],
    "04_candidate_filter": [
        "outputs/output/candidate_filter/<task>_selected_candidates.csv",
        "outputs/output/candidate_filter/<task>_candidate_filter_manifest.json",
    ],
    "05_library_design": [
        "outputs/output/library_design/<task>_expression_design.csv",
        "outputs/output/library_design/<task>_expression_selection_status.json",
        "outputs/output/library_design/<task>_expression_selected_structures/manifest.csv",
        "outputs/output/library_design/<task>_expression_selected_structures.tar.gz",
        "outputs/output/library_design/<task>_library_design_manifest.json",
        "outputs/output/library_design/<task>_library_design_deliverables/",
        "outputs/output/<task>_inverse_folding_library_design_manifest.json",
    ],
}

SUMMARY_OUTPUT_GLOBS = {
    "04_candidate_filter": (
        "outputs/output/candidate_filter/*_candidate_filter_summary.csv",
    ),
    "05_library_design": (
        "outputs/output/library_design/*_library_summary.json",
    ),
}


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_simple_yaml(path: Path | None) -> dict[str, Any]:
    """Read the mapping-only subset used by generated AuraPilot run configs."""
    if path is None or not path.is_file():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith(("#", "- ")):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        content = raw_line.strip()
        if ":" not in content:
            continue
        key, raw_value = content.split(":", 1)
        key = key.strip()
        if not key:
            continue
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        value = raw_value.strip()
        if not value:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
            continue
        if value.startswith(('"', "'")) and value.endswith(value[0]):
            parsed: Any = value[1:-1]
        elif value.lower() in {"true", "false"}:
            parsed = value.lower() == "true"
        elif re.fullmatch(r"-?\d+", value):
            parsed = int(value)
        elif re.fullmatch(r"-?\d+\.\d+", value):
            parsed = float(value)
        else:
            parsed = value
        parent[key] = parsed
    return root


def _first_existing(paths: Iterable[Path]) -> Path | None:
    return next((path for path in paths if path.is_file()), None)


def _first_glob(root: Path, pattern: str) -> Path | None:
    try:
        return next((path for path in sorted(root.glob(pattern)) if path.is_file()), None)
    except OSError:
        return None


def _normalize_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if status in SUCCESS:
        return "complete"
    if status in FAILED:
        return "failed"
    if status in RUNNING:
        return "running"
    if status in BLOCKED:
        return "blocked"
    if status in SKIPPED:
        return "skipped"
    if status in CANCELLED:
        return "cancelled"
    if status in {"degraded", "partial"}:
        return "degraded"
    if status in {"reused", "cached"}:
        return "reused"
    return "pending"


def _iso_mtime(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return ""

def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _error_message(*payloads: dict[str, Any]) -> str:
    for payload in payloads:
        for key in (
            "error",
            "error_summary",
            "blocking_reason",
            "failure_reason",
            "reason",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        errors = payload.get("errors") or payload.get("failures")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors[:3])
    return ""


def _progress_payload(payload: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        "completed": ("completed", "complete", "done", "rmsd_completed", "selected"),
        "total": ("total", "target", "accepted", "n_candidates"),
        "failed": ("failed", "rmsd_failed"),
        "pending": ("pending", "rmsd_pending"),
    }
    result: dict[str, Any] = {}
    for destination, keys in aliases.items():
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result[destination] = value
                break
    if "completed" in result and "total" in result and result["total"]:
        result["fraction"] = min(1.0, float(result["completed"]) / float(result["total"]))
    return result


def _count_files(path: Path, patterns: tuple[str, ...] = ("*",)) -> int:
    if not path.is_dir():
        return 0
    count = 0
    for pattern in patterns:
        try:
            count += sum(1 for item in path.glob(pattern) if item.is_file())
        except OSError:
            continue
    return count


def _safe_resolve(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("Path is outside the monitored project root")
    return resolved


class ProjectMonitor:
    """Aggregate AuraPilot filesystem state without mutating workflow data."""

    def __init__(self, project_root: str | Path = "/nfs/project") -> None:
        self.project_root = Path(project_root).resolve()
        self._count_cache: dict[tuple[str, str], tuple[int, int, int]] = {}
        self._summary_cache: dict[tuple[str, str], tuple[int, int, Any]] = {}
        self._directory_snapshot_cache: dict[
            tuple[str, str], tuple[int, int, dict[str, Any]]
        ] = {}
        self._boltzgen_progress_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._boltzgen_workbench_cache: dict[str, list[Path]] = {}
        self._boltzgen_activity_cache: dict[str, str] = {}
        self._boltzgen_final_metrics_cache: dict[str, Path] = {}

    @staticmethod
    def _active_summary_item(steps: list[dict[str, Any]]) -> dict[str, Any]:
        active_step = next(
            (
                step
                for status in ("failed", "blocked", "running", "degraded", "pending")
                for step in steps
                if step.get("status") == status
            ),
            {},
        )
        stages = active_step.get("stage_totals")
        stages = stages if isinstance(stages, list) else []
        failed_stage = next(
            (
                stage
                for stage in stages
                if stage.get("status") in {"failed", "blocked"}
            ),
            None,
        )
        running_stages = [
            stage for stage in stages if stage.get("status") == "running"
        ]
        return failed_stage or (running_stages[-1] if running_stages else active_step)

    @classmethod
    def _project_overview(cls, summary: dict[str, Any]) -> dict[str, Any]:
        steps = summary.get("steps")
        steps = steps if isinstance(steps, list) else []
        active = cls._active_summary_item(steps)
        progress = active.get("progress")
        progress = progress if isinstance(progress, dict) else {}
        errors = [
            str(item.get("error") or "")
            for item in (
                steps
                + [
                    unit
                    for step in steps
                    for unit in (
                        step.get("units")
                        if isinstance(step.get("units"), list)
                        else []
                    )
                ]
            )
            if item.get("error")
        ]
        runtime = summary.get("runtime")
        runtime = runtime if isinstance(runtime, dict) else {}
        return {
            "current_stage": str(
                runtime.get("current_stage")
                or active.get("title")
                or active.get("id")
                or ""
            ),
            "progress": (
                runtime.get("progress")
                if isinstance(runtime.get("progress"), dict)
                else progress
            ),
            "error": str(runtime.get("error") or (errors[0] if errors else "")),
            "progress_source": str(runtime.get("progress_source") or "inferred"),
            "attempt": runtime.get("attempt"),
            "started_at": str(runtime.get("started_at") or ""),
            "stalled": bool(runtime.get("stalled")),
        }

    def _runtime_metadata(
        self,
        root: Path,
        *,
        status: str,
        updated_at: str,
    ) -> dict[str, Any]:
        progress_path = _first_existing(
            (
                root / "progress.json",
                root / "status" / "progress.json",
                root / "postprocess" / "status" / "progress.json",
                root / "state" / "progress.json",
            )
        )
        payload = _read_json(progress_path)
        heartbeat_at = str(
            payload.get("heartbeat_at")
            or payload.get("updated_at")
            or payload.get("recorded_at")
            or ""
        )
        attempt = payload.get("attempt")
        started_at = str(payload.get("started_at") or "")
        attempts = payload.get("attempts")
        attempts = attempts if isinstance(attempts, list) else []
        current_stage = str(payload.get("current_stage") or "")
        stages = payload.get("stages")
        stages = stages if isinstance(stages, dict) else {}
        stage_payload = stages.get(current_stage)
        stage_payload = stage_payload if isinstance(stage_payload, dict) else {}
        reported_progress = payload.get("progress")
        reported_progress = (
            reported_progress if isinstance(reported_progress, dict) else {}
        )
        if not reported_progress and stage_payload:
            reported_progress = _progress_payload(stage_payload)

        status_files = []
        for pattern in (
            "postprocess/status/*.status",
            "state/downstream/*/status.json",
            "steps/*/nodes/*/status.json",
        ):
            try:
                status_files.extend(path for path in root.glob(pattern) if path.is_file())
            except OSError:
                continue
        for path in status_files:
            if path.suffix == ".json":
                item = _read_json(path)
                item_attempt = item.get("attempt")
                if isinstance(item_attempt, (int, float)):
                    attempt = max(int(attempt or 0), int(item_attempt))
                item_started = str(item.get("started_at") or "")
                if item_started and (
                    not started_at
                    or (_parse_datetime(item_started) or datetime.max.replace(tzinfo=timezone.utc))
                    < (_parse_datetime(started_at) or datetime.max.replace(tzinfo=timezone.utc))
                ):
                    started_at = item_started
                item_heartbeat = str(
                    item.get("heartbeat_at") or item.get("updated_at") or ""
                )
                if item_heartbeat and (
                    not heartbeat_at
                    or (_parse_datetime(item_heartbeat) or datetime.min.replace(tzinfo=timezone.utc))
                    > (_parse_datetime(heartbeat_at) or datetime.min.replace(tzinfo=timezone.utc))
                ):
                    heartbeat_at = item_heartbeat
                continue
            try:
                first_line = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines()[0]
            except (OSError, IndexError):
                continue
            match = re.match(r"(?:attempt_|retry_)(\d+)(?:\S*)?\s+(.+)", first_line)
            if match:
                attempt = max(int(attempt or 0), int(match.group(1)))
                if first_line.startswith("attempt_") and not started_at:
                    started_at = match.group(2).strip()

        if not attempts and isinstance(attempt, (int, float)) and int(attempt) > 1:
            inferred_attempts: dict[int, list[float]] = {}
            try:
                attempt_logs = list(
                    (root / "logs" / "postprocess").glob("*.attempt_*.log")
                )
            except OSError:
                attempt_logs = []
            for path in attempt_logs:
                match = re.search(r"\.attempt_(\d+)\.log$", path.name)
                if not match:
                    continue
                number = int(match.group(1))
                if number >= int(attempt):
                    continue
                try:
                    inferred_attempts.setdefault(number, []).append(
                        path.stat().st_mtime
                    )
                except OSError:
                    continue
            attempts = [
                {
                    "attempt": number,
                    "status": "superseded",
                    "started_at": datetime.fromtimestamp(
                        min(timestamps),
                        timezone.utc,
                    ).isoformat(),
                    "finished_at": datetime.fromtimestamp(
                        max(timestamps),
                        timezone.utc,
                    ).isoformat(),
                    "error": "",
                    "inferred": True,
                }
                for number, timestamps in sorted(inferred_attempts.items())
            ]

        activity_at = heartbeat_at or updated_at
        heartbeat = _parse_datetime(heartbeat_at)
        now = datetime.now(timezone.utc)
        stalled = bool(
            progress_path
            and status == "running"
            and heartbeat
            and (now - heartbeat).total_seconds() > 300
        )
        started = _parse_datetime(started_at)
        duration_seconds = (
            max(0, int((now - started).total_seconds()))
            if started
            else None
        )
        eta_seconds = payload.get("eta_seconds")
        return {
            "progress_source": "reported" if progress_path else "inferred",
            "reported_status": (
                _normalize_status(payload.get("status"))
                if progress_path
                else ""
            ),
            "progress_path": str(progress_path) if progress_path else "",
            "activity_at": activity_at,
            "heartbeat_at": heartbeat_at if progress_path else "",
            "stalled": stalled,
            "stale_after_seconds": 300,
            "attempt": int(attempt) if isinstance(attempt, (int, float)) else None,
            "started_at": started_at,
            "duration_seconds": duration_seconds,
            "eta_seconds": (
                int(eta_seconds)
                if isinstance(eta_seconds, (int, float))
                else None
            ),
            "attempts": attempts,
            "current_stage": str(
                stage_payload.get("title") or current_stage
            ),
            "progress": reported_progress,
            "error": _error_message(payload, stage_payload),
        }

    def list_projects(self) -> list[dict[str, Any]]:
        if not self.project_root.is_dir():
            return []
        projects: list[dict[str, Any]] = []
        for path in sorted(self.project_root.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            if self._is_partial_denovo(path):
                summary = self._get_partial_status(path, include_artifacts=False)
                projects.append(
                    {
                        "name": path.name,
                        "path": str(path),
                        "run_count": 1,
                        "latest_run": "live workspace",
                        "status": summary["status"],
                        "updated_at": summary["updated_at"],
                        "workflow": summary["workflow"],
                        "workflow_family": "partial_denovo",
                        "failed_steps": sum(
                            1
                            for step in summary["steps"]
                            if step["status"] in {"failed", "blocked"}
                        ),
                        **self._project_overview(summary),
                    }
                )
                continue
            partial_runs = self._run_scoped_partial_paths(path)
            if partial_runs:
                latest = partial_runs[-1]
                summary = self._get_run_scoped_partial_status(
                    path,
                    latest,
                    include_artifacts=False,
                )
                projects.append(
                    {
                        "name": path.name,
                        "path": str(path),
                        "run_count": len(partial_runs),
                        "latest_run": latest.name,
                        "status": summary["status"],
                        "updated_at": summary["updated_at"],
                        "workflow": summary["workflow"],
                        "workflow_family": "partial_denovo",
                        "failed_steps": sum(
                            1
                            for step in summary["steps"]
                            if step["status"] in {"failed", "blocked"}
                        ),
                        **self._project_overview(summary),
                    }
                )
                continue
            runs = self._run_paths(path)
            if not runs:
                continue
            latest = runs[-1]
            if not self._is_ifld_run(path, latest):
                continue
            summary = self.get_run_status(path.name, latest.name, include_artifacts=False)
            projects.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "run_count": len(runs),
                    "latest_run": latest.name,
                    "status": summary["status"],
                    "updated_at": summary["updated_at"],
                    "workflow": summary["workflow"],
                    "workflow_family": "ifld",
                    "failed_steps": sum(
                        1
                        for step in summary["steps"]
                        if step["status"] in {"failed", "blocked"}
                    ),
                    **self._project_overview(summary),
                }
            )
        projects.sort(key=lambda item: item["updated_at"], reverse=True)
        return projects

    def list_runs(self, project_name: str) -> list[dict[str, Any]]:
        project = self._project_path(project_name)
        if self._is_partial_denovo(project):
            summary = self._get_partial_status(project, include_artifacts=False)
            return [
                {
                    "id": "workspace",
                    "display_name": "Live workspace",
                    "path": str(project),
                    "status": summary["status"],
                    "workflow": summary["workflow"],
                    "profile": summary["profile"],
                    "updated_at": summary["updated_at"],
                }
            ]
        partial_runs = self._run_scoped_partial_paths(project)
        if partial_runs:
            results = []
            for path in reversed(partial_runs):
                summary = self._get_run_scoped_partial_status(
                    project,
                    path,
                    include_artifacts=False,
                )
                results.append(
                    {
                        "id": path.name,
                        "path": str(path),
                        "status": summary["status"],
                        "workflow": summary["workflow"],
                        "profile": summary["profile"],
                        "updated_at": summary["updated_at"],
                    }
                )
            return results
        results = []
        for path in reversed(
            [
                run
                for run in self._run_paths(project)
                if self._is_ifld_run(project, run)
            ]
        ):
            summary = self.get_run_status(project_name, path.name, include_artifacts=False)
            results.append(
                {
                    "id": path.name,
                    "path": str(path),
                    "status": summary["status"],
                    "workflow": summary["workflow"],
                    "profile": summary["profile"],
                    "updated_at": summary["updated_at"],
                }
            )
        return results

    def get_run_status(
        self,
        project_name: str,
        run_id: str,
        *,
        include_artifacts: bool = True,
    ) -> dict[str, Any]:
        project = self._project_path(project_name)
        if self._is_partial_denovo(project):
            if run_id != "workspace":
                raise FileNotFoundError(run_id)
            return self._get_partial_status(project, include_artifacts=include_artifacts)
        run = self._run_path(project, run_id)
        if self._is_run_scoped_partial(run):
            return self._get_run_scoped_partial_status(
                project,
                run,
                include_artifacts=include_artifacts,
            )
        output_root = run / "outputs"
        output_dir = output_root / "output"

        project_input_path = _first_existing(
            (run / "project_input.json", project / "project_input.json")
        )
        resolved_path = _first_existing(
            (run / "resolved_config.json", project / "resolved_config.json")
        )
        lock_path = _first_existing((run / "profile.lock.json", project / "profile.lock.json"))
        command_path = _first_existing((run / "command.sh", project / "command.sh"))
        project_input = _read_json(project_input_path)
        resolved = _read_json(resolved_path)
        lock = _read_json(lock_path)

        workflow_status_path = _first_glob(output_dir, "*_workflow_status.json")
        workflow_status = _read_json(workflow_status_path)
        stages = workflow_status.get("stages")
        if not isinstance(stages, dict):
            stages = {}

        profile = str(
            lock.get("profile")
            or project_input.get("profile")
            or resolved.get("aurapilot_profile")
            or ""
        )
        workflow = str(workflow_status.get("workflow") or lock.get("workflow") or "")
        if not workflow:
            workflow = (
                "inverse_folding_and_library_design"
                if "inverse_folding_and_library_design" in profile or "ifld" in project.name.lower()
                else "unknown"
            )
        task = str(
            workflow_status.get("task")
            or resolved.get("task")
            or f"{resolved.get('target', '')}_{resolved.get('wt_name', '')}".strip("_")
        )

        failed_marker_path = _first_existing((run / "FAILED.json",))
        failed_marker = _read_json(failed_marker_path)

        steps = [
            self._step_initialize(
                run,
                project_input_path,
                resolved_path,
                lock_path,
                command_path,
                failed_marker,
            ),
            self._step_structure_prediction(output_root, stages),
            self._step_structure_clustering(output_root, stages),
            self._step_mutation_library(run, output_root, stages),
            self._step_candidate_filter(run, output_root, stages),
            self._step_library_design(output_root, stages),
        ]

        status = self._overall_status(steps)
        updated_candidates = [
            workflow_status.get("updated_at"),
            *[step.get("updated_at") for step in steps],
        ]
        updated_at = max((str(value) for value in updated_candidates if value), default="")
        if not updated_at:
            updated_at = _iso_mtime(run)

        result = {
            "project": project.name,
            "project_path": str(project),
            "run_id": run.name,
            "run_path": str(run),
            "workflow": workflow,
            "workflow_family": "ifld",
            "profile": profile,
            "task": task,
            "status": status,
            "updated_at": updated_at,
            "refresh_seconds": 5,
            "steps": steps,
        }
        result["runtime"] = self._runtime_metadata(
            run,
            status=status,
            updated_at=updated_at,
        )
        if (
            result["runtime"]["progress_source"] == "reported"
            and result["runtime"]["reported_status"]
        ):
            result["status"] = result["runtime"]["reported_status"]
        if include_artifacts:
            result["artifacts"] = self.list_artifacts(project_name, run_id)
        return result

    def list_artifacts(self, project_name: str, run_id: str) -> list[dict[str, Any]]:
        project = self._project_path(project_name)
        if self._is_partial_denovo(project):
            if run_id != "workspace":
                raise FileNotFoundError(run_id)
            return self._partial_artifacts(project)
        run = self._run_path(project, run_id)
        resolved = _read_json(
            _first_existing((run / "resolved_config.json", project / "resolved_config.json"))
        )
        candidate_filter = resolved.get("candidate_filter")
        candidate_filter = candidate_filter if isinstance(candidate_filter, dict) else {}
        task = str(
            resolved.get("task")
            or candidate_filter.get("task")
            or ""
        )
        configured = resolved.get("expected_outputs_by_step")
        if not isinstance(configured, dict):
            auto_advance = resolved.get("auto_advance")
            auto_advance = auto_advance if isinstance(auto_advance, dict) else {}
            configured = auto_advance.get("expected_outputs_by_step")
        expected_outputs = (
            configured if isinstance(configured, dict) else FALLBACK_EXPECTED_OUTPUTS
        )

        selected: dict[Path, tuple[str, str]] = {}
        for step_id in ARTIFACT_STEP_META:
            templates = expected_outputs.get(step_id)
            if not isinstance(templates, list):
                templates = FALLBACK_EXPECTED_OUTPUTS.get(step_id, [])
            for template in templates:
                if not isinstance(template, str) or template.startswith("manifest:"):
                    continue
                for path in self._resolve_expected_artifact(run, template, task):
                    selected.setdefault(path, (step_id, "expected_output"))
            for pattern in SUMMARY_OUTPUT_GLOBS.get(step_id, ()):
                try:
                    matches = sorted(run.glob(pattern))
                except OSError:
                    matches = []
                for path in matches:
                    if path.is_file():
                        selected.setdefault(path, (step_id, "summary"))

        # Some profiles declare individual step-5 files but write the complete
        # handoff package into a task-prefixed deliverables directory.  Always
        # expose that canonical package without scanning intermediate trees.
        for path in self._ifld_library_design_deliverables(run):
            selected.setdefault(path, ("05_library_design", "final_deliverable"))

        if not any(step_id == "03_mutation_library" for step_id, _ in selected.values()):
            reused_manifest = self._reused_mutation_library_manifest(run)
            if reused_manifest is not None:
                selected[reused_manifest] = ("03_mutation_library", "reused_output")

        records: list[dict[str, Any]] = []
        for path, (step_id, role) in selected.items():
            try:
                stat = path.stat()
            except OSError:
                continue
            try:
                relative = path.relative_to(run)
                source_run = run.name
            except ValueError:
                try:
                    relative = path.relative_to(project)
                except ValueError:
                    relative = path
                source_run = self._source_run_id(path)
            extension = path.suffix.lower()
            step_label, default_purpose = ARTIFACT_STEP_META[step_id]
            if role == "summary":
                purpose = "Step summary"
            elif role == "reused_output":
                purpose = "Reused next-step input"
            else:
                purpose = default_purpose
            records.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "relative_path": str(relative),
                    "size": stat.st_size,
                    "updated_at": datetime.fromtimestamp(
                        stat.st_mtime, timezone.utc
                    ).isoformat(),
                    "extension": extension.lstrip(".") or "file",
                    "previewable": extension in TEXT_EXTENSIONS
                    and stat.st_size <= 2_000_000,
                    "important": True,
                    "step_id": step_id,
                    "step_number": int(step_id[:2]),
                    "step_label": step_label,
                    "purpose": purpose,
                    "source_run": source_run,
                }
            )
        records.sort(
            key=lambda item: (
                item["step_number"],
                item["relative_path"].lower(),
            )
        )
        return records

    @staticmethod
    def _ifld_library_design_deliverables(run: Path) -> list[Path]:
        design_root = run / "outputs" / "output" / "library_design"
        try:
            directories = sorted(
                path
                for path in design_root.glob("*_library_design_deliverables")
                if path.is_dir()
            )
        except OSError:
            return []

        files: list[Path] = []
        try:
            resolved_run = run.resolve()
        except OSError:
            return []
        for directory in directories:
            try:
                candidates = sorted(directory.rglob("*"))
            except OSError:
                continue
            for path in candidates:
                try:
                    resolved = path.resolve()
                    resolved.relative_to(resolved_run)
                    is_file = path.is_file()
                except (OSError, ValueError):
                    continue
                if is_file:
                    files.append(path)
        return files

    @staticmethod
    def _is_partial_denovo(project: Path) -> bool:
        config_path = _first_existing(
            (
                project / "configs" / "nanobody_partial_library.json",
                project / "project_config.json",
            )
        )
        config = _read_json(config_path)
        return "partial_denovo_library_design" in str(config.get("workflow") or "")

    @staticmethod
    def _is_run_scoped_partial(run: Path) -> bool:
        config_path = run / "required_config.yaml"
        if not config_path.is_file():
            return False
        try:
            text = config_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        project_name = (
            run.parent.parent.name.lower()
            if run.parent.name == "runs"
            else run.name.lower()
        )
        return (
            "partial_denovo_library_design" in text
            and (
                "partial" in project_name
                or "workflow: denovo_design.boltzgen.partial" in text
            )
        )

    def _run_scoped_partial_paths(self, project: Path) -> list[Path]:
        return [
            path
            for path in self._run_paths(project)
            if self._is_run_scoped_partial(path)
        ]

    @staticmethod
    def _is_ifld_run(project: Path, run: Path) -> bool:
        project_input = _read_json(
            _first_existing((run / "project_input.json", project / "project_input.json"))
        )
        resolved = _read_json(
            _first_existing((run / "resolved_config.json", project / "resolved_config.json"))
        )
        lock = _read_json(
            _first_existing((run / "profile.lock.json", project / "profile.lock.json"))
        )
        values = (
            project.name,
            project_input.get("profile"),
            project_input.get("workflow"),
            resolved.get("aurapilot_profile"),
            resolved.get("workflow"),
            lock.get("profile"),
            lock.get("workflow"),
        )
        normalized = " ".join(str(value or "").lower() for value in values)
        return (
            "ifld" in normalized
            or "inverse_folding_and_library_design" in normalized
        )

    def _get_run_scoped_partial_status(
        self,
        project: Path,
        run: Path,
        *,
        include_artifacts: bool,
    ) -> dict[str, Any]:
        config_path = run / "required_config.yaml"
        config = _read_simple_yaml(config_path)
        target = config.get("target")
        target = target if isinstance(target, dict) else {}
        scaffold = config.get("scaffold")
        scaffold = scaffold if isinstance(scaffold, dict) else {}
        framework = scaffold.get("framework")
        framework = framework if isinstance(framework, dict) else {}
        partial = scaffold.get("partial_scaffold")
        partial = partial if isinstance(partial, dict) else {}
        launch = config.get("remote_launch")
        launch = launch if isinstance(launch, dict) else {}
        library = config.get("partial_denovo_library_design")
        library = library if isinstance(library, dict) else {}

        steps = [
            self._run_partial_initialize(run, config_path),
            self._run_partial_target_preparation(run),
            self._run_partial_scaffolds(run, partial),
            self._run_partial_generation(run, launch, library),
            self._run_partial_downstream(run, config),
            self._run_partial_final_library(run, library),
        ]
        updated_at = max(
            (
                value
                for value in (
                    _iso_mtime(config_path),
                    *[step.get("updated_at") for step in steps],
                )
                if value
            ),
            default=_iso_mtime(run),
        )
        result = {
            "project": project.name,
            "project_path": str(project),
            "run_id": run.name,
            "run_path": str(run),
            "workflow": str(config.get("workflow") or "denovo_design.boltzgen"),
            "workflow_family": "partial_denovo",
            "profile": str(config.get("server_profile") or "huoshan"),
            "task": " · ".join(
                value
                for value in (
                    str(target.get("target") or ""),
                    str(framework.get("name") or ""),
                )
                if value
            )
            or project.name,
            "status": self._overall_status(steps),
            "updated_at": updated_at,
            "refresh_seconds": 5,
            "steps": steps,
        }
        result["runtime"] = self._runtime_metadata(
            run,
            status=result["status"],
            updated_at=updated_at,
        )
        if (
            result["runtime"]["progress_source"] == "reported"
            and result["runtime"]["reported_status"]
        ):
            result["status"] = result["runtime"]["reported_status"]
        if include_artifacts:
            result["artifacts"] = self._run_scoped_partial_artifacts(run)
        return result

    def _run_partial_initialize(
        self,
        run: Path,
        config_path: Path,
    ) -> dict[str, Any]:
        step = self._partial_step_base("00_initialize")
        if config_path.is_file():
            step["status"] = "complete"
            step["summary"] = "Run configuration loaded and validated"
            step["progress"] = {"completed": 1, "total": 1, "fraction": 1.0}
            step["evidence"] = [str(config_path)]
            step["updated_at"] = _iso_mtime(config_path)
        else:
            step["summary"] = "等待 required_config.yaml"
        return step

    def _run_partial_target_preparation(self, run: Path) -> dict[str, Any]:
        step = self._partial_step_base("01_target_preparation")
        root = run / "steps" / "02_structure_prediction"
        antigen_path = root / "antigen_structure_manifest.json"
        framework_path = root / "vhh_structure_manifest.json"
        qc_path = root / "structure_qc_manifest.json"
        target_path = root / "target_prep_manifest.json"
        antigen = _read_json(antigen_path)
        framework = _read_json(framework_path)
        qc = _read_json(qc_path)
        target = _read_json(target_path)
        evidence = [
            path
            for path in (antigen_path, framework_path, qc_path, target_path)
            if path.is_file()
        ]
        complete = (
            _normalize_status(antigen.get("status")) == "complete"
            and _normalize_status(framework.get("status")) == "complete"
            and str(qc.get("status") or "").lower() in {"passed", "complete", "completed"}
        )
        if complete:
            step["status"] = "complete"
            step["summary"] = "Antigen and VHH structures passed QC"
        elif evidence:
            step["status"] = "running"
            step["summary"] = "Preparing antigen and VHH structures"
        else:
            step["summary"] = "等待 target and framework preparation"
        step["error"] = _error_message(antigen, framework, qc, target)
        step["progress"] = {
            "completed": 4 if complete else len(evidence),
            "total": 4,
            "fraction": 1.0 if complete else min(1.0, len(evidence) / 4),
        }
        step["evidence"] = [str(path) for path in evidence]
        step["updated_at"] = max((_iso_mtime(path) for path in evidence), default="")
        return step

    def _run_partial_scaffolds(
        self,
        run: Path,
        partial: dict[str, Any],
    ) -> dict[str, Any]:
        step = self._partial_step_base("02_partial_scaffolds")
        prep_path = run / "steps" / "02_structure_prediction" / "target_prep_manifest.json"
        qc_path = run / "steps" / "02_structure_prediction" / "structure_qc_manifest.json"
        prep = _read_json(prep_path)
        qc = _read_json(qc_path)
        qc_partial = qc.get("partial_scaffolds")
        qc_partial = qc_partial if isinstance(qc_partial, dict) else {}
        expected = int(partial.get("count") or 0)
        observed = int(
            prep.get("scaffold_yaml_count")
            or qc_partial.get("yaml_count")
            or 0
        )
        total = expected or observed or 1
        if observed and (not expected or observed >= expected):
            step["status"] = "complete"
            step["summary"] = f"{observed:,} partial scaffolds ready"
        elif observed:
            step["status"] = "running"
            step["summary"] = f"{observed:,} / {total:,} partial scaffolds ready"
        else:
            step["summary"] = "等待 partial scaffold library"
        step["progress"] = {
            "completed": min(observed, total),
            "total": total,
            "fraction": min(1.0, observed / total) if total else 0.0,
        }
        evidence = [path for path in (prep_path, qc_path) if path.is_file()]
        step["evidence"] = [str(path) for path in evidence]
        step["updated_at"] = max((_iso_mtime(path) for path in evidence), default="")
        return step

    def _run_partial_generation(
        self,
        run: Path,
        launch: dict[str, Any],
        library: dict[str, Any],
    ) -> dict[str, Any]:
        step = self._partial_step_base("03_boltzgen_generation")
        output_root = run / "outputs"
        workbench_root = output_root / "boltzgen" / "workbench"
        workbenches = self._boltzgen_workbenches(workbench_root)
        per_node = int(
            launch.get("num_designs")
            or library.get("num_designs_per_node")
            or 0
        )
        expected_nodes = int(
            launch.get("node_count")
            or library.get("node_count")
            or len(workbenches)
            or 1
        )
        expected_total = int(
            launch.get("num_designs_total")
            or library.get("num_designs_total")
            or per_node * expected_nodes
            or 0
        )
        if not per_node and expected_total:
            per_node = max(1, expected_total // expected_nodes)
        units = []
        for workbench in workbenches:
            unit_id = self._partial_unit_id(workbench.name)
            substeps = self._boltzgen_substeps(
                output_root,
                workbench,
                unit_id=unit_id,
                target=per_node or 1,
                cancelled=False,
            )
            error, log_path = self._run_partial_log_error(output_root, unit_id)
            if substeps and all(stage["status"] == "complete" for stage in substeps):
                status = "complete"
                error = ""
            elif error:
                status = "failed"
            elif any(stage["status"] == "running" for stage in substeps):
                status = "running"
            else:
                status = "pending"
            evidence = log_path or workbench
            updated_at = (
                self._boltzgen_activity_cache.get(str(workbench))
                or _iso_mtime(evidence)
            )
            unit = self._partial_unit(
                unit_id,
                status,
                host=unit_id,
                updated_at=updated_at,
            )
            unit["substeps"] = substeps
            unit["error"] = error
            unit["current_stage"] = next(
                (
                    stage["title"]
                    for stage in substeps
                    if stage["status"] in {"running", "failed"}
                ),
                next(
                    (
                        stage["title"]
                        for stage in substeps
                        if stage["status"] != "complete"
                    ),
                    substeps[-1]["title"] if substeps else "",
                ),
            )
            active_stage = next(
                (
                    stage
                    for stage in substeps
                    if stage["status"] in {"running", "failed"}
                ),
                next(
                    (
                        stage
                        for stage in substeps
                        if stage["status"] != "complete"
                    ),
                    substeps[-1] if substeps else None,
                ),
            )
            unit["progress"] = dict((active_stage or {}).get("progress") or {})
            units.append(unit)

        step["units"] = units
        step["stage_totals"] = self._aggregate_boltzgen_stages(units)
        failed = sum(unit["status"] == "failed" for unit in units)
        complete = sum(unit["status"] == "complete" for unit in units)
        design_stage = next(
            (stage for stage in step["stage_totals"] if stage["id"] == "design"),
            None,
        )
        completed_designs = int(
            (design_stage or {}).get("progress", {}).get("completed") or 0
        )
        total_designs = expected_total or int(
            (design_stage or {}).get("progress", {}).get("total") or 0
        )
        if failed:
            step["status"] = "failed"
            step["summary"] = (
                f"{completed_designs:,} / {total_designs:,} designs · "
                f"{failed} node failed"
            )
            step["error"] = next(
                (unit["error"] for unit in units if unit["error"]),
                "Inspect the failed BoltzGen node log.",
            )
        elif units and complete == len(units) and len(units) >= expected_nodes:
            step["status"] = "complete"
            step["summary"] = f"{total_designs:,} designs · {complete} nodes complete"
        elif units:
            step["status"] = "running"
            step["summary"] = (
                f"{completed_designs:,} / {total_designs:,} designs · "
                f"{complete} / {expected_nodes} nodes complete"
            )
        else:
            step["summary"] = "等待 BoltzGen generation"
        step["progress"] = {
            "completed": min(completed_designs, total_designs),
            "total": total_designs or 1,
            "failed": failed,
            "fraction": (
                min(1.0, completed_designs / total_designs)
                if total_designs
                else 0.0
            ),
        }
        step["updated_at"] = max(
            (unit.get("updated_at") or "" for unit in units),
            default="",
        )
        return step

    def _run_partial_downstream(
        self,
        run: Path,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        step = self._partial_step_base("04_downstream_screening")
        step["detail_kind"] = "downstream"
        step_root = run / "steps" / "05_developability_af3"
        nodes_root = run / "steps" / "05_developability_af3" / "nodes"
        try:
            node_dirs = sorted(path for path in nodes_root.iterdir() if path.is_dir())
        except OSError:
            node_dirs = []
        downstream_config = _read_json(step_root / "downstream_config.json")
        configured_nodes = downstream_config.get("nodes")
        configured_nodes = configured_nodes if isinstance(configured_nodes, list) else []
        node_ids = [path.name for path in node_dirs]
        for configured_node in configured_nodes:
            node_id = self._partial_unit_id(str(configured_node))
            if node_id and node_id not in node_ids:
                node_ids.append(node_id)
        node_ids.sort()

        target_config = config.get("target")
        target_config = target_config if isinstance(target_config, dict) else {}
        target_name = str(target_config.get("target") or "")
        library_config = downstream_config.get("library")
        library_config = library_config if isinstance(library_config, dict) else {}
        configured_per_node = int(library_config.get("per_node_budget") or 0)

        units = []
        for node_id in node_ids:
            node_dir = nodes_root / node_id
            logs = sorted(
                node_dir.glob("*.log"),
                key=lambda path: path.stat().st_mtime if path.is_file() else 0,
            )
            log_path = logs[-1] if logs else None
            text = self._tail_text(log_path, max_bytes=256_000) if log_path else ""
            error = self._log_error_summary(text)
            log_mtime = self._path_mtime(log_path)
            runtime_path, runtime_mtime = self._run_partial_runtime_evidence(
                run,
                node_id,
            )
            restarted_after_log = runtime_mtime > log_mtime
            output_base = self._run_partial_downstream_output_base(
                run,
                node_id,
                target_name,
            )
            substeps, shards = self._run_partial_downstream_substeps(
                output_base,
                node_id=node_id,
                expected_designs=configured_per_node,
            )
            node_manifest_path = node_dir / "node_manifest.json"
            node_manifest = _read_json(node_manifest_path)
            node_complete = _normalize_status(node_manifest.get("status")) == "complete"
            current_shard_error = next(
                (shard["error"] for shard in shards if shard.get("error")),
                "",
            )
            if current_shard_error:
                error = current_shard_error
            if (error and not restarted_after_log) or current_shard_error:
                status = "failed"
            elif node_complete:
                status = "complete"
                error = ""
            elif restarted_after_log or any(
                stage["status"] in {"running", "complete"}
                for stage in substeps
            ):
                status = "running"
                error = ""
            else:
                status = "pending"
                error = ""
            updated_candidates = [
                path
                for path in (
                    runtime_path,
                    log_path,
                    node_manifest_path if node_manifest_path.is_file() else None,
                    output_base if output_base.is_dir() else None,
                )
                if path is not None
            ]
            updated_path = max(
                updated_candidates,
                key=self._path_mtime,
                default=None,
            )
            unit = self._partial_unit(
                node_id,
                status,
                host=node_id,
                updated_at=_iso_mtime(updated_path),
            )
            unit["detail_kind"] = "downstream"
            unit["substeps"] = substeps
            unit["shards"] = shards
            unit["current_stage"] = next(
                (
                    stage["title"]
                    for stage in reversed(substeps)
                    if stage["status"] in {"failed", "running"}
                ),
                next(
                    (
                        stage["title"]
                        for stage in substeps
                        if stage["status"] == "pending"
                    ),
                    "Node result complete" if node_complete else status,
                ),
            )
            unit["error"] = error
            unit["log_path"] = str(log_path) if log_path else ""
            unit["runtime_evidence"] = str(runtime_path) if runtime_path else ""
            units.append(unit)
        step["units"] = units
        step["stage_totals"] = self._aggregate_partial_downstream_stages(units)
        cross_node_stage = self._run_partial_cross_node_stage(run, units)
        step["stage_totals"].append(cross_node_stage)
        failed = sum(unit["status"] == "failed" for unit in units)
        complete = sum(unit["status"] == "complete" for unit in units)
        running = sum(unit["status"] == "running" for unit in units)
        if failed:
            step["status"] = "failed"
            step["summary"] = f"{failed} downstream node failed"
            step["error"] = next(
                (unit["error"] for unit in units if unit["error"]),
                "Inspect the failed downstream node log.",
            )
        elif (
            units
            and complete == len(units)
            and cross_node_stage["status"] == "complete"
        ):
            step["status"] = "complete"
            step["summary"] = f"{complete} / {len(units)} downstream nodes complete"
        elif running or complete:
            step["status"] = "running"
            af3_stage = next(
                (
                    stage
                    for stage in step["stage_totals"]
                    if stage["id"] == "af3_refolding"
                ),
                {},
            )
            af3_progress = af3_stage.get("progress")
            af3_progress = af3_progress if isinstance(af3_progress, dict) else {}
            step["summary"] = (
                f"{complete} / {len(units)} nodes complete · "
                f"{int(af3_progress.get('completed') or 0):,} / "
                f"{int(af3_progress.get('total') or 0):,} AF3 designs"
            )
        else:
            step["summary"] = "等待 downstream screening"
        step["progress"] = {
            "completed": complete,
            "total": len(units) or 1,
            "failed": failed,
            "fraction": complete / len(units) if units else 0.0,
        }
        step["updated_at"] = max(
            (unit["updated_at"] for unit in units if unit["updated_at"]),
            default="",
        )
        return step

    def _run_partial_downstream_output_base(
        self,
        run: Path,
        node_id: str,
        target_name: str,
    ) -> Path:
        design_root = run / "outputs" / "output" / "design"
        try:
            matches = sorted(
                path
                for path in design_root.glob(f"*/boltzgen_{node_id}")
                if path.is_dir()
            )
        except OSError:
            matches = []
        if matches:
            return matches[0]
        target = target_name or "target"
        return design_root / target / f"boltzgen_{node_id}"

    def _run_partial_downstream_substeps(
        self,
        output_base: Path,
        *,
        node_id: str,
        expected_designs: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        design_path = output_base / "all_designs.csv"
        tap_path = output_base / "developability_tap.csv"
        cpu_path = output_base / "developability_biophi_solubility.csv"
        merged_path = output_base / "developability.csv"
        af3_base = output_base / "af3"
        node_manifest_path = (
            output_base.parents[4]
            / "steps"
            / "05_developability_af3"
            / "nodes"
            / node_id
            / "node_manifest.json"
            if len(output_base.parents) > 4
            else Path()
        )

        design_rows = self._csv_data_rows(design_path)
        total_designs = expected_designs or design_rows or 1
        tap_summary = self._csv_pass_summary(tap_path, ("TAP_filter",))
        cpu_summary = self._csv_pass_summary(
            cpu_path,
            (
                "PI_filter",
                "BioPhi_filter",
                "humanness_filter",
                "liability_filter",
                "solubility_filter",
            ),
        )
        merged_summary = self._csv_pass_summary(
            merged_path,
            ("all_filter_pass",),
        )
        tap_rows = int(tap_summary.get("rows") or 0)
        cpu_rows = int(cpu_summary.get("rows") or 0)
        merged_rows = int(merged_summary.get("rows") or 0)
        developability_pass = int(
            merged_summary.get("all_filter_pass") or 0
        )
        developability_filtered = max(merged_rows - developability_pass, 0)
        solubility_not_run = int(
            cpu_summary.get("solubility_filter__not_run") or 0
        )

        shards = self._run_partial_af3_shards(
            af3_base,
            expected_total=developability_pass,
        )
        shard_total = sum(int(shard["progress"].get("total") or 0) for shard in shards)
        input_ready = sum(int(shard.get("input_ready") or 0) for shard in shards)
        msa_ready = sum(int(shard.get("msa_ready") or 0) for shard in shards)
        refolded = sum(int(shard.get("refolded") or 0) for shard in shards)
        analyzed = sum(int(shard.get("analyzed") or 0) for shard in shards)
        af3_total = developability_pass or shard_total or 1
        current_shard_errors = [
            str(shard.get("error") or "")
            for shard in shards
            if shard.get("error")
        ]

        def pass_metric(
            summary: dict[str, int],
            *columns: str,
        ) -> int:
            return max((int(summary.get(column) or 0) for column in columns), default=0)

        substeps = [
            self._downstream_stage(
                "input_validation",
                "Input validation",
                design_rows,
                total_designs,
            ),
            self._downstream_stage(
                "structure_prediction",
                "Structure prediction",
                tap_rows,
                total_designs,
            ),
            self._downstream_stage(
                "tap_analysis",
                "TAP analysis",
                tap_rows,
                total_designs,
                metrics=[
                    {
                        "label": "passed",
                        "value": int(tap_summary.get("TAP_filter") or 0),
                        "tone": "success",
                    }
                ],
            ),
            self._downstream_stage(
                "pi_analysis",
                "pI analysis",
                cpu_rows,
                total_designs,
                metrics=[
                    {
                        "label": "passed",
                        "value": int(cpu_summary.get("PI_filter") or 0),
                        "tone": "success",
                    }
                ],
            ),
            self._downstream_stage(
                "biophi_humanness",
                "BioPhi / Humanness",
                cpu_rows,
                total_designs,
                metrics=[
                    {
                        "label": "passed",
                        "value": pass_metric(
                            cpu_summary,
                            "BioPhi_filter",
                            "humanness_filter",
                        ),
                        "tone": "success",
                    }
                ],
            ),
            self._downstream_stage(
                "liability_analysis",
                "Liability analysis",
                cpu_rows,
                total_designs,
                metrics=[
                    {
                        "label": "passed",
                        "value": int(cpu_summary.get("liability_filter") or 0),
                        "tone": "success",
                    }
                ],
            ),
            self._downstream_stage(
                "solubility_analysis",
                "Solubility analysis",
                0 if solubility_not_run >= cpu_rows and cpu_rows else cpu_rows,
                total_designs,
                status=(
                    "skipped"
                    if solubility_not_run >= cpu_rows and cpu_rows
                    else ""
                ),
                metrics=[
                    {
                        "label": (
                            "not run"
                            if solubility_not_run >= cpu_rows and cpu_rows
                            else "passed"
                        ),
                        "value": (
                            solubility_not_run
                            if solubility_not_run >= cpu_rows and cpu_rows
                            else int(cpu_summary.get("solubility_filter") or 0)
                        ),
                        "tone": (
                            "muted"
                            if solubility_not_run >= cpu_rows and cpu_rows
                            else "success"
                        ),
                    }
                ],
            ),
            self._downstream_stage(
                "developability_filter",
                "Developability filter",
                merged_rows,
                total_designs,
                metrics=[
                    {
                        "label": "passed",
                        "value": developability_pass,
                        "tone": "success",
                    },
                    {
                        "label": "filtered",
                        "value": developability_filtered,
                        "tone": "muted",
                    },
                ],
            ),
            self._downstream_stage(
                "af3_sharding",
                "AF3 shard preparation",
                shard_total,
                af3_total,
                metrics=[
                    {"label": "GPU shards", "value": len(shards), "tone": "info"},
                    {"label": "inputs ready", "value": input_ready, "tone": "info"},
                ],
            ),
            self._downstream_stage(
                "msa_preparation",
                "AF3 input & MSA",
                msa_ready,
                af3_total,
            ),
            self._downstream_stage(
                "af3_refolding",
                "AF3 refolding",
                refolded,
                af3_total,
                status="failed" if current_shard_errors else "",
                error=current_shard_errors[0] if current_shard_errors else "",
            ),
            self._downstream_stage(
                "af3_analysis",
                "AF3 scoring & RMSD",
                analyzed,
                af3_total,
            ),
            self._downstream_stage(
                "node_aggregation",
                "Node result validation",
                1
                if _normalize_status(_read_json(node_manifest_path).get("status"))
                == "complete"
                else 0,
                1,
            ),
        ]
        return substeps, shards

    def _run_partial_af3_shards(
        self,
        af3_base: Path,
        *,
        expected_total: int,
    ) -> list[dict[str, Any]]:
        output_names = self._direct_dir_names(af3_base / "output")
        shards = []
        for gpu in range(8):
            shard_path = af3_base / "shards" / f"input_gpu{gpu}.csv"
            summary_path = af3_base / "shards" / f"af3_info_gpu{gpu}.csv"
            log_path = af3_base / "logs" / f"af3_gpu{gpu}.log"
            input_names = self._csv_first_column_values(shard_path)
            total = len(input_names)
            observed_input = len(
                input_names
                & self._direct_dir_names(af3_base / "input" / f"gpu{gpu}")
            )
            observed_msa = len(
                input_names
                & self._direct_dir_names(af3_base / "input_with_msa" / f"gpu{gpu}")
            )
            refolded = len(input_names & output_names)
            msa_ready = max(observed_msa, refolded)
            input_ready = max(observed_input, msa_ready)
            analyzed = min(self._csv_data_rows(summary_path), total)
            text = self._tail_text(log_path, max_bytes=128_000) if log_path.is_file() else ""
            error = self._log_error_summary(text)
            runtime_mtime = max(
                (
                    self._path_mtime(af3_base / "input" / f"gpu{gpu}"),
                    self._path_mtime(af3_base / "input_with_msa" / f"gpu{gpu}"),
                    self._path_mtime(summary_path),
                    self._path_mtime(af3_base / "output"),
                )
            )
            if total == 0:
                status = "skipped" if expected_total else "pending"
                error = ""
            elif analyzed >= total:
                status = "complete"
                error = ""
            elif error and runtime_mtime <= self._path_mtime(log_path):
                status = "failed"
            elif input_ready or msa_ready or refolded or runtime_mtime:
                status = "running"
                error = ""
            else:
                status = "pending"
                error = ""
            shard = self._partial_unit(
                f"gpu{gpu}",
                status,
                host="",
                updated_at=_iso_mtime(
                    max(
                        (
                            path
                            for path in (
                                log_path,
                                summary_path,
                                af3_base / "input_with_msa" / f"gpu{gpu}",
                                af3_base / "output",
                            )
                            if path.exists()
                        ),
                        key=self._path_mtime,
                        default=None,
                    )
                ),
                progress={
                    "completed": analyzed if analyzed else refolded,
                    "total": total,
                    "fraction": (
                        min(1.0, (analyzed if analyzed else refolded) / total)
                        if total
                        else 0.0
                    ),
                },
            )
            shard["title"] = f"GPU {gpu}"
            shard["gpu"] = gpu
            shard["input_ready"] = input_ready
            shard["msa_ready"] = msa_ready
            shard["refolded"] = refolded
            shard["analyzed"] = analyzed
            shard["error"] = error
            shard["log_path"] = str(log_path) if log_path.is_file() else ""
            shards.append(shard)
        return shards

    def _run_partial_cross_node_stage(
        self,
        run: Path,
        units: list[dict[str, Any]],
    ) -> dict[str, Any]:
        step_root = run / "steps" / "05_developability_af3"
        developability_path = step_root / "developability_all_nodes.csv"
        af3_path = step_root / "af3_info_all_nodes.csv"
        completed_files = sum(
            path.is_file() for path in (developability_path, af3_path)
        )
        all_nodes_complete = bool(units) and all(
            unit["status"] == "complete" for unit in units
        )
        status = ""
        if completed_files < 2 and (completed_files or all_nodes_complete):
            status = "running"
        return self._downstream_stage(
            "cross_node_aggregation",
            "Cross-node aggregation",
            completed_files,
            2,
            status=status,
            metrics=[
                {
                    "label": "developability rows",
                    "value": self._csv_data_rows(developability_path),
                    "tone": "info",
                },
                {
                    "label": "AF3 rows",
                    "value": self._csv_data_rows(af3_path),
                    "tone": "info",
                },
            ],
        )

    @staticmethod
    def _downstream_stage(
        stage_id: str,
        title: str,
        completed: int,
        total: int,
        *,
        status: str = "",
        error: str = "",
        metrics: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        total = max(int(total), 1)
        completed = min(max(int(completed), 0), total)
        if status:
            normalized = status
        elif completed >= total:
            normalized = "complete"
        elif completed:
            normalized = "running"
        else:
            normalized = "pending"
        return {
            "id": stage_id,
            "title": title,
            "status": normalized,
            "progress": {
                "completed": completed,
                "total": total,
                "fraction": min(1.0, completed / total),
            },
            "metrics": metrics or [],
            "error": error,
        }

    @staticmethod
    def _aggregate_partial_downstream_stages(
        units: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not units:
            return []
        definitions = [
            (stage["id"], stage["title"])
            for stage in units[0].get("substeps", [])
        ]
        aggregates = []
        for stage_id, title in definitions:
            stages = [
                stage
                for unit in units
                for stage in unit.get("substeps", [])
                if stage.get("id") == stage_id
            ]
            if not stages:
                continue
            completed = sum(int(stage["progress"].get("completed") or 0) for stage in stages)
            total = sum(int(stage["progress"].get("total") or 0) for stage in stages)
            statuses = {stage.get("status") for stage in stages}
            if "failed" in statuses:
                status = "failed"
            elif statuses == {"complete"}:
                status = "complete"
            elif "running" in statuses or "complete" in statuses:
                status = "running"
            elif statuses == {"skipped"}:
                status = "skipped"
            else:
                status = "pending"
            metric_totals: dict[tuple[str, str], int] = {}
            for stage in stages:
                for metric in stage.get("metrics", []):
                    key = (
                        str(metric.get("label") or ""),
                        str(metric.get("tone") or ""),
                    )
                    metric_totals[key] = metric_totals.get(key, 0) + int(
                        metric.get("value") or 0
                    )
            aggregates.append(
                {
                    "id": stage_id,
                    "title": title,
                    "status": status,
                    "progress": {
                        "completed": completed,
                        "total": total,
                        "fraction": min(1.0, completed / total) if total else 0.0,
                    },
                    "metrics": [
                        {"label": key[0], "tone": key[1], "value": value}
                        for key, value in metric_totals.items()
                        if key[0]
                    ],
                }
            )
        return aggregates

    @staticmethod
    def _path_mtime(path: Path | None) -> float:
        if path is None:
            return 0.0
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _path_size(path: Path | None) -> int:
        if path is None:
            return 0
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _run_partial_runtime_evidence(
        self,
        run: Path,
        node_id: str,
    ) -> tuple[Path | None, float]:
        """Return bounded AF3 activity evidence for one downstream node.

        A resumed run can leave an earlier error in ``node_fc_resume.log`` while
        new AF3 jobs continue creating input and output directories.  Only known
        shallow workflow directories are inspected so the dashboard does not
        recursively scan large result trees on every refresh.
        """
        design_root = run / "outputs" / "output" / "design"
        try:
            af3_roots = sorted(
                path
                for path in design_root.glob(f"*/boltzgen_{node_id}/af3")
                if path.is_dir()
            )
        except OSError:
            af3_roots = []

        candidates: list[Path] = []
        for af3_root in af3_roots:
            candidates.extend(
                (
                    af3_root,
                    af3_root / "input",
                    af3_root / "input_with_msa",
                    af3_root / "output",
                )
            )
            for shard_root in (
                af3_root / "input",
                af3_root / "input_with_msa",
            ):
                try:
                    candidates.extend(
                        path for path in shard_root.glob("gpu*") if path.is_dir()
                    )
                except OSError:
                    continue

        latest_path: Path | None = None
        latest_mtime = 0.0
        for path in candidates:
            mtime = self._path_mtime(path)
            if mtime > latest_mtime:
                latest_path = path
                latest_mtime = mtime
        return latest_path, latest_mtime

    def _run_partial_final_library(
        self,
        run: Path,
        library: dict[str, Any],
    ) -> dict[str, Any]:
        step = self._partial_step_base("05_final_library")
        target = int(library.get("library_size") or 12000)
        final_candidates = (
            run / "outputs" / "output" / "library",
            run / "outputs" / "output" / "final_library",
            run / "steps" / "06_partial_library_design",
        )
        deliverables: list[Path] = []
        for directory in final_candidates:
            if not directory.is_dir():
                continue
            for pattern in ("*.csv", "*.xlsx", "*.json", "*.tar.gz"):
                try:
                    deliverables.extend(path for path in directory.glob(pattern) if path.is_file())
                except OSError:
                    continue
        if deliverables:
            step["status"] = "complete"
            step["summary"] = f"Final {target:,}-sequence library generated"
            step["progress"] = {"completed": target, "total": target, "fraction": 1.0}
        elif library.get("enabled") is False:
            step["summary"] = f"Final {target:,}-sequence library is not enabled"
            step["progress"] = {"completed": 0, "total": target, "fraction": 0.0}
        else:
            step["summary"] = f"等待 final {target:,}-sequence library"
            step["progress"] = {"completed": 0, "total": target, "fraction": 0.0}
        step["evidence"] = [str(path) for path in deliverables]
        step["updated_at"] = max((_iso_mtime(path) for path in deliverables), default="")
        return step

    def _run_partial_log_error(
        self,
        output_root: Path,
        unit_id: str,
    ) -> tuple[str, Path | None]:
        try:
            candidates = sorted(
                (output_root / "logs").glob(f"**/run_*_{unit_id}.log"),
                key=lambda path: path.stat().st_mtime if path.is_file() else 0,
            )
        except OSError:
            candidates = []
        path = candidates[-1] if candidates else None
        text = self._tail_text(path, max_bytes=384_000) if path else ""
        return self._log_error_summary(text), path

    @staticmethod
    def _log_error_summary(text: str) -> str:
        if not re.search(r"(Traceback|RuntimeError|Exception|ERROR\b)", text):
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return next(
            (
                line
                for line in reversed(lines)
                if re.search(r"(RuntimeError|Exception|Error:|ERROR\b)", line)
            ),
            lines[-1] if lines else "Unknown log error",
        )[:1000]

    def _run_scoped_partial_artifacts(self, run: Path) -> list[dict[str, Any]]:
        selected: dict[Path, tuple[str, str]] = {}
        patterns = {
            "01_target_preparation": (
                "steps/02_structure_prediction/target_prep_manifest.json",
                "steps/02_structure_prediction/structure_qc_manifest.json",
                "outputs/input/denovo_info.csv",
            ),
            "02_partial_scaffolds": (
                "outputs/boltzgen/fab_targets/*.yaml",
            ),
            "03_boltzgen_generation": (
                "steps/04_boltzgen_aggregation/boltzgen_aggregation_manifest.json",
                "steps/04_boltzgen_aggregation/all_designs_*.csv",
                "steps/04_boltzgen_aggregation/all_designs_*.fasta",
                "steps/04_boltzgen_aggregation/boltzgen_qc_report.md",
            ),
        }
        for step_id, step_patterns in patterns.items():
            for pattern in step_patterns:
                try:
                    matches = sorted(run.glob(pattern))
                except OSError:
                    matches = []
                for path in matches:
                    if path.is_file():
                        selected.setdefault(path, (step_id, "Next-step input"))
        labels = {
            definition[0]: f"Step {int(definition[0][:2])}"
            for definition in PARTIAL_STEP_DEFINITIONS
        }
        records = []
        for path, (step_id, purpose) in selected.items():
            record = self._artifact_record(
                path,
                run,
                step_id=step_id,
                step_label=labels[step_id],
                purpose=purpose,
                source_run=run.name,
            )
            if record:
                records.append(record)
        known_paths = {record["path"] for record in records}
        for record in self._run_scoped_manifest_artifacts(run):
            if record["path"] not in known_paths:
                records.append(record)
                known_paths.add(record["path"])
        records.sort(key=lambda item: (item["step_number"], item["relative_path"].lower()))
        return records

    def _run_scoped_manifest_artifacts(self, run: Path) -> list[dict[str, Any]]:
        manifest_path = (
            run
            / "steps"
            / "06_partial_library"
            / "full_downstream_manifest.json"
        )
        manifest = _read_json(manifest_path)
        outputs = manifest.get("outputs")
        if not manifest_path.is_file() or not isinstance(outputs, dict):
            return []

        hashes = manifest.get("sha256")
        hashes = hashes if isinstance(hashes, dict) else {}
        entries: list[tuple[str, Path, bool]] = [
            ("full_downstream_manifest", manifest_path, False)
        ]
        for key, value in outputs.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            try:
                path = _safe_resolve(run, value)
            except (OSError, ValueError):
                continue
            entries.append((key, path, True))

        records: list[dict[str, Any]] = []
        seen: set[Path] = set()
        for key, path, listed_by_manifest in entries:
            if path in seen:
                continue
            seen.add(path)
            record = self._artifact_record(
                path,
                run,
                step_id="05_final_library",
                step_label="Step 6",
                purpose="Final result",
                source_run=run.name,
            )
            sha256 = hashes.get(path.name)
            metadata = {
                "artifact_source": "canonical_manifest",
                "listed_by_manifest": listed_by_manifest,
                "manifest_key": key,
                "manifest_path": str(manifest_path),
                "sha256": sha256 if isinstance(sha256, str) else "",
                "is_manifest": "manifest" in path.name.lower(),
            }
            if record is None:
                try:
                    relative = path.relative_to(run)
                except ValueError:
                    continue
                extension = path.suffix.lower()
                record = {
                    "name": path.name,
                    "path": str(path),
                    "relative_path": str(relative),
                    "size": None,
                    "updated_at": "",
                    "extension": extension.lstrip(".") or "file",
                    "previewable": False,
                    "important": True,
                    "step_id": "05_final_library",
                    "step_number": 6,
                    "step_label": "Step 6",
                    "purpose": "Final result",
                    "source_run": run.name,
                    "exists": False,
                    "empty": False,
                    "missing": True,
                }
            else:
                record["step_number"] = 6
            record.update(metadata)
            records.append(record)
        return records

    def _get_partial_status(
        self,
        project: Path,
        *,
        include_artifacts: bool,
    ) -> dict[str, Any]:
        config_path = _first_existing(
            (
                project / "configs" / "nanobody_partial_library.json",
                project / "project_config.json",
            )
        )
        config = _read_json(config_path)
        plan_path = _first_existing(
            (project / "execution_plan.json", project / "downstream_runtime.json")
        )
        plan = _read_json(project / "execution_plan.json")
        downstream = _read_json(project / "downstream_runtime.json")
        cancelled = _normalize_status(plan.get("status")) == "cancelled"

        steps = [
            self._partial_initialize(project, config_path, config, plan),
            self._partial_target_preparation(project, config),
            self._partial_scaffolds(project, config, plan),
            self._partial_generation(project, config, plan, downstream, cancelled),
            self._partial_downstream(project, config, plan, downstream, cancelled),
            self._partial_final_library(project, config, downstream, cancelled),
        ]
        updated_at = max(
            (
                value
                for value in (
                    _iso_mtime(config_path),
                    _iso_mtime(plan_path),
                    downstream.get("recorded_at"),
                    *[step.get("updated_at") for step in steps],
                )
                if value
            ),
            default=_iso_mtime(project),
        )
        framework = config.get("framework")
        framework = framework if isinstance(framework, dict) else {}
        antigen = config.get("antigen")
        antigen = antigen if isinstance(antigen, dict) else {}
        task = " · ".join(
            value
            for value in (
                str(antigen.get("name") or ""),
                str(framework.get("name") or ""),
            )
            if value
        )
        result = {
            "project": project.name,
            "project_path": str(project),
            "run_id": "workspace",
            "run_path": str(project),
            "workflow": str(
                config.get("workflow")
                or "denovo_design.boltzgen.partial_denovo_library_design"
            ),
            "workflow_family": "partial_denovo",
            "profile": str(
                config.get("server_profile")
                or framework.get("name")
                or "partial de novo library"
            ),
            "task": task or project.name,
            "status": self._overall_status(steps),
            "updated_at": updated_at,
            "refresh_seconds": 5,
            "steps": steps,
        }
        result["runtime"] = self._runtime_metadata(
            project,
            status=result["status"],
            updated_at=updated_at,
        )
        if (
            result["runtime"]["progress_source"] == "reported"
            and result["runtime"]["reported_status"]
        ):
            result["status"] = result["runtime"]["reported_status"]
        if include_artifacts:
            result["artifacts"] = self._partial_artifacts(project)
        return result

    @staticmethod
    def _partial_step_base(step_id: str) -> dict[str, Any]:
        definition = next(item for item in PARTIAL_STEP_DEFINITIONS if item[0] == step_id)
        return {
            "id": definition[0],
            "number": int(definition[0][:2]),
            "title": definition[1],
            "description": definition[2],
            "status": "pending",
            "summary": "",
            "error": "",
            "progress": {},
            "units": [],
            "updated_at": "",
            "evidence": [],
        }

    def _partial_initialize(
        self,
        project: Path,
        config_path: Path | None,
        config: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        step = self._partial_step_base("00_initialize")
        input_path = project / "input" / "denovo_info.csv"
        present = [path for path in (config_path, input_path) if path and path.is_file()]
        preflight = plan.get("preflight")
        preflight = preflight if isinstance(preflight, dict) else {}
        failed_checks = [
            key
            for key, value in preflight.items()
            if "fail" in str(
                value.get("status") if isinstance(value, dict) else value
            ).lower()
        ]
        if failed_checks:
            step["status"] = "failed"
            step["summary"] = "执行前检查失败"
            step["error"] = "Failed checks: " + ", ".join(failed_checks)
        elif len(present) == 2:
            step["status"] = "complete"
            step["summary"] = "项目配置与输入已校验"
        else:
            step["summary"] = "等待项目配置与输入"
        step["progress"] = {
            "completed": len(present),
            "total": 2,
            "fraction": len(present) / 2,
        }
        step["evidence"] = [str(path) for path in present]
        step["updated_at"] = max((_iso_mtime(path) for path in present), default="")
        return step

    def _partial_target_preparation(
        self,
        project: Path,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        step = self._partial_step_base("01_target_preparation")
        structure_status_path = project / "target_prep" / "structure_prep_status.json"
        partial_manifest_path = project / "target_prep" / "nanobody_partial_library_manifest.json"
        target_manifests = sorted(project.glob("target_prep/*/target_manifest.json"))
        structure_status = _read_json(structure_status_path)
        partial_manifest = _read_json(partial_manifest_path)
        evidence = [
            path
            for path in (structure_status_path, partial_manifest_path, *target_manifests)
            if path.is_file()
        ]
        branches = config.get("branches")
        branches = branches if isinstance(branches, list) else []
        enabled = sum(
            1
            for branch in branches
            if isinstance(branch, dict) and branch.get("execution_enabled", True)
        )
        status = _normalize_status(structure_status.get("status"))
        if status == "failed":
            step["status"] = "failed"
            step["summary"] = "Target 或 framework 结构校验失败"
            step["error"] = _error_message(structure_status, partial_manifest)
        elif evidence:
            step["status"] = "complete"
            step["summary"] = (
                f"抗原与 VHH framework 已准备 · {enabled or len(branches) or 1} target branch"
            )
        else:
            step["summary"] = "等待抗原与 framework 准备"
        step["progress"] = {
            "completed": 1 if evidence else 0,
            "total": 1,
            "fraction": 1.0 if evidence else 0.0,
        }
        step["evidence"] = [str(path) for path in evidence]
        step["updated_at"] = max((_iso_mtime(path) for path in evidence), default="")
        return step

    def _partial_scaffolds(
        self,
        project: Path,
        config: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        step = self._partial_step_base("02_partial_scaffolds")
        manifests = sorted(project.glob("boltzgen/fab_scaffolds/*/manifest.csv"))
        partial = config.get("partial_scaffold")
        partial = partial if isinstance(partial, dict) else {}
        expected = int(partial.get("count") or 0)
        preflight = plan.get("preflight")
        preflight = preflight if isinstance(preflight, dict) else {}
        validation = preflight.get("partial_scaffold_validation")
        validation = validation if isinstance(validation, dict) else {}
        observed = int(
            validation.get("manifest_rows")
            or validation.get("yaml_count")
            or (expected if manifests else 0)
        )
        if manifests:
            step["status"] = "complete"
            step["summary"] = f"{observed or expected:,} partial scaffolds ready"
        else:
            step["summary"] = "等待 partial scaffold library"
        total = expected or observed or 1
        step["progress"] = {
            "completed": min(observed, total),
            "total": total,
            "fraction": min(1.0, observed / total) if total else 0.0,
        }
        step["evidence"] = [str(path) for path in manifests]
        step["updated_at"] = max((_iso_mtime(path) for path in manifests), default="")
        return step

    def _partial_generation(
        self,
        project: Path,
        config: dict[str, Any],
        plan: dict[str, Any],
        downstream: dict[str, Any],
        cancelled: bool,
    ) -> dict[str, Any]:
        step = self._partial_step_base("03_boltzgen_generation")
        workbenches = self._boltzgen_workbenches(
            project / "boltzgen" / "workbench"
        )
        execution = config.get("execution")
        execution = execution if isinstance(execution, dict) else {}
        configured_nodes = execution.get("full_generation_nodes")
        configured_nodes = configured_nodes if isinstance(configured_nodes, dict) else {}
        node_targets = self._partial_generation_targets(project, config, plan)
        unit_map: dict[str, dict[str, Any]] = {}
        runtime = plan.get("runtime")
        runtime = runtime if isinstance(runtime, dict) else {}
        termination_outputs = runtime.get("termination_outputs")
        termination_outputs = (
            termination_outputs if isinstance(termination_outputs, dict) else {}
        )
        termination_by_node = {
            str(key).replace("-", ""): value
            for key, value in termination_outputs.items()
            if isinstance(value, dict)
        }

        for workbench in workbenches:
            unit_id = self._partial_unit_id(workbench.name)
            target = int(node_targets.get(unit_id) or 0)
            final_metrics = self._boltzgen_final_metrics(workbench)
            substeps = self._boltzgen_substeps(
                project,
                workbench,
                unit_id=unit_id,
                target=target,
                cancelled=cancelled,
                design_override=int(
                    termination_by_node.get(unit_id, {}).get("cif_count") or 0
                ),
            )
            configured = str(
                configured_nodes.get(unit_id)
                or configured_nodes.get(unit_id.replace("-", ""))
                or ""
            )
            status = (
                "failed"
                if any(item["status"] == "failed" for item in substeps)
                else "complete"
                if substeps and all(item["status"] == "complete" for item in substeps)
                else "cancelled"
                if cancelled
                else _normalize_status(configured)
            )
            if status == "pending" and any(
                item["status"] in {"running", "complete"} for item in substeps
            ):
                status = "running"
            evidence = final_metrics or workbench
            updated_at = (
                self._boltzgen_activity_cache.get(str(workbench))
                or _iso_mtime(evidence)
            )
            unit = self._partial_unit(
                unit_id,
                status,
                host=unit_id,
                updated_at=updated_at,
            )
            unit["substeps"] = substeps
            unit["current_stage"] = next(
                (
                    item["title"]
                    for item in substeps
                    if item["status"] in {"running", "failed", "cancelled"}
                ),
                next(
                    (
                        item["title"]
                        for item in substeps
                        if item["status"] != "complete"
                    ),
                    substeps[-1]["title"] if substeps else "",
                ),
            )
            active_stage = next(
                (
                    item
                    for item in substeps
                    if item["status"] in {"running", "failed", "cancelled"}
                ),
                next(
                    (
                        item
                        for item in substeps
                        if item["status"] != "complete"
                    ),
                    substeps[-1] if substeps else None,
                ),
            )
            unit["progress"] = dict((active_stage or {}).get("progress") or {})
            unit_map[unit_id] = unit

        for unit_id, payload in termination_outputs.items():
            payload = payload if isinstance(payload, dict) else {}
            canonical_unit_id = str(unit_id).replace("-", "")
            generated = int(payload.get("cif_count") or 0)
            target = int(node_targets.get(canonical_unit_id) or generated or 1)
            synthetic = self._partial_unit(
                canonical_unit_id,
                "cancelled" if cancelled else "running",
                host=canonical_unit_id,
                progress={
                    "completed": generated,
                    "total": target,
                    "fraction": min(1.0, generated / target),
                },
            )
            synthetic["substeps"] = self._synthetic_cancelled_substeps(
                generated,
                target,
            )
            synthetic["current_stage"] = "Design"
            unit_map.setdefault(
                canonical_unit_id,
                synthetic,
            )

        state_status_path = None
        shards: dict[str, Any] = {}
        try:
            state_candidates = sorted(
                (project / "state" / "downstream").glob("*/status.json")
            )
        except OSError:
            state_candidates = []
        for candidate in state_candidates:
            payload = _read_json(candidate)
            candidate_shards = payload.get("shards")
            if isinstance(candidate_shards, dict):
                state_status_path = candidate
                shards = candidate_shards
                break
        for unit_id, payload in shards.items():
            payload = payload if isinstance(payload, dict) else {}
            final_metrics_value = payload.get("final_metrics")
            final_metrics = Path(str(final_metrics_value)) if final_metrics_value else None
            status = "complete" if payload.get("complete") else "running"
            unit_map.setdefault(
                unit_id,
                self._partial_unit(
                    unit_id,
                    status,
                    host=unit_id.split("_", 1)[0],
                    updated_at=_iso_mtime(final_metrics),
                ),
            )

        units = list(unit_map.values())
        step["units"] = units
        step["stage_totals"] = self._aggregate_boltzgen_stages(units)
        failed = sum(unit["status"] == "failed" for unit in units)
        completed = sum(unit["status"] == "complete" for unit in units)
        generation = config.get("generation")
        generation = generation if isinstance(generation, dict) else {}
        expected_nodes = int(generation.get("node_count") or 0)
        if not expected_nodes:
            nodes = plan.get("nodes")
            expected_nodes = len(nodes) if isinstance(nodes, list) else len(units)
        expected_nodes = max(expected_nodes, len(units), 1)

        if cancelled:
            step["status"] = "cancelled"
            generated = sum(
                int(payload.get("cif_count") or 0)
                for payload in termination_outputs.values()
                if isinstance(payload, dict)
            )
            target = int(
                (plan.get("final_library") or {}).get("total_boltzgen_designs") or 0
            )
            step["summary"] = (
                f"用户终止 · 已生成 {generated:,}"
                + (f" / {target:,} designs" if target else " designs")
            )
            if target:
                step["progress"] = {
                    "completed": generated,
                    "total": target,
                    "fraction": min(1.0, generated / target),
                }
        elif failed:
            step["status"] = "failed"
            step["summary"] = f"{failed} generation shard failed"
            step["error"] = "Inspect the failed generation shard log."
        elif units and completed == len(units) and len(units) >= expected_nodes:
            step["status"] = "complete"
            step["summary"] = f"{completed} / {expected_nodes} generation shards complete"
        elif units or "submitted" in str(plan.get("status") or ""):
            step["status"] = "running"
            step["summary"] = f"{completed} / {expected_nodes} generation shards complete"
        else:
            step["summary"] = "等待 BoltzGen generation"
        if not step["progress"]:
            step["progress"] = {
                "completed": completed,
                "total": expected_nodes,
                "failed": failed,
                "fraction": min(1.0, completed / expected_nodes),
            }
        step["updated_at"] = max(
            (
                value
                for value in (
                    _iso_mtime(project / "execution_plan.json"),
                    _iso_mtime(state_status_path),
                    *[
                        str(unit.get("updated_at") or "")
                        for unit in units
                    ],
                )
                if value
            ),
            default="",
        )
        return step

    def _boltzgen_workbenches(self, root: Path) -> list[Path]:
        cache_key = str(root)
        cached = self._boltzgen_workbench_cache.get(cache_key, [])
        try:
            workbenches = sorted(
                path for path in root.iterdir() if path.is_dir()
            )
        except OSError:
            return list(cached)
        if workbenches:
            self._boltzgen_workbench_cache[cache_key] = workbenches
            return workbenches
        return list(cached)

    def _partial_generation_targets(
        self,
        project: Path,
        config: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, int]:
        targets: dict[str, int] = {}
        downstream_config = _read_json(project / "downstream_config.json")
        shards = downstream_config.get("shards")
        if isinstance(shards, list):
            for shard in shards:
                if not isinstance(shard, dict):
                    continue
                key = str(shard.get("key") or shard.get("node") or "").replace("-", "")
                if key:
                    targets[key] = int(shard.get("num_designs") or 0)

        nodes = plan.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                key = str(node.get("key") or node.get("node") or "").replace("-", "")
                if key and key not in targets:
                    targets[key] = int(node.get("num_designs") or 0)

        waves = plan.get("generation_waves")
        if isinstance(waves, list):
            for wave in waves:
                if not isinstance(wave, dict):
                    continue
                active_nodes = wave.get("active_nodes")
                if not isinstance(active_nodes, list):
                    continue
                for node in active_nodes:
                    if not isinstance(node, dict):
                        continue
                    key = str(node.get("node") or "").replace("-", "")
                    if key and key not in targets:
                        targets[key] = int(node.get("num_designs") or 0)

        generation = config.get("generation")
        generation = generation if isinstance(generation, dict) else {}
        per_node = int(generation.get("num_designs_per_node") or 0)
        if per_node:
            for index in range(1, int(generation.get("node_count") or 0) + 1):
                targets.setdefault(f"ln{index:02d}", per_node)
        return targets

    def _boltzgen_substeps(
        self,
        project: Path,
        workbench: Path,
        *,
        unit_id: str,
        target: int,
        cancelled: bool,
        design_override: int = 0,
    ) -> list[dict[str, Any]]:
        all_metrics = workbench / "final_ranked_designs" / "all_designs_metrics.csv"
        final_metrics = self._boltzgen_final_metrics(workbench)
        stage_definitions = (
            ("design", "Design"),
            ("inverse_folding", "Inverse folding"),
            ("folding", "Folding"),
            ("analysis", "Analysis"),
            ("filtering", "Filtering"),
        )

        if final_metrics is not None:
            analyzed_rows = self._csv_data_rows(all_metrics)
            filtered_rows = self._csv_data_rows(final_metrics)
            total = max(target, design_override, analyzed_rows, 1)
            stages = []
            for stage_id, title in stage_definitions:
                stage_total = analyzed_rows if stage_id == "filtering" and analyzed_rows else total
                stages.append(
                    self._boltzgen_stage(
                        stage_id,
                        title,
                        stage_total,
                        stage_total,
                        cancelled=False,
                        complete_marker=True,
                        result_count=(
                            analyzed_rows
                            if stage_id == "analysis"
                            else filtered_rows
                            if stage_id == "filtering"
                            else 0
                        ),
                        result_label=(
                            "analyzable"
                            if stage_id == "analysis"
                            else "retained"
                            if stage_id == "filtering"
                            else ""
                        ),
                    )
                )
            latest_activity = max(
                (
                    value
                    for value in (
                        _iso_mtime(all_metrics),
                        _iso_mtime(final_metrics),
                    )
                    if value
                ),
                default="",
            )
            if latest_activity:
                self._boltzgen_activity_cache[str(workbench)] = latest_activity
            return stages

        output_progress = self._boltzgen_output_progress(workbench)
        activity_values = [str(output_progress.get("updated_at") or "")]
        progress = self._boltzgen_log_progress(project, unit_id)
        current_index = int(progress.get("stage_index") or 0)
        output_counts = {
            stage_id: int(output_progress["counts"].get(stage_id) or 0)
            for stage_id, _ in stage_definitions
        }
        output_index = max(
            (
                index
                for index, (stage_id, _) in enumerate(stage_definitions, 1)
                if output_counts[stage_id] > 0
            ),
            default=0,
        )
        active_index = current_index or output_index
        total = max(
            target,
            design_override,
            int(progress.get("total") or 0),
            *(output_counts.values()),
            1,
        )
        stages = []
        for index, (stage_id, title) in enumerate(stage_definitions, 1):
            output_count = min(output_counts[stage_id], total)
            if index < active_index:
                completed = total
                complete_marker = True
                estimated = False
                started = False
            elif index == active_index and current_index == index:
                if progress.get("exact_count"):
                    completed = min(int(progress.get("completed") or 0), total)
                else:
                    completed = round(total * float(progress.get("fraction") or 0.0))
                complete_marker = float(progress.get("fraction") or 0.0) >= 1.0
                estimated = bool(progress.get("estimated"))
                started = bool(progress.get("started", True))
            elif index == active_index:
                completed = output_count
                complete_marker = completed >= total
                estimated = False
                started = bool(completed)
            elif stage_id == "design" and design_override:
                completed = min(design_override, total)
                complete_marker = completed >= total
                estimated = False
                started = bool(completed)
            else:
                completed = 0
                complete_marker = False
                estimated = False
                started = False
            stage = self._boltzgen_stage(
                stage_id,
                title,
                completed,
                total,
                cancelled=cancelled,
                complete_marker=complete_marker,
                next_started=started,
                result_count=(
                    completed
                    if stage_id == "analysis"
                    else output_counts["filtering"]
                    if stage_id == "filtering"
                    else 0
                ),
                result_label=(
                    "analyzable"
                    if stage_id == "analysis"
                    else "retained"
                    if stage_id == "filtering"
                    else ""
                ),
            )
            stage["progress"]["estimated"] = estimated
            stages.append(stage)
        activity_values.append(str(progress.get("updated_at") or ""))
        latest_activity = max(
            (value for value in activity_values if value),
            default="",
        )
        if latest_activity:
            self._boltzgen_activity_cache[str(workbench)] = latest_activity
        return stages

    def _boltzgen_output_progress(self, workbench: Path) -> dict[str, Any]:
        inverse_root = workbench / "intermediate_designs_inverse_folded"
        snapshots = {
            "design": self._directory_file_snapshot(
                workbench / "intermediate_designs" / "molecules_out_dir"
            ),
            "inverse_folding": self._directory_file_snapshot(
                inverse_root / "molecules_out_dir"
            ),
            "fold_out_npz": self._directory_file_snapshot(
                inverse_root / "fold_out_npz",
                suffix=".npz",
            ),
            "refold_cif": self._directory_file_snapshot(
                inverse_root / "refold_cif",
                suffix=".cif",
            ),
        }
        all_metrics = workbench / "final_ranked_designs" / "all_designs_metrics.csv"
        final_metrics = self._boltzgen_final_metrics(workbench)
        counts = {
            "design": int(snapshots["design"]["count"]),
            "inverse_folding": int(snapshots["inverse_folding"]["count"]),
            "folding": min(
                int(snapshots["fold_out_npz"]["count"]),
                int(snapshots["refold_cif"]["count"]),
            ),
            "analysis": self._csv_data_rows(all_metrics),
            "filtering": self._csv_data_rows(final_metrics),
        }
        updated_at = max(
            (
                value
                for value in (
                    *[
                        str(snapshot.get("updated_at") or "")
                        for snapshot in snapshots.values()
                    ],
                    _iso_mtime(all_metrics),
                    _iso_mtime(final_metrics),
                )
                if value
            ),
            default="",
        )
        return {
            "counts": counts,
            "updated_at": updated_at,
            "read_ok": all(
                bool(snapshot.get("read_ok")) for snapshot in snapshots.values()
            ),
        }

    def _boltzgen_final_metrics(self, workbench: Path) -> Path | None:
        cache_key = str(workbench)
        final_metrics = _first_glob(
            workbench / "final_ranked_designs",
            "final_designs_metrics_*.csv",
        )
        if final_metrics is not None:
            self._boltzgen_final_metrics_cache[cache_key] = final_metrics
            return final_metrics
        return self._boltzgen_final_metrics_cache.get(cache_key)

    def _boltzgen_log_progress(
        self,
        project: Path,
        unit_id: str,
    ) -> dict[str, Any]:
        cache_key = (str(project), unit_id)
        cached = self._boltzgen_progress_cache.get(cache_key, {})
        candidates = [
            project / "logs" / f"boltzgen_generation_{unit_id}.log",
        ]
        for root, pattern in (
            (project / "generation" / unit_id / "logs", "*_boltzgen.log"),
            (project / "output" / "generation" / unit_id / "logs", "*_boltzgen.log"),
            (project / "logs", f"run_*_{unit_id}.log"),
            (project / "logs", f"**/run_*_{unit_id}.log"),
        ):
            try:
                candidates.extend(sorted(root.glob(pattern)))
            except OSError:
                continue
        existing = [candidate for candidate in candidates if candidate.is_file()]
        path = max(existing, key=self._path_mtime, default=None)
        if path is None:
            return dict(cached)
        text = self._tail_text(path, max_bytes=768_000).replace("\r", "\n")
        if not text:
            return dict(cached)
        progress_pattern = re.compile(
            r"(?P<label>Processing samples:\s*)?"
            r"(?P<percent>\d{1,3})%\|[^\n]*?\|\s*"
            r"(?P<completed>\d+)\s*/\s*(?P<total>\d+)",
            re.IGNORECASE,
        )
        matches = list(progress_pattern.finditer(text))
        if matches:
            match = matches[-1]
            completed = int(match.group("completed"))
            total = int(match.group("total"))
            fraction = (
                min(1.0, completed / total)
                if total
                else int(match.group("percent")) / 100
            )
            if match.group("label"):
                stage_index, stage = 4, "analysis"
                exact_count = True
            else:
                marker = self._boltzgen_stage_marker(
                    path,
                    text[:match.start()],
                    before_offset=max(
                        0,
                        self._path_size(path) - 768_000,
                    ),
                )
                stage_index = int(marker.get("stage_index") or 0)
                stage = str(marker.get("stage") or "")
                exact_count = False
            result = {
                "stage_index": stage_index,
                "stage": stage,
                "fraction": fraction,
                "completed": completed,
                "total": total,
                "exact_count": exact_count,
                "estimated": not exact_count,
                "started": True,
                "updated_at": _iso_mtime(path),
            }
            if stage_index:
                self._boltzgen_progress_cache[cache_key] = result
                return result
        completed_pattern = re.compile(
            r"Step\s+(design|inverse folding|folding|analysis|filtering)"
            r"\s+completed successfully",
            re.IGNORECASE,
        )
        completed = list(completed_pattern.finditer(text))
        if completed:
            names = {
                "design": 1,
                "inverse folding": 2,
                "folding": 3,
                "analysis": 4,
                "filtering": 5,
            }
            index = names.get(completed[-1].group(1).lower(), 0)
            result = {
                "stage_index": index,
                "fraction": 1.0,
                "estimated": False,
                "started": True,
                "updated_at": _iso_mtime(path),
            }
            self._boltzgen_progress_cache[cache_key] = result
            return result
        marker = self._boltzgen_stage_marker(path, text)
        if marker:
            result = {
                **marker,
                "fraction": 0.0,
                "estimated": False,
                "started": True,
                "updated_at": _iso_mtime(path),
            }
            self._boltzgen_progress_cache[cache_key] = result
            return result
        return dict(cached)

    def _boltzgen_stage_marker(
        self,
        path: Path,
        known_text: str = "",
        *,
        before_offset: int | None = None,
    ) -> dict[str, Any]:
        marker = self._boltzgen_stage_marker_from_text(known_text)
        if marker:
            return marker
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                chunk_size = 1024 * 1024
                cursor = min(size, before_offset) if before_offset is not None else size
                scanned = 0
                while cursor > 0 and scanned < 64 * 1024 * 1024:
                    start = max(0, cursor - chunk_size)
                    handle.seek(start)
                    read_start = max(0, start - 256)
                    handle.seek(read_start)
                    text = handle.read(cursor - read_start).decode(
                        "utf-8",
                        errors="replace",
                    )
                    marker = self._boltzgen_stage_marker_from_text(
                        text.replace("\r", "\n")
                    )
                    if marker:
                        return marker
                    scanned += cursor - start
                    cursor = start
        except OSError:
            return {}
        return {}

    @staticmethod
    def _boltzgen_stage_marker_from_text(text: str) -> dict[str, Any]:
        matches: list[tuple[int, int, str]] = []
        for match in re.finditer(
            r"\[Step\s+([1-5])/5\]\s+([A-Za-z_ ]+?)(?:\s+-|:|\n|$)",
            text,
            re.IGNORECASE,
        ):
            matches.append(
                (
                    match.start(),
                    int(match.group(1)),
                    match.group(2).strip().lower(),
                )
            )
        for match in re.finditer(
            r"Initializing\s+FromGeneratedDataModule\s+datasets",
            text,
            re.IGNORECASE,
        ):
            matches.append((match.start(), 4, "analysis"))
        if not matches:
            return {}
        _, stage_index, stage = max(matches, key=lambda item: item[0])
        return {
            "stage_index": stage_index,
            "stage": stage,
        }

    def _directory_file_snapshot(
        self,
        directory: Path,
        *,
        suffix: str = "",
        prefix: str = "",
    ) -> dict[str, Any]:
        cache_key = (str(directory), f"{prefix}*{suffix}")
        cached_entry = self._directory_snapshot_cache.get(cache_key)
        try:
            stat = directory.stat()
        except FileNotFoundError:
            return {"count": 0, "updated_at": "", "read_ok": True}
        except OSError:
            if cached_entry:
                return {**cached_entry[2], "read_ok": False, "cached": True}
            return {"count": 0, "updated_at": "", "read_ok": False}
        if cached_entry and cached_entry[:2] == (stat.st_mtime_ns, stat.st_size):
            return dict(cached_entry[2])
        count = 0
        latest_mtime = 0.0
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if (
                        not entry.is_file(follow_symlinks=False)
                        or not entry.name.startswith(prefix)
                        or not entry.name.endswith(suffix)
                    ):
                        continue
                    entry_stat = entry.stat(follow_symlinks=False)
                    count += 1
                    latest_mtime = max(latest_mtime, entry_stat.st_mtime)
        except FileNotFoundError:
            return {"count": 0, "updated_at": "", "read_ok": True}
        except OSError:
            if cached_entry:
                return {**cached_entry[2], "read_ok": False, "cached": True}
            return {"count": 0, "updated_at": "", "read_ok": False}
        snapshot = {
            "count": count,
            "updated_at": (
                datetime.fromtimestamp(latest_mtime, timezone.utc).isoformat()
                if latest_mtime
                else ""
            ),
            "read_ok": True,
        }
        self._directory_snapshot_cache[cache_key] = (
            stat.st_mtime_ns,
            stat.st_size,
            snapshot,
        )
        return dict(snapshot)

    def _tail_text(self, path: Path, *, max_bytes: int) -> str:
        try:
            stat = path.stat()
        except OSError:
            return ""
        cache_key = (str(path), f"tail_{max_bytes}")
        cached = self._count_cache.get(cache_key)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return str(cached[2])
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, stat.st_size - max_bytes))
                text = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
        # The shared cache is numeric for counts; log tails are intentionally
        # not retained to keep memory bounded across many active workspaces.
        return text

    @staticmethod
    def _boltzgen_stage(
        stage_id: str,
        title: str,
        completed: int,
        total: int,
        *,
        cancelled: bool,
        complete_marker: bool = False,
        next_started: bool = False,
        result_count: int = 0,
        result_label: str = "",
    ) -> dict[str, Any]:
        total = max(int(total), 1)
        completed = min(max(int(completed), 0), total)
        if complete_marker or completed >= total:
            status = "complete"
            completed = total
        elif cancelled:
            status = "cancelled"
        elif completed or next_started:
            status = "running"
        else:
            status = "pending"
        return {
            "id": stage_id,
            "title": title,
            "status": status,
            "progress": {
                "completed": completed,
                "total": total,
                "fraction": min(1.0, completed / total),
            },
            "result_count": int(result_count),
            "result_label": result_label,
        }

    def _synthetic_cancelled_substeps(
        self,
        generated: int,
        target: int,
    ) -> list[dict[str, Any]]:
        stages = []
        for stage_id, title in (
            ("design", "Design"),
            ("inverse_folding", "Inverse folding"),
            ("folding", "Folding"),
            ("analysis", "Analysis"),
            ("filtering", "Filtering"),
        ):
            completed = generated if stage_id == "design" else 0
            stages.append(
                self._boltzgen_stage(
                    stage_id,
                    title,
                    completed,
                    target,
                    cancelled=True,
                )
            )
        return stages

    @staticmethod
    def _aggregate_boltzgen_stages(
        units: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        stage_ids = (
            ("design", "Design"),
            ("inverse_folding", "Inverse folding"),
            ("folding", "Folding"),
            ("analysis", "Analysis"),
            ("filtering", "Filtering"),
        )
        aggregates = []
        for stage_id, title in stage_ids:
            stages = [
                stage
                for unit in units
                for stage in unit.get("substeps", [])
                if stage.get("id") == stage_id
            ]
            if not stages:
                continue
            completed = sum(int(stage["progress"].get("completed") or 0) for stage in stages)
            total = sum(int(stage["progress"].get("total") or 0) for stage in stages)
            result_count = sum(int(stage.get("result_count") or 0) for stage in stages)
            statuses = {stage.get("status") for stage in stages}
            if "failed" in statuses:
                status = "failed"
            elif statuses == {"complete"}:
                status = "complete"
            elif "running" in statuses:
                status = "running"
            elif "cancelled" in statuses:
                status = "cancelled"
            else:
                status = "pending"
            aggregates.append(
                {
                    "id": stage_id,
                    "title": title,
                    "status": status,
                    "progress": {
                        "completed": completed,
                        "total": total,
                        "fraction": min(1.0, completed / total) if total else 0.0,
                        "estimated": any(
                            bool(stage["progress"].get("estimated")) for stage in stages
                        ),
                    },
                    "result_count": result_count,
                    "result_label": next(
                        (
                            str(stage.get("result_label") or "")
                            for stage in stages
                            if stage.get("result_label")
                        ),
                        "",
                    ),
                }
            )
        return aggregates

    def _count_direct_files(
        self,
        directory: Path,
        *,
        suffix: str,
        prefix: str = "",
    ) -> int:
        if not directory.is_dir():
            return 0
        try:
            stat = directory.stat()
        except OSError:
            return 0
        cache_key = (str(directory), f"{prefix}*{suffix}")
        cached = self._count_cache.get(cache_key)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2]
        try:
            count = sum(
                1
                for entry in os.scandir(directory)
                if entry.is_file(follow_symlinks=False)
                and entry.name.startswith(prefix)
                and entry.name.endswith(suffix)
            )
        except OSError:
            return 0
        self._count_cache[cache_key] = (stat.st_mtime_ns, stat.st_size, count)
        return count

    def _csv_data_rows(self, path: Path | None) -> int:
        if path is None:
            return 0
        cache_key = (str(path), "csv_rows")
        cached = self._count_cache.get(cache_key)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return 0
        except OSError:
            return cached[2] if cached else 0
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2]
        try:
            with path.open("rb") as handle:
                rows = 0
                last_byte = b""
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    rows += chunk.count(b"\n")
                    last_byte = chunk[-1:]
        except OSError:
            return cached[2] if cached else 0
        if stat.st_size and last_byte != b"\n":
            rows += 1
        rows = max(rows - 1, 0)
        self._count_cache[cache_key] = (stat.st_mtime_ns, stat.st_size, rows)
        return rows

    def _csv_pass_summary(
        self,
        path: Path,
        columns: tuple[str, ...],
    ) -> dict[str, int]:
        if not path.is_file():
            return {
                "rows": 0,
                **{column: 0 for column in columns},
                **{f"{column}__not_run": 0 for column in columns},
            }
        try:
            stat = path.stat()
        except OSError:
            return {
                "rows": 0,
                **{column: 0 for column in columns},
                **{f"{column}__not_run": 0 for column in columns},
            }
        cache_key = (str(path), f"csv_pass:{','.join(columns)}")
        cached = self._summary_cache.get(cache_key)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return dict(cached[2])
        summary = {
            "rows": 0,
            **{column: 0 for column in columns},
            **{f"{column}__not_run": 0 for column in columns},
        }
        try:
            with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                for row in csv.DictReader(handle):
                    summary["rows"] += 1
                    for column in columns:
                        value = str(row.get(column) or "").strip().lower()
                        if value in {"pass", "passed", "true", "1", "complete"}:
                            summary[column] += 1
                        elif value in {"not_run", "not run", "skipped", "disabled"}:
                            summary[f"{column}__not_run"] += 1
        except (OSError, csv.Error):
            return {
                "rows": 0,
                **{column: 0 for column in columns},
                **{f"{column}__not_run": 0 for column in columns},
            }
        self._summary_cache[cache_key] = (
            stat.st_mtime_ns,
            stat.st_size,
            dict(summary),
        )
        return summary

    def _csv_first_column_values(self, path: Path) -> set[str]:
        if not path.is_file():
            return set()
        try:
            stat = path.stat()
        except OSError:
            return set()
        cache_key = (str(path), "csv_first_column")
        cached = self._summary_cache.get(cache_key)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return set(cached[2])
        values: set[str] = set()
        try:
            with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                reader = csv.reader(handle)
                next(reader, None)
                for row in reader:
                    if row and row[0].strip():
                        values.add(row[0].strip().lower())
        except (OSError, csv.Error):
            return set()
        self._summary_cache[cache_key] = (
            stat.st_mtime_ns,
            stat.st_size,
            set(values),
        )
        return values

    def _direct_dir_names(self, directory: Path) -> set[str]:
        if not directory.is_dir():
            return set()
        try:
            stat = directory.stat()
        except OSError:
            return set()
        cache_key = (str(directory), "direct_dirs")
        cached = self._summary_cache.get(cache_key)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return set(cached[2])
        try:
            values = {
                entry.name.lower()
                for entry in os.scandir(directory)
                if entry.is_dir(follow_symlinks=False)
            }
        except OSError:
            return set()
        self._summary_cache[cache_key] = (
            stat.st_mtime_ns,
            stat.st_size,
            set(values),
        )
        return values

    def _partial_downstream(
        self,
        project: Path,
        config: dict[str, Any],
        plan: dict[str, Any],
        downstream: dict[str, Any],
        cancelled: bool,
    ) -> dict[str, Any]:
        step = self._partial_step_base("04_downstream_screening")
        step["detail_kind"] = "downstream"
        if cancelled:
            step["status"] = "cancelled"
            step["summary"] = "生成阶段终止，下游筛选未启动"
            step["updated_at"] = _iso_mtime(project / "execution_plan.json")
            return step

        downstream_config = _read_json(project / "downstream_config.json")
        configured_shards = downstream_config.get("shards")
        configured_shards = (
            configured_shards if isinstance(configured_shards, list) else []
        )
        if configured_shards:
            units = self._partial_ln_configured_downstream_units(
                project,
                downstream_config,
            )
        else:
            units = self._partial_ln_postprocess_units(project, config)

        step["units"] = units
        step["stage_totals"] = self._aggregate_partial_downstream_stages(units)
        cross_node_stage = self._partial_ln_cross_node_stage(project, units)
        step["stage_totals"].append(cross_node_stage)
        failed = sum(unit["status"] == "failed" for unit in units)
        completed = sum(unit["status"] == "complete" for unit in units)
        running = sum(unit["status"] == "running" for unit in units)
        current_state = downstream.get("current_state")
        current_state = current_state if isinstance(current_state, dict) else {}
        downstream_started = current_state.get("downstream_started")
        if failed:
            step["status"] = "failed"
            step["summary"] = f"{failed} downstream node failed"
            step["error"] = next(
                (unit["error"] for unit in units if unit.get("error")),
                "Inspect the failed postprocess node.",
            )
        elif (
            units
            and completed == len(units)
            and cross_node_stage["status"] == "complete"
        ):
            step["status"] = "complete"
            step["summary"] = f"{completed} / {len(units)} downstream nodes complete"
        elif running or completed:
            step["status"] = "running"
            af3_stage = next(
                (
                    stage
                    for stage in step["stage_totals"]
                    if stage["id"] == "af3_refolding"
                ),
                {},
            )
            progress = af3_stage.get("progress")
            progress = progress if isinstance(progress, dict) else {}
            step["summary"] = (
                f"{completed} / {len(units)} nodes complete · "
                f"{int(progress.get('completed') or 0):,} / "
                f"{int(progress.get('total') or 0):,} AF3 designs"
            )
        elif downstream_started is False or units:
            step["status"] = "pending"
            step["summary"] = "等待 generation shards 完成"
        else:
            step["summary"] = "等待可开发性筛选与结构复折叠"
        step["progress"] = {
            "completed": completed,
            "total": len(units) or 1,
            "failed": failed,
            "fraction": completed / len(units) if units else 0.0,
        }
        step["updated_at"] = max(
            (unit["updated_at"] for unit in units if unit.get("updated_at")),
            default=_iso_mtime(project / "downstream_runtime.json"),
        )
        return step

    def _partial_ln_postprocess_units(
        self,
        project: Path,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        statuses: dict[str, tuple[str, Path | None]] = {}
        status_dir = project / "postprocess" / "status"
        try:
            text_paths = sorted(status_dir.glob("ln*.status"))
        except OSError:
            text_paths = []
        for path in text_paths:
            try:
                phase = path.read_text(encoding="utf-8", errors="replace").split()[0]
            except (OSError, IndexError):
                phase = ""
            statuses[path.stem] = (phase, path)

        output_root = project / "postprocess" / "design"
        try:
            output_bases = sorted(
                path
                for path in output_root.glob("*/boltzgen_ln*")
                if path.is_dir()
            )
        except OSError:
            output_bases = []
        for output_base in output_bases:
            unit_id = self._partial_unit_id(output_base.name)
            statuses.setdefault(unit_id, ("", None))

        generation = config.get("generation")
        generation = generation if isinstance(generation, dict) else {}
        configured_nodes = int(generation.get("node_count") or 0)
        if configured_nodes:
            for index in range(1, configured_nodes + 1):
                statuses.setdefault(f"ln{index:02d}", ("", None))

        budget = int(generation.get("budget_per_node") or 0)
        units = []
        for unit_id, (phase, status_path) in sorted(statuses.items()):
            output_base = self._partial_ln_output_base(project, unit_id)
            summary_path = status_dir / f"{unit_id}_summary.json"
            complete_marker = summary_path.is_file()
            substeps, counts = self._partial_ln_downstream_substeps(
                output_base,
                expected_designs=budget,
                complete_marker=complete_marker,
            )
            log_path = self._latest_path(
                project / "logs" / "postprocess",
                f"{unit_id}.attempt_*.log",
            )
            text = self._tail_text(log_path, max_bytes=256_000) if log_path else ""
            error = self._log_error_summary(text)
            runtime_mtime = max(
                (
                    self._path_mtime(output_base),
                    self._path_mtime(output_base / "work" / "af3_input_with_msa"),
                    self._path_mtime(output_base / "af3" / "output"),
                    self._path_mtime(summary_path),
                )
            )
            if error and runtime_mtime <= self._path_mtime(log_path):
                status = "failed"
            elif complete_marker:
                status = "complete"
                error = ""
            elif self._partial_phase_status(phase) == "running" or any(
                stage["status"] in {"running", "complete"} for stage in substeps
            ):
                status = "running"
                error = ""
            else:
                status = self._partial_phase_status(phase)
                error = "" if status != "failed" else error

            updated_path = max(
                (
                    path
                    for path in (
                        status_path,
                        log_path,
                        summary_path if summary_path.is_file() else None,
                        output_base if output_base.is_dir() else None,
                    )
                    if path is not None
                ),
                key=self._path_mtime,
                default=None,
            )
            unit = self._partial_unit(
                unit_id,
                status,
                host=unit_id,
                updated_at=_iso_mtime(updated_path),
            )
            unit["detail_kind"] = "downstream"
            unit["substeps"] = substeps
            unit["shards"] = self._partial_ln_unavailable_gpu_units(
                range(8),
                status,
            )
            unit["shard_label"] = "GPU workers"
            unit["shard_note"] = (
                "This workflow records exact node totals but not per-GPU counters."
            )
            unit["current_stage"] = self._current_downstream_stage(
                substeps,
                fallback=phase or status,
            )
            unit["error"] = error
            unit["log_path"] = str(log_path) if log_path else ""
            unit["counts"] = counts
            units.append(unit)
        return units

    def _partial_ln_configured_downstream_units(
        self,
        project: Path,
        downstream_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        configured_shards = downstream_config.get("shards")
        configured_shards = (
            configured_shards if isinstance(configured_shards, list) else []
        )
        library = downstream_config.get("library")
        library = library if isinstance(library, dict) else {}
        expected_designs = int(library.get("per_node_budget") or 0)
        target = str(downstream_config.get("target") or "")
        physical_nodes: dict[str, list[dict[str, Any]]] = {}

        for shard_config in configured_shards:
            if not isinstance(shard_config, dict):
                continue
            key = str(shard_config.get("key") or shard_config.get("node") or "")
            physical_node = str(shard_config.get("node") or key).replace("-", "")
            state_dir = project / "state" / "downstream" / key
            status_path = state_dir / "status.json"
            status_payload = _read_json(status_path)
            phase = str(
                status_payload.get("phase")
                or status_payload.get("status")
                or ""
            )
            complete_marker = (state_dir / "_SUCCESS").is_file()
            output_base = self._partial_ln_output_base(
                project,
                key,
                target=target,
            )
            substeps, counts = self._partial_ln_downstream_substeps(
                output_base,
                expected_designs=expected_designs,
                complete_marker=complete_marker,
            )
            status = "complete" if complete_marker else self._partial_phase_status(phase)
            if status == "pending" and any(
                stage["status"] in {"running", "complete"} for stage in substeps
            ):
                status = "running"
            error = _error_message(status_payload)
            if status == "failed" and not error:
                error = f"{key}: downstream shard failed"
            detail = self._partial_unit(
                key,
                status,
                host=physical_node,
                updated_at=_iso_mtime(status_path),
                progress={
                    "completed": counts["analyzed"] or counts["refolded"],
                    "total": counts["total"],
                    "fraction": (
                        min(
                            1.0,
                            (counts["analyzed"] or counts["refolded"])
                            / counts["total"],
                        )
                        if counts["total"]
                        else 0.0
                    ),
                },
            )
            detail["title"] = key
            detail["kind"] = "logical"
            detail["phase"] = phase
            detail["assigned_gpus"] = [
                int(gpu) for gpu in shard_config.get("gpus", [])
            ]
            detail["dependency"] = str(shard_config.get("depends_on") or "")
            detail["input_ready"] = counts["input_ready"]
            detail["msa_ready"] = counts["msa_ready"]
            detail["refolded"] = counts["refolded"]
            detail["analyzed"] = counts["analyzed"]
            detail["error"] = error
            detail["substeps"] = substeps
            physical_nodes.setdefault(physical_node, []).append(detail)

        units = []
        for physical_node, logical_shards in sorted(physical_nodes.items()):
            temporary_units = [
                {"substeps": shard["substeps"]}
                for shard in logical_shards
            ]
            substeps = self._aggregate_partial_downstream_stages(temporary_units)
            statuses = {shard["status"] for shard in logical_shards}
            if "failed" in statuses:
                status = "failed"
            elif statuses == {"complete"}:
                status = "complete"
            elif "running" in statuses or "complete" in statuses:
                status = "running"
            else:
                status = "pending"
            unit = self._partial_unit(
                physical_node,
                status,
                host=physical_node,
                updated_at=max(
                    (
                        str(shard.get("updated_at") or "")
                        for shard in logical_shards
                    ),
                    default="",
                ),
            )
            unit["detail_kind"] = "downstream"
            unit["substeps"] = substeps
            unit["shards"] = logical_shards
            unit["shard_label"] = "Downstream shards"
            phases = {
                str(shard.get("phase") or "")
                for shard in logical_shards
                if shard.get("phase")
            }
            unit["current_stage"] = (
                "Waiting for BoltzGen"
                if phases and phases <= {"waiting_for_boltzgen"}
                else self._current_downstream_stage(
                    substeps,
                    fallback=next(iter(phases), status),
                )
            )
            unit["error"] = next(
                (
                    str(shard.get("error") or "")
                    for shard in logical_shards
                    if shard.get("error")
                ),
                "",
            )
            units.append(unit)
        return units

    def _partial_ln_downstream_substeps(
        self,
        output_base: Path,
        *,
        expected_designs: int,
        complete_marker: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        design_path = output_base / "all_designs.csv"
        tap_path = output_base / "developability_tap.csv"
        cpu_path = output_base / "developability_biophi_solubility.csv"
        merged_path = output_base / "developability.csv"
        af3_info_path = output_base / "af3" / "af3_info.csv"
        design_rows = self._csv_data_rows(design_path)
        total_designs = design_rows or expected_designs or 1
        tap_summary = self._csv_pass_summary(tap_path, ("TAP_filter",))
        cpu_summary = self._csv_pass_summary(
            cpu_path,
            (
                "PI_filter",
                "BioPhi_filter",
                "humanness_filter",
                "liability_filter",
                "solubility_filter",
            ),
        )
        merged_summary = self._csv_pass_summary(
            merged_path,
            ("all_filter_pass",),
        )
        tap_rows = int(tap_summary.get("rows") or 0)
        cpu_rows = int(cpu_summary.get("rows") or 0)
        merged_rows = int(merged_summary.get("rows") or 0)
        passed = int(merged_summary.get("all_filter_pass") or 0)
        filtered = max(merged_rows - passed, 0)
        input_ready = len(
            self._direct_dir_names(output_base / "work" / "af3_input")
        )
        msa_ready = len(
            self._direct_dir_names(output_base / "work" / "af3_input_with_msa")
        )
        refolded = len(self._direct_dir_names(output_base / "af3" / "output"))
        analyzed = self._csv_data_rows(af3_info_path)
        msa_ready = max(msa_ready, refolded, analyzed)
        input_ready = max(input_ready, msa_ready)
        af3_total = passed or input_ready or expected_designs or 1
        solubility_not_run = int(
            cpu_summary.get("solubility_filter__not_run") or 0
        )

        def metric(column: str) -> int:
            return int(cpu_summary.get(column) or 0)

        stages = [
            self._downstream_stage(
                "input_validation",
                "Input validation",
                design_rows,
                total_designs,
            ),
            self._downstream_stage(
                "structure_prediction",
                "Structure prediction",
                tap_rows,
                total_designs,
            ),
            self._downstream_stage(
                "tap_analysis",
                "TAP analysis",
                tap_rows,
                total_designs,
                metrics=[
                    {
                        "label": "passed",
                        "value": int(tap_summary.get("TAP_filter") or 0),
                        "tone": "success",
                    }
                ],
            ),
            self._downstream_stage(
                "pi_analysis",
                "pI analysis",
                cpu_rows,
                total_designs,
                metrics=[
                    {"label": "passed", "value": metric("PI_filter"), "tone": "success"}
                ],
            ),
            self._downstream_stage(
                "biophi_humanness",
                "BioPhi / Humanness",
                cpu_rows,
                total_designs,
                metrics=[
                    {
                        "label": "passed",
                        "value": max(
                            metric("BioPhi_filter"),
                            metric("humanness_filter"),
                        ),
                        "tone": "success",
                    }
                ],
            ),
            self._downstream_stage(
                "liability_analysis",
                "Liability analysis",
                cpu_rows,
                total_designs,
                metrics=[
                    {
                        "label": "passed",
                        "value": metric("liability_filter"),
                        "tone": "success",
                    }
                ],
            ),
            self._downstream_stage(
                "solubility_analysis",
                "Solubility analysis",
                0 if solubility_not_run >= cpu_rows and cpu_rows else cpu_rows,
                total_designs,
                status=(
                    "skipped"
                    if solubility_not_run >= cpu_rows and cpu_rows
                    else ""
                ),
                metrics=[
                    {
                        "label": (
                            "not run"
                            if solubility_not_run >= cpu_rows and cpu_rows
                            else "passed"
                        ),
                        "value": (
                            solubility_not_run
                            if solubility_not_run >= cpu_rows and cpu_rows
                            else metric("solubility_filter")
                        ),
                        "tone": (
                            "muted"
                            if solubility_not_run >= cpu_rows and cpu_rows
                            else "success"
                        ),
                    }
                ],
            ),
            self._downstream_stage(
                "developability_filter",
                "Developability filter",
                merged_rows,
                total_designs,
                metrics=[
                    {"label": "passed", "value": passed, "tone": "success"},
                    {"label": "filtered", "value": filtered, "tone": "muted"},
                ],
            ),
            self._downstream_stage(
                "af3_sharding",
                "AF3 worker preparation",
                input_ready,
                af3_total,
            ),
            self._downstream_stage(
                "msa_preparation",
                "AF3 input & MSA",
                msa_ready,
                af3_total,
            ),
            self._downstream_stage(
                "af3_refolding",
                "AF3 refolding",
                refolded,
                af3_total,
            ),
            self._downstream_stage(
                "af3_analysis",
                "AF3 scoring & RMSD",
                analyzed,
                af3_total,
            ),
            self._downstream_stage(
                "node_aggregation",
                "Node result validation",
                1 if complete_marker else 0,
                1,
            ),
        ]
        return stages, {
            "total": af3_total,
            "input_ready": input_ready,
            "msa_ready": msa_ready,
            "refolded": refolded,
            "analyzed": analyzed,
        }

    def _partial_ln_output_base(
        self,
        project: Path,
        unit_id: str,
        *,
        target: str = "",
    ) -> Path:
        for root in (
            project / "postprocess" / "design",
            project / "output" / "design",
        ):
            try:
                matches = sorted(
                    path
                    for path in root.glob(f"*/boltzgen_{unit_id}")
                    if path.is_dir()
                )
            except OSError:
                matches = []
            if matches:
                return matches[0]
        target_name = target or "target"
        preferred_root = (
            project / "output" / "design"
            if (project / "downstream_config.json").is_file()
            else project / "postprocess" / "design"
        )
        return preferred_root / target_name / f"boltzgen_{unit_id}"

    def _partial_ln_unavailable_gpu_units(
        self,
        gpus: Iterable[int],
        node_status: str,
    ) -> list[dict[str, Any]]:
        shards = []
        for gpu in gpus:
            status = (
                node_status
                if node_status in {"complete", "running", "failed", "cancelled"}
                else "pending"
            )
            shard = self._partial_unit(
                f"gpu{gpu}",
                status,
            )
            shard["title"] = f"GPU {gpu}"
            shard["gpu"] = gpu
            shard["unavailable"] = True
            shard["note"] = "Per-GPU counters are not recorded by this workflow."
            shard["assigned_gpus"] = [gpu]
            shard["input_ready"] = 0
            shard["msa_ready"] = 0
            shard["refolded"] = 0
            shard["analyzed"] = 0
            shards.append(shard)
        return shards

    @staticmethod
    def _current_downstream_stage(
        substeps: list[dict[str, Any]],
        *,
        fallback: str,
    ) -> str:
        return next(
            (
                stage["title"]
                for stage in reversed(substeps)
                if stage["status"] in {"failed", "running"}
            ),
            next(
                (
                    stage["title"]
                    for stage in substeps
                    if stage["status"] == "pending"
                ),
                fallback,
            ),
        )

    def _partial_ln_cross_node_stage(
        self,
        project: Path,
        units: list[dict[str, Any]],
    ) -> dict[str, Any]:
        candidates = (
            project / "library" / "aggregate",
            project / "postprocess" / "aggregate",
            project / "output" / "aggregate",
        )
        developability_path = next(
            (
                root / "developability_all_nodes.csv"
                for root in candidates
                if (root / "developability_all_nodes.csv").is_file()
            ),
            None,
        )
        af3_path = next(
            (
                root / "af3_info_all_nodes.csv"
                for root in candidates
                if (root / "af3_info_all_nodes.csv").is_file()
            ),
            None,
        )
        completed_files = sum(path is not None for path in (developability_path, af3_path))
        all_nodes_complete = bool(units) and all(
            unit["status"] == "complete" for unit in units
        )
        status = ""
        if completed_files < 2 and (completed_files or all_nodes_complete):
            status = "running"
        return self._downstream_stage(
            "cross_node_aggregation",
            "Cross-node aggregation",
            completed_files,
            2,
            status=status,
            metrics=[
                {
                    "label": "developability rows",
                    "value": self._csv_data_rows(developability_path),
                    "tone": "info",
                },
                {
                    "label": "AF3 rows",
                    "value": self._csv_data_rows(af3_path),
                    "tone": "info",
                },
            ],
        )

    @staticmethod
    def _latest_path(directory: Path, pattern: str) -> Path | None:
        try:
            paths = sorted(
                (path for path in directory.glob(pattern) if path.is_file()),
                key=lambda path: path.stat().st_mtime,
            )
        except OSError:
            return None
        return paths[-1] if paths else None

    def _partial_final_library(
        self,
        project: Path,
        config: dict[str, Any],
        downstream: dict[str, Any],
        cancelled: bool,
    ) -> dict[str, Any]:
        step = self._partial_step_base("05_final_library")
        target = self._partial_library_target(project, config)
        step["title"] = f"Final {target:,} Library Selection" if target else "Final Library Selection"
        if cancelled:
            step["status"] = "cancelled"
            step["summary"] = (
                f"工作流终止，未生成最终 {target:,} 条文库"
                if target
                else "工作流终止，未生成最终文库"
            )
            step["progress"] = {
                "completed": 0,
                "total": target,
                "fraction": 0.0,
            }
            step["updated_at"] = _iso_mtime(project / "execution_plan.json")
            return step

        final_status_path = project / "postprocess" / "status" / "final_library.status"
        try:
            final_phase = (
                final_status_path.read_text(encoding="utf-8", errors="replace").split()[0]
                if final_status_path.is_file()
                else ""
            )
        except (OSError, IndexError):
            final_phase = ""
        finalizer_path = project / "state" / "downstream" / "finalizer" / "status.json"
        finalizer = _read_json(finalizer_path)
        final_phase = str(finalizer.get("phase") or final_phase)
        final_dirs = self._partial_final_dirs(project, config)
        deliverables = []
        for root in final_dirs:
            for pattern in ("*manifest*.json", "*summary*.json", "*.csv", "*.xlsx", "*.tar.gz"):
                try:
                    deliverables.extend(path for path in root.glob(pattern) if path.is_file())
                except OSError:
                    continue
        selected = self._partial_selected_library_count(final_dirs)
        if (
            (target and selected >= target)
            or self._partial_phase_status(final_phase) == "complete"
        ):
            step["status"] = "complete"
            step["summary"] = (
                f"最终文库筛选完成 · {selected:,} / {target:,}"
                if target
                else f"最终 partial de novo library 已生成 · {len(deliverables)} deliverables"
            )
        elif self._partial_phase_status(final_phase) == "running":
            step["status"] = "running"
            step["summary"] = (
                f"正在筛选最终文库 · {selected:,} / {target:,}"
                if target
                else "正在聚合最终 partial de novo library"
            )
        else:
            step["summary"] = (
                f"等待下游完成后筛选最终 {target:,} 条文库"
                if target
                else "等待所有下游 shard 完成后聚合"
            )
        step["progress"] = {
            "completed": min(selected, target) if target else selected,
            "total": target,
            "fraction": min(1.0, selected / target) if target else 0.0,
        }
        step["updated_at"] = max(
            (
                value
                for value in (
                    _iso_mtime(final_status_path),
                    _iso_mtime(finalizer_path),
                    *[_iso_mtime(path) for path in deliverables],
                )
                if value
            ),
            default="",
        )
        step["evidence"] = [str(path) for path in deliverables]
        return step

    @staticmethod
    def _partial_library_target(project: Path, config: dict[str, Any]) -> int:
        library_design = config.get("library_design")
        library_design = library_design if isinstance(library_design, dict) else {}
        downstream_config = _read_json(project / "downstream_config.json")
        downstream_library = downstream_config.get("library")
        downstream_library = (
            downstream_library if isinstance(downstream_library, dict) else {}
        )
        plan = _read_json(project / "execution_plan.json")
        generation = plan.get("generation")
        generation = generation if isinstance(generation, dict) else {}
        final_library = plan.get("final_library")
        final_library = final_library if isinstance(final_library, dict) else {}
        return int(
            library_design.get("library_size")
            or downstream_library.get("library_size")
            or generation.get("final_library_target")
            or final_library.get("library_size")
            or 0
        )

    def _partial_selected_library_count(self, final_dirs: list[Path]) -> int:
        for root in final_dirs:
            validation = _read_json(root / "validation.json")
            if validation:
                value = validation.get("heavy_pool_rows")
                if isinstance(value, (int, float)):
                    return int(value)
            for path in sorted(root.glob("*manifest*.json")):
                manifest = _read_json(path)
                counts = manifest.get("counts")
                counts = counts if isinstance(counts, dict) else {}
                value = counts.get("heavy_pool")
                if isinstance(value, (int, float)):
                    return int(value)
            heavy_csv = _first_glob(root, "*oligo_pool_design_H*.csv")
            if heavy_csv:
                return self._csv_data_rows(heavy_csv)
        return 0

    @staticmethod
    def _partial_phase_status(value: str) -> str:
        phase = str(value or "").strip().lower()
        if any(token in phase for token in ("failed", "error", "stalled")):
            return "failed"
        if any(token in phase for token in ("cancel", "terminate", "stopped")):
            return "cancelled"
        if any(token in phase for token in ("complete", "success", "passed", "done")):
            return "complete"
        if any(
            token in phase
            for token in (
                "running",
                "attempt",
                "processing",
                "waiting_for_idle",
                "finalizing",
                "aggregating",
            )
        ):
            return "running"
        return "pending"

    @staticmethod
    def _partial_unit(
        unit_id: str,
        status: str,
        *,
        host: str = "",
        updated_at: str = "",
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": unit_id,
            "title": unit_id,
            "status": status,
            "host": host,
            "gpu": "",
            "error": "",
            "updated_at": updated_at,
            "progress": progress or {},
            "log_path": "",
        }

    @staticmethod
    def _partial_unit_id(name: str) -> str:
        huoshan = re.search(r"(huoshan[_-]A800[_-]\d+)$", name, re.IGNORECASE)
        if huoshan:
            return huoshan.group(1).replace("-", "_")
        parts = name.split("_")
        for index in range(len(parts) - 1, -1, -1):
            if parts[index].lower().startswith("ln"):
                return "_".join(parts[index:])
        return parts[-1]

    def _partial_final_dirs(
        self,
        project: Path,
        config: dict[str, Any],
    ) -> list[Path]:
        candidates = [
            project / "library" / "final_12000",
            project / "output" / "final_library",
            project / "output" / "library",
        ]
        execution = config.get("execution")
        execution = execution if isinstance(execution, dict) else {}
        automation = execution.get("postprocess_automation")
        automation = automation if isinstance(automation, dict) else {}
        value = automation.get("final_output_dir")
        if value:
            try:
                candidates.insert(0, _safe_resolve(self.project_root, str(value)))
            except ValueError:
                pass
        unique: list[Path] = []
        for path in candidates:
            if path.is_dir() and path not in unique:
                unique.append(path)
        return unique

    def _partial_artifacts(self, project: Path) -> list[dict[str, Any]]:
        selected: dict[Path, tuple[str, str]] = {}
        patterns = {
            "01_target_preparation": (
                "target_prep/nanobody_partial_library_manifest.json",
                "target_prep/structure_prep_status.json",
                "target_prep/*/target_manifest.json",
            ),
            "02_partial_scaffolds": (
                "boltzgen/fab_scaffolds/*/manifest.csv",
            ),
            "03_boltzgen_generation": (
                "boltzgen/workbench/*/final_ranked_designs/final_designs_metrics_*.csv",
                "boltzgen/workbench/*/final_ranked_designs/results_overview.pdf",
            ),
        }
        for step_id, step_patterns in patterns.items():
            for pattern in step_patterns:
                try:
                    matches = sorted(project.glob(pattern))
                except OSError:
                    matches = []
                for path in matches:
                    if path.is_file():
                        purpose = (
                            "Step summary"
                            if path.suffix.lower() == ".pdf"
                            else "Next-step input"
                        )
                        selected.setdefault(path, (step_id, purpose))
        for final_dir in self._partial_final_dirs(
            project,
            _read_json(
                _first_existing(
                    (
                        project / "configs" / "nanobody_partial_library.json",
                        project / "project_config.json",
                    )
                )
            ),
        ):
            for pattern in ("*manifest*.json", "*summary*.json", "*.csv", "*.xlsx", "*.tar.gz"):
                try:
                    matches = sorted(final_dir.glob(pattern))
                except OSError:
                    matches = []
                for path in matches:
                    if path.is_file():
                        selected.setdefault(path, ("05_final_library", "Final result"))

        labels = {
            definition[0]: f"Step {int(definition[0][:2])}"
            for definition in PARTIAL_STEP_DEFINITIONS
        }
        records = []
        for path, (step_id, purpose) in selected.items():
            record = self._artifact_record(
                path,
                project,
                step_id=step_id,
                step_label=labels[step_id],
                purpose=purpose,
                source_run="workspace",
            )
            if record:
                records.append(record)
        records.sort(key=lambda item: (item["step_number"], item["relative_path"].lower()))
        return records

    @staticmethod
    def _artifact_record(
        path: Path,
        relative_root: Path,
        *,
        step_id: str,
        step_label: str,
        purpose: str,
        source_run: str,
    ) -> dict[str, Any] | None:
        try:
            stat = path.stat()
            relative = path.relative_to(relative_root)
        except (OSError, ValueError):
            return None
        extension = path.suffix.lower()
        return {
            "name": path.name,
            "path": str(path),
            "relative_path": str(relative),
            "size": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "extension": extension.lstrip(".") or "file",
            "previewable": extension in TEXT_EXTENSIONS and stat.st_size <= 2_000_000,
            "important": True,
            "step_id": step_id,
            "step_number": int(step_id[:2]),
            "step_label": step_label,
            "purpose": purpose,
            "source_run": source_run,
            "exists": True,
            "empty": stat.st_size == 0,
            "missing": False,
            "is_manifest": "manifest" in path.name.lower(),
            "artifact_source": "filesystem",
            "listed_by_manifest": False,
        }

    def _reused_mutation_library_manifest(self, run: Path) -> Path | None:
        design_manifest_path = _first_glob(
            run / "outputs" / "output" / "library_design",
            "*_library_design_manifest.json",
        )
        design_manifest = _read_json(design_manifest_path)
        config = design_manifest.get("config")
        config = config if isinstance(config, dict) else {}
        value = config.get("mutation_library_dir")
        if not value:
            return None
        try:
            mutation_dir = _safe_resolve(self.project_root, str(value))
        except ValueError:
            return None
        generic = mutation_dir / "mutation_library_manifest.json"
        return generic if generic.is_file() else _first_glob(
            mutation_dir,
            "*_mutation_library_manifest.json",
        )

    @staticmethod
    def _source_run_id(path: Path) -> str:
        parts = path.parts
        try:
            index = parts.index("runs")
            return parts[index + 1]
        except (ValueError, IndexError):
            return ""

    @staticmethod
    def _resolve_expected_artifact(run: Path, template: str, task: str) -> list[Path]:
        """Resolve a workflow-declared output without scanning intermediate trees."""
        if not template.startswith("outputs/output/"):
            return []
        variants = [task, task.lower()] if task else [""]
        paths: list[Path] = []
        seen: set[Path] = set()
        is_directory = template.endswith("/")

        for value in variants:
            relative = template.replace("<task>", value)
            candidate = run / relative.rstrip("/")
            if is_directory and candidate.is_dir():
                try:
                    matches = sorted(path for path in candidate.rglob("*") if path.is_file())
                except OSError:
                    matches = []
            elif candidate.is_file():
                matches = [candidate]
            else:
                matches = []
            for path in matches:
                if path not in seen:
                    seen.add(path)
                    paths.append(path)

        if paths:
            return paths

        # Older IFLD runs prefix the mutation manifest with the task name even
        # though the profile declares the generic filename.
        if template.endswith("/mutation_library/mutation_library_manifest.json"):
            try:
                matches = sorted(
                    (run / "outputs" / "output" / "mutation_library").glob(
                        "*_mutation_library_manifest.json"
                    )
                )
            except OSError:
                matches = []
            return [path for path in matches if path.is_file()]

        # Task casing and legacy naming differ between some historical runs.
        wildcard = template.replace("<task>", "*").rstrip("/")
        try:
            matches = sorted(run.glob(wildcard))
        except OSError:
            matches = []
        return [path for path in matches if path.is_file()]

    def preview_file(self, value: str, *, max_chars: int = 200_000) -> dict[str, Any]:
        path = _safe_resolve(self.project_root, value)
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            raise ValueError("This file type is not previewable")
        if path.stat().st_size > 2_000_000:
            raise ValueError("File is too large to preview")
        text = path.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > max_chars
        return {
            "path": str(path),
            "name": path.name,
            "content": text[:max_chars],
            "truncated": truncated,
        }

    def resolve_download(self, value: str) -> Path:
        path = _safe_resolve(self.project_root, value)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def _project_path(self, name: str) -> Path:
        path = _safe_resolve(self.project_root, name)
        if path.parent != self.project_root or not path.is_dir():
            raise FileNotFoundError(name)
        return path

    @staticmethod
    def _run_paths(project: Path) -> list[Path]:
        root = project / "runs"
        if not root.is_dir():
            return []
        return sorted(
            (path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")),
            key=lambda item: item.name,
        )

    def _run_path(self, project: Path, run_id: str) -> Path:
        root = (project / "runs").resolve()
        path = _safe_resolve(root, run_id)
        if path.parent != root or not path.is_dir():
            raise FileNotFoundError(run_id)
        return path

    @staticmethod
    def _step_base(step_id: str) -> dict[str, Any]:
        definition = next(item for item in STEP_DEFINITIONS if item[0] == step_id)
        return {
            "id": definition[0],
            "number": int(definition[0][:2]),
            "title": definition[1],
            "description": definition[2],
            "status": "pending",
            "summary": "",
            "error": "",
            "progress": {},
            "units": [],
            "updated_at": "",
            "evidence": [],
        }

    def _step_initialize(
        self,
        run: Path,
        project_input: Path | None,
        resolved: Path | None,
        lock: Path | None,
        command: Path | None,
        failed_marker: dict[str, Any],
    ) -> dict[str, Any]:
        step = self._step_base("00_initialize")
        files = [project_input, resolved, lock, command]
        present = [path for path in files if path is not None]
        step["progress"] = {
            "completed": len(present),
            "total": len(files),
            "fraction": len(present) / len(files),
        }
        step["evidence"] = [str(path) for path in present]
        failed_step = str(failed_marker.get("failed_step") or "").lower()
        if failed_marker and any(token in failed_step for token in ("valid", "init", "config")):
            step["status"] = "failed"
            step["error"] = _error_message(failed_marker)
            step["summary"] = "初始化或配置校验失败"
        elif len(present) == len(files):
            step["status"] = "complete"
            step["summary"] = "配置、Profile lock 与命令入口已就绪"
        else:
            step["status"] = "pending"
            missing = [
                name
                for name, path in zip(
                    ("project_input.json", "resolved_config.json", "profile.lock.json", "command.sh"),
                    files,
                )
                if path is None
            ]
            step["summary"] = f"等待配置文件：{', '.join(missing)}"
        step["updated_at"] = max((_iso_mtime(path) for path in present), default="")
        return step

    def _step_structure_prediction(
        self, output_root: Path, stages: dict[str, Any]
    ) -> dict[str, Any]:
        step = self._step_base("01_structure_prediction")
        stage = stages.get("structure_prediction")
        stage = stage if isinstance(stage, dict) else {}
        step["status"] = _normalize_status(stage.get("status"))
        bootstrap_path = output_root / "work" / "existing_selected_structures" / "bootstrap_manifest.json"
        bootstrap = _read_json(bootstrap_path)
        selected_dir = output_root / "output" / "selected_structures"
        selected_count = _count_files(selected_dir, ("*.cif", "*.pdb"))
        if step["status"] == "skipped" and bootstrap:
            step["status"] = "reused"
            step["summary"] = "使用已审查结构；当前 Profile 跳过结构预测"
            step["evidence"].append(str(bootstrap_path))
        elif step["status"] == "complete":
            step["summary"] = "结构预测完成"
        elif step["status"] == "pending" and selected_count:
            step["status"] = "reused"
            step["summary"] = "检测到现有结构输入"
        else:
            step["summary"] = "等待结构预测"
        if selected_count:
            step["progress"] = {
                "completed": selected_count,
                "total": selected_count,
                "fraction": 1.0,
                "label": f"{selected_count} structures",
            }
        jobs_root = output_root / "work" / "step1_distributed" / "jobs"
        step["units"] = self._status_units(jobs_root, ("worker_status.json", "job_status.json"))
        step["error"] = _error_message(stage, bootstrap)
        step["updated_at"] = str(stage.get("finished_at") or stage.get("started_at") or "")
        return step

    def _step_structure_clustering(
        self, output_root: Path, stages: dict[str, Any]
    ) -> dict[str, Any]:
        step = self._step_base("02_structure_clustering")
        stage = stages.get("structure_clustering")
        stage = stage if isinstance(stage, dict) else {}
        step["status"] = _normalize_status(stage.get("status"))
        import_path = output_root / "work" / "existing_selected_structures" / "import_manifest.json"
        imported = _read_json(import_path)
        selected_dir = output_root / "output" / "selected_structures"
        selected_count = _count_files(selected_dir, ("*.cif", "*.pdb"))
        if step["status"] == "skipped" and imported:
            step["status"] = "reused"
            step["summary"] = "直接采用已审查的代表结构"
            step["evidence"].append(str(import_path))
        elif step["status"] == "complete":
            step["summary"] = "结构聚类与代表选择完成"
        elif selected_count:
            step["status"] = "reused"
            step["summary"] = "检测到已选代表结构"
        else:
            step["summary"] = "等待结构聚类"
        step["progress"] = {
            "completed": selected_count,
            "total": selected_count or 1,
            "fraction": 1.0 if selected_count else 0.0,
            "label": f"{selected_count} selected structures",
        }
        step["error"] = _error_message(stage, imported)
        step["updated_at"] = str(stage.get("finished_at") or stage.get("started_at") or "")
        return step

    def _step_mutation_library(
        self, run: Path, output_root: Path, stages: dict[str, Any]
    ) -> dict[str, Any]:
        step = self._step_base("03_mutation_library")
        stage = stages.get("mutation_library")
        stage = stage if isinstance(stage, dict) else {}
        step["status"] = _normalize_status(stage.get("status"))
        mutation_dir = output_root / "output" / "mutation_library"
        method_status_dir = mutation_dir / "method_status"
        step["units"] = self._status_units(method_status_dir, ("*.json",), direct=True)

        library_manifest_path = _first_glob(
            output_root / "output" / "library_design", "*_library_design_manifest.json"
        )
        library_manifest = _read_json(library_manifest_path)
        config = library_manifest.get("config")
        config = config if isinstance(config, dict) else {}
        referenced_value = config.get("mutation_library_dir")
        referenced = Path(str(referenced_value)) if referenced_value else None
        reused_from = ""
        if referenced and referenced.is_absolute() and run not in referenced.parents:
            reused_from = str(referenced)

        if step["status"] == "complete":
            step["summary"] = "候选生成与突变效应评分完成"
        elif reused_from:
            step["status"] = "reused"
            step["summary"] = "复用其他 Run 的 mutation library 与 scorer 结果"
            step["reused_from"] = reused_from
            step["evidence"].append(reused_from)
        elif step["status"] == "skipped" and _count_files(mutation_dir):
            step["status"] = "reused"
            step["summary"] = "使用预先计算的 mutation library"
        else:
            step["summary"] = "等待候选生成与评分"

        if step["units"]:
            completed = sum(unit["status"] in {"complete", "reused"} for unit in step["units"])
            failed = sum(unit["status"] == "failed" for unit in step["units"])
            step["progress"] = {
                "completed": completed,
                "total": len(step["units"]),
                "failed": failed,
                "fraction": completed / len(step["units"]),
            }
            if failed and step["status"] == "complete":
                step["status"] = "degraded"
        step["error"] = _error_message(stage, library_manifest)
        step["updated_at"] = str(stage.get("finished_at") or stage.get("started_at") or "")
        return step

    def _step_candidate_filter(
        self, run: Path, output_root: Path, stages: dict[str, Any]
    ) -> dict[str, Any]:
        step = self._step_base("04_candidate_filter")
        stage = stages.get("candidate_filter")
        stage = stage if isinstance(stage, dict) else {}
        manifest_path = _first_glob(
            output_root / "output" / "candidate_filter", "*_candidate_filter_manifest.json"
        )
        manifest = _read_json(manifest_path)
        step["status"] = _normalize_status(manifest.get("status") or stage.get("status"))
        if step["status"] == "complete":
            candidates = int(manifest.get("n_candidates") or 0)
            selected = int(manifest.get("n_selected") or 0)
            step["summary"] = f"{candidates:,} candidates processed · {selected:,} selected"
        else:
            step["summary"] = "等待复折叠与候选筛选"
        step["progress"] = _progress_payload(manifest)
        if manifest.get("n_candidates") and not step["progress"].get("total"):
            step["progress"]["total"] = manifest["n_candidates"]

        scheduler_candidates = (
            output_root / "work" / "streaming_refolding" / "scheduler_manifest.json",
            *sorted(
                run.glob(
                    "project_adapters/workspaces/*/work/streaming_refolding/scheduler_manifest.json"
                )
            ),
        )
        scheduler_path = _first_existing(scheduler_candidates)
        scheduler = _read_json(scheduler_path)
        if scheduler:
            step["units"].append(
                {
                    "id": "streaming_refolding",
                    "title": "Streaming refolding",
                    "status": _normalize_status(scheduler.get("status")),
                    "host": str(scheduler.get("host") or ""),
                    "gpu": "",
                    "error": _error_message(scheduler),
                    "updated_at": str(scheduler.get("updated_at") or ""),
                    "progress": _progress_payload(scheduler),
                    "log_path": str(scheduler.get("log_path") or ""),
                }
            )
            step["evidence"].append(str(scheduler_path))
        if manifest_path:
            step["evidence"].append(str(manifest_path))
        step["error"] = _error_message(manifest, stage, scheduler)
        step["updated_at"] = str(
            manifest.get("updated_at")
            or manifest.get("finished_at")
            or stage.get("finished_at")
            or ""
        )
        return step

    def _step_library_design(
        self, output_root: Path, stages: dict[str, Any]
    ) -> dict[str, Any]:
        step = self._step_base("05_library_design")
        stage = stages.get("library_design")
        stage = stage if isinstance(stage, dict) else {}
        design_dir = output_root / "output" / "library_design"
        manifest_path = _first_glob(design_dir, "*_library_design_manifest.json")
        selection_path = _first_glob(design_dir, "*_expression_selection_status.json")
        manifest = _read_json(manifest_path)
        selection = _read_json(selection_path)
        step["status"] = _normalize_status(manifest.get("status") or stage.get("status"))
        selected = int(selection.get("selected") or 0)
        target = int(selection.get("target") or 0)
        evaluated = int(selection.get("evaluated") or 0)
        if step["status"] == "complete":
            step["summary"] = (
                f"{selected:,} expression designs selected"
                + (f" from {evaluated:,} evaluated" if evaluated else "")
            )
        else:
            step["summary"] = "等待表达候选与文库设计"
        step["progress"] = {
            "completed": selected,
            "total": target,
            "fraction": min(1.0, selected / target) if target else 0.0,
        }
        if selection:
            step["units"].append(
                {
                    "id": "expression_selection",
                    "title": "Expression selection",
                    "status": "complete" if selection.get("complete") else step["status"],
                    "host": "",
                    "gpu": "",
                    "error": _error_message(selection),
                    "updated_at": _iso_mtime(selection_path),
                    "progress": {
                        "completed": selected,
                        "total": target,
                        "fraction": min(1.0, selected / target) if target else 0.0,
                    },
                    "log_path": "",
                }
            )
        step["error"] = _error_message(manifest, selection, stage)
        step["updated_at"] = str(
            stage.get("finished_at")
            or manifest.get("finished_at")
            or _iso_mtime(manifest_path)
        )
        step["evidence"] = [
            str(path) for path in (manifest_path, selection_path) if path is not None
        ]
        return step

    @staticmethod
    def _status_units(
        root: Path,
        names: tuple[str, ...],
        *,
        direct: bool = False,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        if not root.is_dir():
            return []
        paths: list[Path] = []
        try:
            if direct:
                for name in names:
                    paths.extend(root.glob(name))
            else:
                for name in names:
                    paths.extend(root.glob(f"*/{name}"))
        except OSError:
            return []
        units = []
        seen: set[str] = set()
        for path in sorted(paths)[:limit]:
            payload = _read_json(path)
            unit_id = str(
                payload.get("job_id")
                or payload.get("shard_id")
                or payload.get("method")
                or path.parent.name
                or path.stem
            )
            if unit_id in seen:
                continue
            seen.add(unit_id)
            units.append(
                {
                    "id": unit_id,
                    "title": str(payload.get("method") or payload.get("title") or unit_id),
                    "status": _normalize_status(
                        payload.get("status")
                        or ("complete" if payload.get("completed") is True else "")
                    ),
                    "host": str(payload.get("host") or payload.get("hostname") or ""),
                    "gpu": payload.get("gpu", ""),
                    "error": _error_message(payload),
                    "updated_at": str(payload.get("updated_at") or _iso_mtime(path)),
                    "progress": _progress_payload(payload),
                    "log_path": str(payload.get("log_path") or ""),
                }
            )
        return units

    @staticmethod
    def _overall_status(steps: list[dict[str, Any]]) -> str:
        statuses = {step["status"] for step in steps}
        if "failed" in statuses:
            return "failed"
        if "blocked" in statuses:
            return "blocked"
        if "running" in statuses:
            return "running"
        if "degraded" in statuses:
            return "degraded"
        if "cancelled" in statuses:
            return "cancelled"
        if statuses <= {"complete", "reused", "skipped"}:
            return "complete"
        return "pending"
