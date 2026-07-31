const loopbackHost = ["127.0.0.1", "localhost"].includes(window.location.hostname)
  ? window.location.hostname
  : "127.0.0.1";

const servers = [
  {
    id: "ln",
    label: "LN",
    apiBase: `http://${loopbackHost}:8765`,
  },
  {
    id: "huoshan",
    label: "Huoshan A800",
    apiBase: `http://${loopbackHost}:8766`,
  },
];

const state = {
  projects: [],
  projectsByServer: {},
  healthByServer: {},
  runs: [],
  run: null,
  manifestArtifacts: [],
  project: "",
  runId: "",
  serverFilter: "ln",
  activeServerId: "ln",
  workflowFamily: "ifld",
  projectSearch: "",
  projectStatusFilter: "all",
  artifactSearch: "",
  sidePanelTab: "outputs",
  artifactPanelCollapsed: false,
  collapsedArtifactGroups: new Set(),
  expandedSteps: new Set(),
  expandedUnits: new Set(),
  expandedShardGroups: new Set(),
  progressSamples: new Map(),
  timer: null,
};

const elements = {
  serverSwitcher: document.querySelector("#serverSwitcher"),
  allServerCount: document.querySelector("#allServerCount"),
  lnServerCount: document.querySelector("#lnServerCount"),
  huoshanServerCount: document.querySelector("#huoshanServerCount"),
  serverName: document.querySelector("#serverName"),
  projectRootPath: document.querySelector("#projectRootPath"),
  projectList: document.querySelector("#projectList"),
  projectCount: document.querySelector("#projectCount"),
  workflowBranches: document.querySelector("#workflowBranches"),
  ifldCount: document.querySelector("#ifldCount"),
  partialCount: document.querySelector("#partialCount"),
  projectSearch: document.querySelector("#projectSearch"),
  projectStatusFilters: document.querySelector("#projectStatusFilters"),
  projectAllCount: document.querySelector("#projectAllCount"),
  projectRunningCount: document.querySelector("#projectRunningCount"),
  projectFailedCount: document.querySelector("#projectFailedCount"),
  projectPendingCount: document.querySelector("#projectPendingCount"),
  projectCompleteCount: document.querySelector("#projectCompleteCount"),
  projectTitle: document.querySelector("#projectTitle"),
  projectMeta: document.querySelector("#projectMeta"),
  workflowEyebrow: document.querySelector("#workflowEyebrow"),
  pipelineTitle: document.querySelector("#pipelineTitle"),
  runSelectLabelText: document.querySelector("#runSelectLabelText"),
  runSelect: document.querySelector("#runSelect"),
  refreshButton: document.querySelector("#refreshButton"),
  runSummary: document.querySelector("#runSummary"),
  overallStatus: document.querySelector("#overallStatus"),
  workflowName: document.querySelector("#workflowName"),
  profileName: document.querySelector("#profileName"),
  currentStage: document.querySelector("#currentStage"),
  workflowProgress: document.querySelector("#workflowProgress"),
  workflowProgressBar: document.querySelector("#workflowProgressBar"),
  workflowProgressCount: document.querySelector("#workflowProgressCount"),
  workflowProgressText: document.querySelector("#workflowProgressText"),
  runtimeMeta: document.querySelector("#runtimeMeta"),
  stepsComplete: document.querySelector("#stepsComplete"),
  activityLabel: document.querySelector("#activityLabel"),
  lastUpdated: document.querySelector("#lastUpdated"),
  activeErrorFact: document.querySelector("#activeErrorFact"),
  activeErrors: document.querySelector("#activeErrors"),
  pipeline: document.querySelector("#pipeline"),
  contentGrid: document.querySelector("#contentGrid"),
  artifactPanel: document.querySelector("#artifactPanel"),
  artifactPanelToggle: document.querySelector("#artifactPanelToggle"),
  sidePanelTitle: document.querySelector("#sidePanelTitle"),
  sidePanelTabs: document.querySelector("#sidePanelTabs"),
  sideErrorCount: document.querySelector("#sideErrorCount"),
  sideLogCount: document.querySelector("#sideLogCount"),
  artifactControls: document.querySelector("#artifactControls"),
  artifactCount: document.querySelector("#artifactCount"),
  artifactList: document.querySelector("#artifactList"),
  artifactSearch: document.querySelector("#artifactSearch"),
  previewDialog: document.querySelector("#previewDialog"),
  previewTitle: document.querySelector("#previewTitle"),
  previewContent: document.querySelector("#previewContent"),
  previewPath: document.querySelector("#previewPath"),
  downloadLink: document.querySelector("#downloadLink"),
  closePreview: document.querySelector("#closePreview"),
  toast: document.querySelector("#toast"),
};

const statusIcons = {
  complete: "✓",
  failed: "×",
  blocked: "!",
  running: "↻",
  reused: "↗",
  degraded: "!",
  skipped: "—",
  cancelled: "■",
  pending: "·",
};

function serverById(serverId) {
  return servers.find((server) => server.id === serverId) || servers[0];
}

function apiUrl(path, serverId = state.activeServerId) {
  return `${serverById(serverId).apiBase}${path}`;
}

async function api(path, serverId = state.activeServerId) {
  const response = await fetch(apiUrl(path, serverId), { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function formatRelativeTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 5) return "Just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function formatDuration(value) {
  const seconds = Math.max(0, Number(value || 0));
  if (!seconds) return "";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  if (hours < 24) return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

function activeStage(run) {
  const steps = run.steps || [];
  const activeStep = steps.find((step) => ["failed", "blocked"].includes(step.status))
    || steps.find((step) => ["running", "degraded"].includes(step.status))
    || steps.find((step) => step.status === "pending");
  if (!activeStep) return null;
  const stageTotals = activeStep.stage_totals || [];
  const failedStage = stageTotals.find((stage) => ["failed", "blocked"].includes(stage.status));
  const runningStages = stageTotals.filter((stage) => stage.status === "running");
  return failedStage || runningStages[runningStages.length - 1] || activeStep;
}

function currentStageLabel(run) {
  if (run.status === "complete") return "Workflow complete";
  if (run.status === "cancelled") return "Workflow stopped";
  if (run.runtime?.current_stage) return run.runtime.current_stage;
  return activeStage(run)?.title || "Waiting for status";
}

function summaryProgress(run) {
  if (run.status === "complete") {
    return {
      completed: run.steps.length,
      total: run.steps.length,
      fraction: 1,
      estimated: false,
    };
  }
  if (run.runtime?.progress && Object.keys(run.runtime.progress).length) {
    const progress = run.runtime.progress;
    const fraction = Number(
      progress.fraction
      || (
        Number(progress.total || 0)
          ? Number(progress.completed || 0) / Number(progress.total)
          : 0
      ),
    );
    return {
      completed: Number(progress.completed || 0),
      total: Number(progress.total || 0),
      fraction: Math.max(0, Math.min(1, Number.isFinite(fraction) ? fraction : 0)),
      estimated: false,
    };
  }
  const progress = activeStage(run)?.progress || {};
  const fraction = Number(progress.fraction || 0);
  return {
    completed: Number(progress.completed || 0),
    total: Number(progress.total || 0),
    fraction: Math.max(0, Math.min(1, Number.isFinite(fraction) ? fraction : 0)),
    estimated: Boolean(progress.estimated),
  };
}

function estimatedEta(run, progress) {
  if (!progress.total || progress.completed >= progress.total) return null;
  if (Number.isFinite(Number(run.runtime?.eta_seconds))) {
    return Number(run.runtime.eta_seconds);
  }
  const key = [
    state.activeServerId,
    run.project,
    run.run_id,
    currentStageLabel(run),
  ].join(":");
  const now = Date.now();
  const previous = state.progressSamples.get(key);
  state.progressSamples.set(key, { completed: progress.completed, at: now });
  if (!previous || progress.completed <= previous.completed || now <= previous.at) return null;
  const rate = (progress.completed - previous.completed) / ((now - previous.at) / 1000);
  return rate > 0 ? (progress.total - progress.completed) / rate : null;
}

function activeErrorCount(steps) {
  const messages = new Set();
  const failedItems = new Set();
  const visit = (item, path) => {
    if (!item || typeof item !== "object") return;
    if (item.error) messages.add(String(item.error).trim());
    if (["failed", "blocked"].includes(item.status)) {
      failedItems.add(`${path}:${item.id || item.title || "unknown"}`);
    }
    for (const key of ["units", "substeps", "shards", "stage_totals"]) {
      (item[key] || []).forEach((child, index) => visit(child, `${path}/${key}/${index}`));
    }
  };
  (steps || []).forEach((step, index) => visit(step, `steps/${index}`));
  return messages.size || failedItems.size;
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`;
  return `${(size / 1024 ** 3).toFixed(1)} GB`;
}

function formatCount(value) {
  return new Intl.NumberFormat("en-US").format(Number(value || 0));
}

function projectStatusGroup(status) {
  if (["failed", "blocked", "degraded", "stalled"].includes(status)) return "failed";
  if (status === "running") return "running";
  if (status === "pending") return "pending";
  if (["complete", "reused", "skipped"].includes(status)) return "complete";
  return status;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  window.setTimeout(() => elements.toast.classList.remove("visible"), 3000);
}

function setLoading(loading) {
  elements.refreshButton.classList.toggle("loading", loading);
  elements.refreshButton.disabled = loading;
}

async function loadProjects() {
  await Promise.all(servers.map(async (server) => {
    try {
      const [health, payload] = await Promise.all([
        api("/api/health", server.id),
        api("/api/projects", server.id),
      ]);
      state.healthByServer[server.id] = { ...health, online: true };
      state.projectsByServer[server.id] = (payload.projects || []).map((project) => ({
        ...project,
        server_id: server.id,
        server_label: server.label,
      }));
    } catch (error) {
      state.healthByServer[server.id] = {
        online: false,
        error: error.message,
        server_id: server.id,
        server_label: server.label,
      };
      state.projectsByServer[server.id] = [];
    }
  }));

  renderServerSwitcher();
  const requestedServer = new URLSearchParams(window.location.search).get("server");
  state.serverFilter = requestedServer === "all" || serverById(requestedServer).id === requestedServer
    ? requestedServer
    : "ln";
  if (state.serverFilter !== "all" && !state.healthByServer[state.serverFilter]?.online) {
    state.serverFilter = servers.find((server) => state.healthByServer[server.id]?.online)?.id || "ln";
  }
  state.activeServerId = state.serverFilter === "all"
    ? servers.find((server) => state.healthByServer[server.id]?.online)?.id || "ln"
    : state.serverFilter;
  applyServerFilter();

  elements.ifldCount.textContent = state.projects.filter((item) => item.workflow_family === "ifld").length;
  elements.partialCount.textContent = state.projects.filter((item) => item.workflow_family === "partial_denovo").length;
  if (!state.projects.length) {
    elements.projectTitle.textContent = "No AuraPilot projects found";
    elements.projectMeta.textContent = "当前服务器没有可识别的项目，或采集服务不可访问";
    elements.pipeline.innerHTML = '<div class="empty-state">No project runs found.</div>';
    return;
  }
  const requested = new URLSearchParams(window.location.search).get("project");
  const requestedProject = state.projects.find((item) => (
    item.name === requested
    && (state.serverFilter === "all" || item.server_id === state.serverFilter)
  ));
  const currentProject = state.projects.find((item) => (
    item.name === state.project && item.server_id === state.activeServerId
  ));
  state.workflowFamily = requestedProject?.workflow_family
    || currentProject?.workflow_family
    || "ifld";
  renderWorkflowBranches();
  renderProjects();
  const familyProjects = state.projects.filter((item) => item.workflow_family === state.workflowFamily);
  const preferred = requestedProject
    || familyProjects.find((item) => item.name === "test_cd98_hu43f8c6_v8_ifld")
    || familyProjects[0];
  if (preferred) await selectProject(preferred.name, false, preferred.server_id);
}

function applyServerFilter() {
  state.projects = state.serverFilter === "all"
    ? servers.flatMap((server) => state.projectsByServer[server.id] || [])
    : state.projectsByServer[state.serverFilter] || [];
  renderServerSwitcher();
  renderServerMeta();
}

function renderServerSwitcher() {
  const total = servers.reduce(
    (count, server) => count + (state.projectsByServer[server.id]?.length || 0),
    0,
  );
  elements.allServerCount.textContent = total;
  elements.lnServerCount.textContent = state.healthByServer.ln?.online
    ? state.projectsByServer.ln?.length || 0
    : "×";
  elements.huoshanServerCount.textContent = state.healthByServer.huoshan?.online
    ? state.projectsByServer.huoshan?.length || 0
    : "×";
  elements.serverSwitcher.querySelectorAll("[data-server]").forEach((button) => {
    const serverId = button.dataset.server;
    button.classList.toggle("active", serverId === state.serverFilter);
    if (serverId !== "all") {
      button.classList.toggle("offline", !state.healthByServer[serverId]?.online);
    }
  });
}

function renderServerMeta() {
  if (state.serverFilter === "all") {
    elements.serverName.textContent = "All servers · Read-only";
    elements.projectRootPath.textContent = "LN + Huoshan A800";
    return;
  }
  const server = serverById(state.serverFilter);
  const health = state.healthByServer[state.serverFilter] || {};
  elements.serverName.textContent = `${server.label} · ${health.online ? "Read-only" : "Offline"}`;
  elements.projectRootPath.textContent = health.project_root || "/nfs/project";
}

function renderProjects() {
  const familyProjects = state.projects.filter(
    (project) => project.workflow_family === state.workflowFamily,
  );
  const counts = familyProjects.reduce((result, project) => {
    const group = projectStatusGroup(project.status);
    result[group] = (result[group] || 0) + 1;
    return result;
  }, {});
  elements.projectAllCount.textContent = familyProjects.length;
  elements.projectRunningCount.textContent = counts.running || 0;
  elements.projectFailedCount.textContent = counts.failed || 0;
  elements.projectPendingCount.textContent = counts.pending || 0;
  elements.projectCompleteCount.textContent = counts.complete || 0;
  elements.projectStatusFilters.querySelectorAll("[data-project-status]").forEach((button) => {
    button.classList.toggle("active", button.dataset.projectStatus === state.projectStatusFilter);
  });
  const query = state.projectSearch.trim().toLowerCase();
  const priority = { failed: 0, running: 1, pending: 2, cancelled: 3, complete: 4 };
  const projects = familyProjects
    .filter((project) => (
      (!query || project.name.toLowerCase().includes(query))
      && (
        state.projectStatusFilter === "all"
        || projectStatusGroup(project.status) === state.projectStatusFilter
      )
    ))
    .sort((left, right) => {
      const statusOrder = (priority[projectStatusGroup(left.status)] ?? 5)
        - (priority[projectStatusGroup(right.status)] ?? 5);
      if (statusOrder) return statusOrder;
      return String(right.updated_at || "").localeCompare(String(left.updated_at || ""));
    });
  elements.projectCount.textContent = `${projects.length} / ${familyProjects.length}`;
  if (!projects.length) {
    elements.projectList.innerHTML = '<div class="empty-state rail-empty">No matching projects.</div>';
    return;
  }
  elements.projectList.innerHTML = projects.map((project) => `
    <button class="project-button ${
      project.name === state.project && project.server_id === state.activeServerId
        ? "active"
        : ""
    }"
      type="button" data-project="${escapeHtml(project.name)}"
      data-project-server="${escapeHtml(project.server_id)}"
      title="${escapeHtml(project.name)}">
      <i class="${escapeHtml(project.status)}"></i>
      <span>
        <strong>${escapeHtml(project.name)}</strong>
        <span>${state.serverFilter === "all" ? `${escapeHtml(project.server_label)} · ` : ""}${escapeHtml(project.current_stage || project.latest_run)} · ${formatRelativeTime(project.updated_at)}</span>
        ${project.error ? `<span class="project-error" title="${escapeHtml(project.error)}">${escapeHtml(project.error)}</span>` : ""}
        ${project.progress?.total ? `
          <div class="project-progress" title="${formatCount(project.progress.completed)} / ${formatCount(project.progress.total)}">
            <i style="width:${Math.max(0, Math.min(1, Number(project.progress.fraction || 0))) * 100}%"></i>
          </div>
        ` : ""}
      </span>
    </button>
  `).join("");
  elements.projectList.querySelectorAll("[data-project]").forEach((button) => {
    button.addEventListener("click", () => (
      selectProject(button.dataset.project, true, button.dataset.projectServer)
    ));
  });
}

function renderWorkflowBranches() {
  elements.workflowBranches.querySelectorAll("[data-family]").forEach((button) => {
    button.classList.toggle("active", button.dataset.family === state.workflowFamily);
  });
}

async function selectProject(
  projectName,
  updateUrl = true,
  serverId = state.activeServerId,
) {
  if (!projectName) return;
  const project = state.projects.find((item) => (
    item.name === projectName && item.server_id === serverId
  )) || (state.projectsByServer[serverId] || []).find((item) => item.name === projectName);
  if (!project) return;
  state.activeServerId = serverId;
  if (updateUrl && state.serverFilter === "all") {
    state.serverFilter = serverId;
    applyServerFilter();
  }
  if (project) state.workflowFamily = project.workflow_family;
  state.project = projectName;
  state.runId = "";
  renderWorkflowBranches();
  renderProjects();
  if (updateUrl) {
    const url = new URL(window.location.href);
    url.searchParams.set("server", serverId);
    url.searchParams.set("project", projectName);
    url.searchParams.delete("run");
    window.history.replaceState({}, "", url);
  }
  const payload = await api(
    `/api/projects/${encodeURIComponent(projectName)}/runs`,
    serverId,
  );
  state.runs = payload.runs || [];
  renderRunSelect();
  if (!state.runs.length) return;
  const requested = new URLSearchParams(window.location.search).get("run");
  const selected = state.runs.find((run) => run.id === requested)
    || state.runs.find((run) => run.id === state.runId)
    || state.runs[0];
  await selectRun(selected.id, updateUrl);
}

function renderRunSelect() {
  elements.runSelect.innerHTML = state.runs.map((run) => `
    <option value="${escapeHtml(run.id)}">${escapeHtml(run.display_name || run.id)} · ${escapeHtml(run.status)}</option>
  `).join("");
}

async function selectRun(runId, updateUrl = true) {
  if (!runId) return;
  if (state.runId !== runId) {
    state.collapsedArtifactGroups.clear();
    state.expandedSteps.clear();
    state.expandedUnits.clear();
    state.expandedShardGroups.clear();
  }
  state.runId = runId;
  elements.runSelect.value = runId;
  if (updateUrl) {
    const url = new URL(window.location.href);
    url.searchParams.set("server", state.activeServerId);
    url.searchParams.set("project", state.project);
    url.searchParams.set("run", runId);
    window.history.replaceState({}, "", url);
  }
  await refreshRun();
}

async function refreshRun() {
  if (!state.project || !state.runId) return;
  setLoading(true);
  try {
    state.run = await api(
      `/api/projects/${encodeURIComponent(state.project)}/runs/${encodeURIComponent(state.runId)}/status`,
      state.activeServerId,
    );
    await loadCanonicalManifestArtifacts();
    renderRun();
  } catch (error) {
    showToast(error.message);
  } finally {
    setLoading(false);
  }
}

function renderRun() {
  const run = state.run;
  if (!run) return;
  elements.projectTitle.textContent = run.project;
  elements.projectMeta.textContent = `${serverById(state.activeServerId).label} · ${run.task || "Untitled task"} · ${run.run_path}`;
  const partial = run.workflow_family === "partial_denovo";
  elements.workflowEyebrow.textContent = partial
    ? "Partial de novo library / live filesystem state"
    : "IFLD workflow / live filesystem state";
  elements.pipelineTitle.textContent = partial
    ? "Six-step partial de novo workflow"
    : "Six-step IFLD workflow";
  elements.runSelectLabelText.textContent = partial ? "Workspace" : "Run";
  elements.workflowName.textContent = run.workflow;
  elements.profileName.textContent = run.profile || "Profile not recorded";
  const runtime = run.runtime || {};
  const displayedStatus = runtime.stalled ? "stalled" : run.status;
  elements.overallStatus.textContent = displayedStatus;
  elements.overallStatus.className = `status-pill ${runtime.stalled ? "blocked" : run.status}`;
  elements.runSummary.className = `run-summary ${runtime.stalled ? "blocked stalled" : run.status}`;
  const done = run.steps.filter((step) => ["complete", "reused", "skipped"].includes(step.status)).length;
  const progress = summaryProgress(run);
  const percentage = Math.round(progress.fraction * 100);
  const eta = estimatedEta(run, progress);
  const errors = activeErrorCount(run.steps);
  elements.currentStage.textContent = currentStageLabel(run);
  elements.workflowProgressBar.style.width = `${percentage}%`;
  elements.workflowProgressCount.textContent = progress.total
    ? `${progress.estimated ? "~" : ""}${formatCount(progress.completed)} / ${formatCount(progress.total)}`
    : "No count yet";
  elements.workflowProgressText.textContent = `${percentage}%`;
  elements.workflowProgress.setAttribute("aria-valuenow", String(percentage));
  const runtimeItems = [
    runtime.attempt ? `Attempt ${runtime.attempt}` : "",
    runtime.duration_seconds ? `Elapsed ${formatDuration(runtime.duration_seconds)}` : "",
    eta ? `ETA ~${formatDuration(eta)}` : progress.total && progress.completed < progress.total ? "ETA calculating" : "",
    runtime.progress_source === "reported" ? "Reported progress" : "Estimated from outputs",
  ].filter(Boolean);
  elements.runtimeMeta.innerHTML = runtimeItems.map((item, index) => `
    <span class="${index === runtimeItems.length - 1 ? `source ${escapeHtml(runtime.progress_source || "inferred")}` : ""}">${escapeHtml(item)}</span>
  `).join("");
  elements.stepsComplete.textContent = `${done} / ${run.steps.length}`;
  const activityAt = runtime.activity_at || run.updated_at;
  elements.activityLabel.textContent = runtime.progress_source === "reported"
    ? "Last heartbeat"
    : "Last activity";
  elements.lastUpdated.textContent = formatRelativeTime(activityAt);
  elements.lastUpdated.title = formatTime(activityAt);
  elements.activeErrors.textContent = formatCount(errors);
  elements.activeErrorFact.classList.toggle("has-errors", errors > 0);
  const projectRecord = (state.projectsByServer[state.activeServerId] || []).find(
    (project) => project.name === run.project,
  );
  if (projectRecord) {
    Object.assign(projectRecord, {
      status: displayedStatus,
      updated_at: activityAt,
      current_stage: currentStageLabel(run),
      progress,
      error: collectSideErrors()[0]?.message || "",
      progress_source: runtime.progress_source || "inferred",
      stalled: Boolean(runtime.stalled),
    });
    renderProjects();
  }
  renderPipeline(run.steps);
  renderArtifacts();
}

function renderPipeline(steps) {
  const renderMetrics = (stage) => stage.metrics?.length ? `
    <div class="stage-metrics">
      ${stage.metrics.map((metric) => `
        <span class="${escapeHtml(metric.tone || "")}">
          <strong>${formatCount(metric.value)}</strong>
          ${escapeHtml(metric.label)}
        </span>
      `).join("")}
    </div>
  ` : "";

  const renderStage = (stage, className = "") => {
    const stageFraction = Math.max(0, Math.min(1, Number(stage.progress?.fraction || 0)));
    return `
      <div class="unit-stage ${escapeHtml(stage.status)} ${className}">
        <div>
          <i>${statusIcons[stage.status] || "·"}</i>
          <strong title="${escapeHtml(stage.title)}">${escapeHtml(stage.title)}</strong>
        </div>
        <span>${stage.progress?.estimated ? "~" : ""}${formatCount(stage.progress?.completed)} / ${formatCount(stage.progress?.total)}</span>
        <div class="stage-mini-progress"><span style="width:${stageFraction * 100}%"></span></div>
        ${stage.result_count ? `<small>${formatCount(stage.result_count)} ${escapeHtml(stage.result_label)}</small>` : ""}
        ${renderMetrics(stage)}
        ${stage.error ? `<div class="stage-error">${escapeHtml(stage.error)}</div>` : ""}
      </div>
    `;
  };

  const renderShard = (shard) => {
    const fraction = Math.max(0, Math.min(1, Number(shard.progress?.fraction || 0)));
    const assignedGpus = shard.assigned_gpus?.length
      ? `GPU ${shard.assigned_gpus.join(", ")}`
      : "";
    const metadata = [assignedGpus, shard.dependency ? `after ${shard.dependency}` : "", shard.phase || ""]
      .filter(Boolean)
      .join(" · ");
    return `
      <div class="shard-card ${escapeHtml(shard.status)} ${shard.unavailable ? "unavailable" : ""}">
        <div class="shard-heading">
          <i>${statusIcons[shard.status] || "·"}</i>
          <strong>${escapeHtml(shard.title || shard.id)}</strong>
          <span class="status-pill ${escapeHtml(shard.status)}">${escapeHtml(shard.status)}</span>
        </div>
        ${metadata ? `<div class="shard-metadata">${escapeHtml(metadata)}</div>` : ""}
        ${shard.unavailable ? `
          <div class="shard-unavailable">${escapeHtml(shard.note || "Per-GPU counters are not recorded.")}</div>
        ` : `
          <div class="shard-progress"><span style="width:${fraction * 100}%"></span></div>
          <div class="shard-counts">
            <span><strong>${formatCount(shard.input_ready)}</strong> input</span>
            <span><strong>${formatCount(shard.msa_ready)}</strong> MSA</span>
            <span><strong>${formatCount(shard.refolded)}</strong> refolded</span>
            <span><strong>${formatCount(shard.analyzed)}</strong> analyzed</span>
          </div>
          <small>${formatCount(shard.progress?.completed)} / ${formatCount(shard.progress?.total)} designs</small>
        `}
        ${shard.error ? `<div class="shard-error">${escapeHtml(shard.error)}</div>` : ""}
      </div>
    `;
  };

  const collectDisplayStages = (step) => {
    const supplied = step.stage_totals || [];
    const definitions = [];
    const seen = new Set();
    const addDefinition = (stage) => {
      if (!stage?.id || seen.has(stage.id)) return;
      seen.add(stage.id);
      definitions.push(stage);
    };
    supplied.forEach(addDefinition);
    (step.units || []).forEach((unit) => (unit.substeps || []).forEach(addDefinition));

    return definitions.map((definition) => {
      const existing = supplied.find((stage) => stage.id === definition.id);
      if (existing) return existing;

      const instances = (step.units || [])
        .flatMap((unit) => unit.substeps || [])
        .filter((stage) => stage.id === definition.id);
      const statuses = instances.map((stage) => stage.status || "pending");
      let status = "pending";
      if (statuses.includes("failed")) status = "failed";
      else if (statuses.includes("blocked")) status = "blocked";
      else if (statuses.includes("degraded")) status = "degraded";
      else if (statuses.includes("running")) status = "running";
      else if (statuses.length && statuses.every((value) => ["complete", "skipped", "cancelled"].includes(value))) {
        status = statuses.includes("complete") ? "complete" : "skipped";
      }

      const completed = instances.reduce((sum, stage) => sum + Number(stage.progress?.completed || 0), 0);
      const total = instances.reduce((sum, stage) => sum + Number(stage.progress?.total || 0), 0);
      const resultCount = instances.reduce((sum, stage) => sum + Number(stage.result_count || 0), 0);
      return {
        ...definition,
        status,
        progress: {
          completed,
          total,
          fraction: total ? completed / total : 0,
          estimated: instances.some((stage) => stage.progress?.estimated),
        },
        result_count: resultCount,
      };
    });
  };

  const renderUnit = (step, unit) => {
    const hasNestedDetails = Boolean(unit.substeps?.length || unit.shards?.length);
    if (!hasNestedDetails) return `
      <div class="unit-row">
        <i class="${escapeHtml(unit.status)}"></i>
        <strong title="${escapeHtml(unit.id)}">${escapeHtml(unit.title || unit.id)}</strong>
        <span>${escapeHtml(unit.host || "")}${unit.gpu !== "" && unit.gpu != null ? ` · GPU ${escapeHtml(unit.gpu)}` : ""}</span>
        <span>${escapeHtml(unit.status)}</span>
        ${unit.error ? `<div class="unit-error">${escapeHtml(unit.error)}</div>` : ""}
      </div>
    `;

    const unitKey = `${step.id}:${unit.id}`;
    const unitExpanded = state.expandedUnits.has(unitKey);
    const shardExpanded = state.expandedShardGroups.has(unitKey);
    const shardCount = unit.shards?.length || 0;
    return `
      <div class="unit-card multi-stage-unit ${unitExpanded ? "expanded" : ""}">
        <button class="unit-card-heading multi-stage-unit-toggle" type="button"
          data-unit-key="${escapeHtml(unitKey)}"
          aria-expanded="${unitExpanded ? "true" : "false"}">
          <i class="${escapeHtml(unit.status)}"></i>
          <strong title="${escapeHtml(unit.id)}">${escapeHtml(unit.title || unit.id)}</strong>
          <span>${escapeHtml(unit.current_stage || unit.status)}</span>
          <small class="status-pill ${escapeHtml(unit.status)}">${escapeHtml(unit.status)}</small>
          <b aria-hidden="true">${unitExpanded ? "▴" : "▾"}</b>
        </button>
        <div class="multi-stage-unit-details">
          <div class="unit-stage-grid downstream-stage-grid">
            ${(unit.substeps || []).map((stage) => renderStage(stage, "downstream-stage")).join("")}
          </div>
          ${shardCount ? `
            <button class="shard-toggle" type="button"
              data-shard-key="${escapeHtml(unitKey)}"
              aria-expanded="${shardExpanded ? "true" : "false"}">
              <span>${escapeHtml(unit.shard_label || "AF3 GPU shards")}</span>
              <strong>${shardCount}</strong>
              <small>${shardExpanded ? "Hide details ▴" : "Show details ▾"}</small>
            </button>
            ${unit.shard_note ? `<p class="shard-note">${escapeHtml(unit.shard_note)}</p>` : ""}
            <div class="shard-grid ${shardExpanded ? "expanded" : ""}">
              ${unit.shards.map(renderShard).join("")}
            </div>
          ` : ""}
          ${unit.error ? `<div class="unit-error">${escapeHtml(unit.error)}</div>` : ""}
        </div>
      </div>
    `;
  };

  const renderStageMatrix = (step, displayStages) => {
    const units = (step.units || []).filter((unit) => unit.substeps?.length);
    if (!units.length || !displayStages.length) return "";
    const stages = displayStages;
    const abbreviate = (title) => ({
      "Input validation": "Input",
      "Structure prediction": "Structure",
      "TAP analysis": "TAP",
      "pI analysis": "pI",
      "BioPhi / Humanness": "BioPhi",
      "Liability analysis": "Liability",
      "Solubility analysis": "Solubility",
      "Developability filter": "Dev filter",
      "AF3 worker preparation": "Workers",
      "AF3 input & MSA": "MSA",
      "AF3 refolding": "AF3",
      "AF3 scoring & RMSD": "RMSD",
      "Node result validation": "Validate",
    }[title] || title);
    const columns = `minmax(88px, 1.4fr) repeat(${stages.length}, minmax(40px, 1fr))`;
    return `
      <div class="stage-matrix" aria-label="Server or execution unit by stage status matrix">
        <div class="detail-section-label">Server / execution-unit status</div>
        <div class="stage-matrix-grid" style="grid-template-columns:${columns}">
          <div class="stage-matrix-cell header">Server / unit</div>
          ${stages.map((stage) => `
            <div class="stage-matrix-cell header" title="${escapeHtml(stage.title)}">${escapeHtml(abbreviate(stage.title))}</div>
          `).join("")}
          ${units.map((unit) => `
            <div class="stage-matrix-cell node" title="${escapeHtml([unit.title || unit.id, unit.host, unit.gpu !== "" && unit.gpu != null ? `GPU ${unit.gpu}` : ""].filter(Boolean).join(" · "))}">${escapeHtml(unit.title || unit.id)}</div>
            ${stages.map((definition) => {
              const stage = unit.substeps.find((item) => item.id === definition.id);
              if (!stage) return `
                <div class="stage-matrix-cell not-recorded"
                  title="${escapeHtml(`${unit.title || unit.id} · ${definition.title} · not recorded for this unit`)}">
                  —
                </div>
              `;
              const completed = Number(stage.progress?.completed || 0);
              const total = Number(stage.progress?.total || 0);
              return `
                <button class="stage-matrix-cell ${escapeHtml(stage.status)}" type="button"
                  data-matrix-unit="${escapeHtml(`${step.id}:${unit.id}`)}"
                  title="${escapeHtml(`${unit.title || unit.id} · ${stage.title || definition.title} · ${stage.status}${total ? ` · ${formatCount(completed)} / ${formatCount(total)}` : ""}`)}">
                  ${statusIcons[stage.status] || "·"}
                </button>
              `;
            }).join("")}
          `).join("")}
        </div>
      </div>
    `;
  };

  elements.pipeline.innerHTML = steps.map((step) => {
    const fraction = Math.max(0, Math.min(1, Number(step.progress?.fraction || 0)));
    const unitCount = step.units?.length || 0;
    const progressLabel = step.progress?.total
      ? `${step.progress.completed || 0} / ${step.progress.total}`
      : "";
    const error = step.error ? `
      <div class="step-error">
        <strong>Error reason</strong>
        ${escapeHtml(step.error)}
      </div>
    ` : "";
    const reused = step.reused_from ? `
      <div class="step-error" style="border-color: var(--purple); background: var(--purple-soft); color: var(--purple)">
        <strong>Reused source</strong>
        ${escapeHtml(step.reused_from)}
      </div>
    ` : "";
    const displayStages = collectDisplayStages(step);
    const stageMatrix = renderStageMatrix(step, displayStages);
    const stageTotals = displayStages.length ? `
      <div class="stage-overview">
        <div class="detail-section-label">Substep summary</div>
        ${displayStages.map((stage) => {
          const stageFraction = Math.max(0, Math.min(1, Number(stage.progress?.fraction || 0)));
          return `
            <div class="stage-overview-item ${escapeHtml(stage.status)}">
              <div>
                <i>${statusIcons[stage.status] || "·"}</i>
                <strong title="${escapeHtml(stage.title)}">${escapeHtml(stage.title)}</strong>
              </div>
              <span>${stage.progress?.estimated ? "~" : ""}${formatCount(stage.progress?.completed)} / ${formatCount(stage.progress?.total)}</span>
              <div class="stage-mini-progress"><span style="width:${stageFraction * 100}%"></span></div>
              ${stage.result_count ? `<small>${formatCount(stage.result_count)} ${escapeHtml(stage.result_label)}</small>` : ""}
              ${renderMetrics(stage)}
            </div>
          `;
        }).join("")}
      </div>
    ` : "";
    const units = unitCount ? `
      <div class="unit-list">
        <div class="detail-section-label">Server / execution-unit details</div>
        ${step.units.map((unit) => renderUnit(step, unit)).join("")}
      </div>
    ` : "";
    const expanded = state.expandedSteps.has(step.id);
    const hasDetails = Boolean(unitCount || displayStages.length);
    const hasMultiStageUnits = (step.units || []).some((unit) => unit.substeps?.length);
    const detailLabel = unitCount
      ? `${unitCount} execution ${unitCount === 1 ? "unit" : "units"}`
      : `${displayStages.length} ${displayStages.length === 1 ? "substep" : "substeps"}`;
    return `
      <article class="step-card ${expanded ? "expanded" : ""} ${hasDetails ? "has-details" : ""} ${hasMultiStageUnits ? "multi-stage-detail" : ""}" data-step="${escapeHtml(step.id)}">
        <div class="step-icon ${escapeHtml(step.status)}">${statusIcons[step.status] || "·"}</div>
        <div class="step-main ${hasDetails ? "step-detail-trigger" : ""}"
          ${hasDetails ? `data-step-toggle="${escapeHtml(step.id)}" role="button" tabindex="0" aria-expanded="${expanded ? "true" : "false"}"` : ""}>
          <span class="step-kicker">Step ${step.number}</span>
          <h3>${escapeHtml(step.title)}</h3>
          <p class="step-summary">${escapeHtml(step.summary || step.description)}</p>
          ${step.progress && Object.keys(step.progress).length ? `
            <div class="step-progress" title="${escapeHtml(progressLabel)}">
              <span style="width:${fraction * 100}%"></span>
            </div>
          ` : ""}
        </div>
        <div class="step-side">
          <span class="status-pill ${escapeHtml(step.status)}">${escapeHtml(step.status)}</span>
          ${hasDetails ? `<button class="unit-toggle" data-step-toggle="${escapeHtml(step.id)}" type="button">${detailLabel} ${expanded ? "▴" : "▾"}</button>` : ""}
        </div>
        ${error}
        ${reused}
        ${stageTotals}
        ${stageMatrix}
        ${units}
      </article>
    `;
  }).join("");

  const toggleStep = (stepId) => {
    if (state.expandedSteps.has(stepId)) state.expandedSteps.delete(stepId);
    else state.expandedSteps.add(stepId);
    renderPipeline(state.run?.steps || steps);
  };
  elements.pipeline.querySelectorAll("[data-step-toggle]").forEach((trigger) => {
    trigger.addEventListener("click", () => toggleStep(trigger.dataset.stepToggle));
    if (trigger.tagName !== "BUTTON") {
      trigger.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggleStep(trigger.dataset.stepToggle);
        }
      });
    }
  });
  elements.pipeline.querySelectorAll("[data-unit-key]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.unitKey;
      if (state.expandedUnits.has(key)) state.expandedUnits.delete(key);
      else state.expandedUnits.add(key);
      renderPipeline(state.run?.steps || steps);
    });
  });
  elements.pipeline.querySelectorAll("[data-matrix-unit]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.matrixUnit;
      state.expandedUnits.add(key);
      renderPipeline(state.run?.steps || steps);
    });
  });
  elements.pipeline.querySelectorAll("[data-shard-key]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.shardKey;
      if (state.expandedShardGroups.has(key)) state.expandedShardGroups.delete(key);
      else state.expandedShardGroups.add(key);
      renderPipeline(state.run?.steps || steps);
    });
  });
}

function collectSideErrors() {
  const records = [];
  const seen = new Set();
  const visit = (item, context) => {
    if (!item || typeof item !== "object") return;
    const title = item.title || item.id || context;
    if (item.error) {
      const key = `${item.error}:${item.log_path || ""}`;
      if (!seen.has(key)) {
        seen.add(key);
        records.push({
          title,
          message: String(item.error),
          path: item.log_path || "",
          status: item.status || "failed",
        });
      }
    }
    for (const key of ["units", "substeps", "shards", "stage_totals"]) {
      (item[key] || []).forEach((child) => visit(child, title));
    }
  };
  (state.run?.steps || []).forEach((step) => visit(step, step.title));
  return records;
}

function collectSideLogs() {
  const records = [];
  const seen = new Set();
  const add = (path, title, status = "") => {
    if (!path || seen.has(path)) return;
    seen.add(path);
    records.push({ path, title, status });
  };
  const visit = (item, context) => {
    if (!item || typeof item !== "object") return;
    const title = item.title || item.id || context;
    add(item.log_path, title, item.status);
    (item.evidence || [])
      .filter((path) => /\.(log|out|err|txt)$/i.test(path))
      .forEach((path) => add(path, title, item.status));
    for (const key of ["units", "substeps", "shards"]) {
      (item[key] || []).forEach((child) => visit(child, title));
    }
  };
  (state.run?.steps || []).forEach((step) => visit(step, step.title));
  return records;
}

function renderSideMessages(records, kind) {
  if (!records.length) {
    elements.artifactList.innerHTML = `
      <div class="empty-state">${kind === "errors" ? "No active errors." : "No workflow logs recorded."}</div>
    `;
    return;
  }
  elements.artifactList.innerHTML = `
    <div class="side-message-list">
      ${records.map((record) => {
        const tag = record.path ? "button" : "div";
        return `
          <${tag} class="side-message ${kind === "errors" ? "error" : "log"}"
            ${record.path ? `type="button" data-side-path="${escapeHtml(record.path)}"` : ""}>
            <div class="side-message-heading">
              <strong>${escapeHtml(record.title)}</strong>
              <small>${escapeHtml(record.status || "")}</small>
            </div>
            ${record.message ? `<p>${escapeHtml(record.message)}</p>` : ""}
            ${record.path ? `<div class="side-message-path" title="${escapeHtml(record.path)}">${escapeHtml(record.path)}</div>` : ""}
          </${tag}>
        `;
      }).join("")}
    </div>
  `;
  elements.artifactList.querySelectorAll("[data-side-path]").forEach((button) => {
    button.addEventListener("click", () => previewArtifact(button.dataset.sidePath));
  });
}

function appendAttemptHistory() {
  const attempts = state.run?.runtime?.attempts || [];
  if (!attempts.length) return;
  elements.artifactList.insertAdjacentHTML("beforeend", `
    <section class="attempt-history">
      <h3>Attempt history</h3>
      ${[...attempts].reverse().map((attempt) => `
        <div class="attempt-history-item">
          <div>
            <strong>Attempt ${escapeHtml(attempt.attempt)}</strong>
            <span class="status-pill ${escapeHtml(attempt.status || "pending")}">${escapeHtml(attempt.status || "unknown")}</span>
          </div>
          <small>${escapeHtml(attempt.started_at || "Unknown start")}${attempt.finished_at ? ` → ${escapeHtml(attempt.finished_at)}` : ""}</small>
          ${attempt.error ? `<p>${escapeHtml(attempt.error)}</p>` : ""}
        </div>
      `).join("")}
    </section>
  `);
}

function manifestArtifact(path, {
  key = "",
  size = null,
  sha256 = "",
  manifestPath = "",
  isManifest = false,
  listedByManifest = true,
} = {}) {
  const runPath = state.run?.run_path || "";
  const name = path.split("/").pop() || path;
  const extension = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
  const relativePath = runPath && path.startsWith(`${runPath}/`)
    ? path.slice(runPath.length + 1)
    : path;
  return {
    name,
    path,
    relative_path: relativePath,
    size,
    updated_at: state.run?.updated_at || "",
    extension,
    previewable: ["json", "yaml", "yml", "txt", "md"].includes(extension),
    important: true,
    step_id: "05_final_library",
    step_number: 6,
    step_label: "Step 6",
    purpose: "Final result",
    source_run: state.run?.run_id || "",
    manifest_key: key,
    manifest_path: manifestPath,
    sha256,
    exists: true,
    empty: size === 0,
    missing: false,
    is_manifest: isManifest,
    artifact_source: "canonical_manifest",
    listed_by_manifest: listedByManifest,
  };
}

async function loadCanonicalManifestArtifacts() {
  state.manifestArtifacts = [];
  const runPath = state.run?.run_path || "";
  if (!runPath || state.run?.workflow_family !== "partial_denovo") return;

  const manifestPath = `${runPath}/steps/06_partial_library/full_downstream_manifest.json`;
  try {
    const payload = await api(
      `/api/file?path=${encodeURIComponent(manifestPath)}`,
      state.activeServerId,
    );
    const manifest = JSON.parse(payload.content);
    const outputs = manifest.outputs;
    if (!outputs || typeof outputs !== "object" || Array.isArray(outputs)) return;

    const encodedManifestSize = new TextEncoder().encode(payload.content || "").length;
    const artifacts = [
      manifestArtifact(manifestPath, {
        key: "full_downstream_manifest",
        size: encodedManifestSize,
        manifestPath,
        isManifest: true,
        listedByManifest: false,
      }),
    ];
    const hashes = manifest.sha256 && typeof manifest.sha256 === "object"
      ? manifest.sha256
      : {};
    Object.entries(outputs).forEach(([key, path]) => {
      if (typeof path !== "string" || !path.startsWith(`${runPath}/`)) return;
      const name = path.split("/").pop() || path;
      const sha256 = typeof hashes[name] === "string" ? hashes[name] : "";
      artifacts.push(manifestArtifact(path, {
        key,
        size: sha256 === "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
          ? 0
          : null,
        sha256,
        manifestPath,
        isManifest: name.toLowerCase().includes("manifest"),
      }));
    });
    state.manifestArtifacts = artifacts;
  } catch (error) {
    // Incomplete runs may not have a final manifest yet.
    state.manifestArtifacts = [];
  }
}

function renderArtifacts() {
  const errors = collectSideErrors();
  const logs = collectSideLogs();
  elements.sideErrorCount.textContent = errors.length;
  elements.sideLogCount.textContent = logs.length;
  elements.sidePanelTabs.querySelectorAll("[data-side-tab]").forEach((button) => {
    const active = button.dataset.sideTab === state.sidePanelTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  elements.artifactControls.hidden = state.sidePanelTab !== "outputs";
  if (state.sidePanelTab === "errors") {
    elements.sidePanelTitle.textContent = "Active errors";
    elements.artifactCount.textContent = `${errors.length} active`;
    renderSideMessages(errors, "errors");
    appendAttemptHistory();
    return;
  }
  if (state.sidePanelTab === "logs") {
    elements.sidePanelTitle.textContent = "Workflow logs";
    elements.artifactCount.textContent = `${logs.length} logs`;
    renderSideMessages(logs, "logs");
    return;
  }
  elements.sidePanelTitle.textContent = "Workflow deliverables";
  const listed = state.run?.artifacts || [];
  const knownPaths = new Set(listed.map((artifact) => artifact.path));
  const all = [
    ...listed,
    ...state.manifestArtifacts.filter((artifact) => !knownPaths.has(artifact.path)),
  ];
  const query = state.artifactSearch.toLowerCase();
  const visible = all.filter((artifact) => (
    !query
    || artifact.relative_path.toLowerCase().includes(query)
    || artifact.step_label.toLowerCase().includes(query)
  ));
  elements.artifactCount.textContent = `${all.length} files`;
  if (!visible.length) {
    elements.artifactList.innerHTML = '<div class="empty-state">No matching workflow deliverables.</div>';
    return;
  }
  const groups = visible.reduce((result, artifact) => {
    (result[artifact.step_id] ||= []).push(artifact);
    return result;
  }, {});
  elements.artifactList.innerHTML = Object.values(groups).map((artifacts) => {
    const first = artifacts[0];
    const collapsed = state.collapsedArtifactGroups.has(first.step_id);
    return `
      <section class="artifact-group${collapsed ? " collapsed" : ""}">
        <button class="artifact-group-heading" type="button"
          data-artifact-group="${escapeHtml(first.step_id)}"
          aria-expanded="${collapsed ? "false" : "true"}">
          <span>${escapeHtml(first.step_label)}</span>
          <strong>${escapeHtml(first.purpose)}</strong>
          <span class="artifact-group-meta">
            <small class="artifact-group-count">${artifacts.length}</small>
            <span class="artifact-group-chevron" aria-hidden="true">▴</span>
          </span>
        </button>
        <div class="artifact-group-files">
          ${artifacts.map((artifact) => {
            const sourceLabel = artifact.is_manifest
              ? "Manifest file"
              : artifact.listed_by_manifest
                ? "Listed by manifest"
                : "";
            const sizeLabel = artifact.missing
              ? "Missing"
              : artifact.size === null || artifact.size === undefined
                ? "Size unavailable"
                : Number(artifact.size) === 0
                  ? "0 B · Empty"
                  : formatBytes(artifact.size);
            return `
              <button class="artifact-item ${artifact.missing ? "missing" : ""}" type="button"
                data-path="${escapeHtml(artifact.path)}"
                data-previewable="${artifact.previewable && !artifact.missing ? "1" : "0"}"
                ${artifact.missing ? "disabled" : ""}>
                <span class="file-icon">${escapeHtml(artifact.extension.slice(0, 4))}</span>
                <span>
                  <strong title="${escapeHtml(artifact.name)}">${escapeHtml(artifact.name)}</strong>
                  <span title="${escapeHtml(artifact.relative_path)}">${
                    artifact.source_run && artifact.source_run !== state.run.run_id
                      ? `From ${escapeHtml(artifact.source_run)} · `
                      : ""
                  }${escapeHtml(artifact.relative_path)}${
                    sourceLabel ? ` · ${escapeHtml(sourceLabel)}` : ""
                  }</span>
                </span>
                <small class="${artifact.missing ? "missing" : ""}">${escapeHtml(sizeLabel)}</small>
              </button>
            `;
          }).join("")}
        </div>
      </section>
    `;
  }).join("");
  elements.artifactList.querySelectorAll("[data-artifact-group]").forEach((button) => {
    button.addEventListener("click", () => {
      const groupId = button.dataset.artifactGroup;
      if (state.collapsedArtifactGroups.has(groupId)) {
        state.collapsedArtifactGroups.delete(groupId);
      } else {
        state.collapsedArtifactGroups.add(groupId);
      }
      renderArtifacts();
    });
  });
  elements.artifactList.querySelectorAll("[data-path]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.previewable === "1") {
        previewArtifact(button.dataset.path);
      } else {
        window.location.href = apiUrl(
          `/api/file?download=1&path=${encodeURIComponent(button.dataset.path)}`,
        );
      }
    });
  });
}

async function previewArtifact(path) {
  try {
    const payload = await api(`/api/file?path=${encodeURIComponent(path)}`);
    elements.previewTitle.textContent = payload.name;
    elements.previewContent.textContent = payload.content + (payload.truncated ? "\n\n… preview truncated …" : "");
    elements.previewPath.textContent = payload.path;
    elements.downloadLink.href = apiUrl(
      `/api/file?download=1&path=${encodeURIComponent(path)}`,
    );
    elements.previewDialog.showModal();
  } catch (error) {
    showToast(error.message);
  }
}

elements.runSelect.addEventListener("change", () => selectRun(elements.runSelect.value, true));
elements.projectSearch.addEventListener("input", () => {
  state.projectSearch = elements.projectSearch.value;
  renderProjects();
});
elements.projectStatusFilters.querySelectorAll("[data-project-status]").forEach((button) => {
  button.addEventListener("click", () => {
    state.projectStatusFilter = button.dataset.projectStatus;
    renderProjects();
  });
});
elements.sidePanelTabs.querySelectorAll("[data-side-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    state.sidePanelTab = button.dataset.sideTab;
    renderArtifacts();
  });
});
elements.artifactPanelToggle.addEventListener("click", () => {
  state.artifactPanelCollapsed = !state.artifactPanelCollapsed;
  elements.artifactPanel.classList.toggle("collapsed", state.artifactPanelCollapsed);
  elements.contentGrid.classList.toggle("artifact-panel-collapsed", state.artifactPanelCollapsed);
  elements.artifactPanelToggle.setAttribute(
    "aria-expanded",
    state.artifactPanelCollapsed ? "false" : "true",
  );
  elements.artifactPanelToggle.setAttribute(
    "aria-label",
    state.artifactPanelCollapsed ? "Expand side panel" : "Collapse side panel",
  );
});
elements.serverSwitcher.querySelectorAll("[data-server]").forEach((button) => {
  button.addEventListener("click", async () => {
    const serverId = button.dataset.server;
    if (serverId !== "all" && !state.healthByServer[serverId]?.online) {
      showToast(`${serverById(serverId).label} collector is offline`);
      return;
    }
    if (serverId === state.serverFilter) return;
    state.serverFilter = serverId;
    if (serverId !== "all") state.activeServerId = serverId;
    state.project = "";
    state.runId = "";
    state.run = null;
    applyServerFilter();
    elements.ifldCount.textContent = state.projects.filter(
      (item) => item.workflow_family === "ifld"
    ).length;
    elements.partialCount.textContent = state.projects.filter(
      (item) => item.workflow_family === "partial_denovo"
    ).length;
    const familyProjects = state.projects.filter(
      (item) => item.workflow_family === state.workflowFamily
    );
    if (!familyProjects.length && state.projects.length) {
      state.workflowFamily = state.projects[0].workflow_family;
    }
    renderWorkflowBranches();
    renderProjects();
    const url = new URL(window.location.href);
    url.searchParams.set("server", serverId);
    url.searchParams.delete("project");
    url.searchParams.delete("run");
    window.history.replaceState({}, "", url);
    const preferred = state.projects.find(
      (item) => item.workflow_family === state.workflowFamily
    ) || state.projects[0];
    if (preferred) {
      await selectProject(preferred.name, false, preferred.server_id);
    } else {
      elements.projectTitle.textContent = "No projects found";
      elements.projectMeta.textContent = "当前服务器没有可识别的 AuraPilot 运行";
      elements.pipeline.innerHTML = '<div class="empty-state">No project runs found.</div>';
      renderArtifacts();
    }
  });
});
elements.workflowBranches.querySelectorAll("[data-family]").forEach((button) => {
  button.addEventListener("click", async () => {
    if (button.dataset.family === state.workflowFamily) return;
    state.workflowFamily = button.dataset.family;
    state.project = "";
    state.runId = "";
    renderWorkflowBranches();
    renderProjects();
    const first = state.projects.find((item) => item.workflow_family === state.workflowFamily);
    if (first) await selectProject(first.name, true, first.server_id);
  });
});
elements.refreshButton.addEventListener("click", refreshRun);
elements.artifactSearch.addEventListener("input", () => {
  state.artifactSearch = elements.artifactSearch.value;
  renderArtifacts();
});
elements.closePreview.addEventListener("click", () => elements.previewDialog.close());
elements.previewDialog.addEventListener("click", (event) => {
  if (event.target === elements.previewDialog) elements.previewDialog.close();
});

loadProjects().catch((error) => showToast(error.message));
state.timer = window.setInterval(refreshRun, 5000);
