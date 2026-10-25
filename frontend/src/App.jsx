import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Bell,
  Check,
  ChevronDown,
  CircleHelp,
  Clock3,
  Cpu,
  Gauge,
  LayoutDashboard,
  ListFilter,
  MoreHorizontal,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Search,
  Settings2,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  TerminalSquare,
  X,
} from "lucide-react";
import { fetchDashboardData, fetchPriorityActions, fetchPriorityData, setMonitoringEnabled, updateSettings } from "./api.js";

const demoProcesses = [
  { pid: 2381, name: "python · model_train.py", cpu: 78.4, nice: 5, state: "High usage" },
  { pid: 1456, name: "code", cpu: 24.8, nice: 0, state: "Normal" },
  { pid: 3290, name: "chrome", cpu: 18.2, nice: 0, state: "Normal" },
  { pid: 874, name: "postgres", cpu: 11.6, nice: 0, state: "Normal" },
  { pid: 1924, name: "node · vite", cpu: 8.3, nice: 0, state: "Normal" },
  { pid: 1021, name: "gnome-shell", cpu: 3.9, nice: -5, state: "Normal" },
];

const demoActivity = [
  { time: "10:42:18", tone: "amber", title: "High CPU threshold crossed", detail: "python · model_train.py · PID 2381", reason: "HIGH_CPU" },
  { time: "10:41:55", tone: "green", title: "Process sample recorded", detail: "chrome · PID 3290 · 18.2% CPU", reason: "SAMPLE" },
  { time: "10:41:42", tone: "blue", title: "Monitoring session started", detail: "Polling every 5 seconds", reason: "MONITOR_START" },
  { time: "10:40:30", tone: "green", title: "Process sample recorded", detail: "postgres · PID 874 · 11.6% CPU", reason: "SAMPLE" },
];

const chartValues = [30, 36, 33, 43, 39, 48, 44, 41, 52, 49, 57, 48, 56, 47, 43, 51, 46, 61, 53, 58, 51, 55, 48, 53];
function getChartGeometry(values) {
  const points = values.map((value, index) => {
    const x = values.length === 1 ? 640 : (index / (values.length - 1)) * 640;
    return `${x},${132 - value * 1.2}`;
  }).join(" ");
  return { line: points, area: `0,132 ${points} 640,132` };
}

function Brand({ connectionState, demoMode }) {
  return (
    <a aria-label="ProcNanny dashboard" className="brand" href="#overview">
      <span className="brand-icon"><Activity size={19} strokeWidth={2.6} /></span>
      <span className="brand-name">proc<span>nanny</span><i>.</i></span>
      <span className="brand-version">{demoMode ? "DEMO" : connectionState === "connected" ? "LIVE" : "LAB"}</span>
    </a>
  );
}

function Sidebar({ onDemoInfo, connectionState, demoMode, processCount, activePage }) {
  return (
    <aside className="sidebar">
      <Brand connectionState={connectionState} demoMode={demoMode} />
      <div className="workspace-switch">
        <span className="workspace-avatar">OS</span>
        <span className="workspace-copy"><strong>Operating Systems</strong><small>Lab workspace</small></span>
        <ChevronDown size={14} />
      </div>
      <span className="nav-label">WORKSPACE</span>
      <nav aria-label="Main navigation">
        <a className={`nav-item ${activePage === "overview" ? "active" : ""}`} href="#overview"><LayoutDashboard size={17} />Overview{activePage === "overview" && <span className="nav-current" />}</a>
        <a className="nav-item" href="#processes"><Cpu size={17} />Processes<span className="nav-count">{processCount.toString().padStart(2, "0")}</span></a>
        <a className="nav-item" href="#activity"><Activity size={17} />Activity log</a>
        <a className={`nav-item ${activePage === "priority-management" ? "active" : ""}`} href="#priority-management"><Gauge size={17} />Priority management{activePage === "priority-management" && <span className="nav-current" />}</a>
        <a className={`nav-item ${activePage === "priority-logs" ? "active" : ""}`} href="#priority-logs"><ListFilter size={17} />Priority logs{activePage === "priority-logs" && <span className="nav-current" />}</a>
      </nav>
      <span className="nav-label tools-label">TOOLS</span>
      <nav aria-label="Tools">
        <a className="nav-item" href="#policies"><SlidersHorizontal size={17} />Policies<span className="nav-soon">SOON</span></a>
        <a className={`nav-item ${activePage === "settings" ? "active" : ""}`} href="#settings"><Settings2 size={17} />Settings{activePage === "settings" && <span className="nav-current" />}</a>
      </nav>
      <div className="sidebar-spacer" />
      <button className="help-card" onClick={onDemoInfo}>
        <span className="help-icon"><CircleHelp size={17} /></span>
        <span><strong>{demoMode ? "About this demo" : "About ProcNanny"}</strong><small>{demoMode ? "See what's simulated" : "Live data and safety"}</small></span>
        <ArrowUpRight size={14} className="help-arrow" />
      </button>
      <div className="profile-row">
        <span className="profile-avatar">OS</span>
        <span className="profile-copy"><strong>Lab workstation</strong><small>{demoMode ? "Local dashboard demo" : "Loopback API"}</small></span>
        <MoreHorizontal size={18} className="muted-icon" />
      </div>
      <div className="sidebar-foot"><span className={`green-dot ${connectionState}`} />{demoMode ? "DEMO DATA" : connectionState === "connected" ? "BACKEND CONNECTED" : connectionState === "loading" ? "CONNECTING" : "BACKEND OFFLINE"} <span>·</span> v0.1.0</div>
    </aside>
  );
}

function Topbar({ monitoring, connectionState, demoMode, activePage }) {
  const sourceLabel = demoMode ? "DEMO DATA" : connectionState === "connected" ? "LIVE DATA" : connectionState === "loading" ? "CONNECTING" : "DISCONNECTED";
  const pageTitle = activePage === "priority-management"
    ? "Priority Management"
    : activePage === "priority-logs" ? "Priority Management Logs" : activePage === "settings" ? "Settings" : "Overview";
  return (
    <header className="topbar">
      <div className="breadcrumbs"><span>Workspace</span><span className="crumb-slash">/</span><strong>{pageTitle}</strong></div>
      <div className="topbar-actions">
        <span className={`demo-pill ${demoMode ? "demo-source" : connectionState}`}><span className="demo-pill-dot" />{sourceLabel}</span>
        <button aria-label="Search processes" className="icon-button" title="Search processes below"><Search size={17} /></button>
        <button aria-label="Notifications unavailable" className="icon-button notification-button" disabled title="Notifications are not implemented"><Bell size={17} /></button>
        <span className="topbar-divider" />
        <span aria-live="polite" className={`connection-pill ${connectionState}`} role="status"><span />{demoMode ? monitoring ? "Demo on" : "Demo paused" : connectionState !== "connected" ? "Backend offline" : monitoring ? "Monitor on" : "Monitor stopped"}</span>
      </div>
    </header>
  );
}

function priorityActionStatus(action) {
  if (action.status === "dry_run") return "Dry-run proposal";
  if (action.status === "proposed") return "Proposed";
  if (action.status === "applied") return "Applied";
  if (action.status === "failed") return "Failed";
  if (action.status === "skipped") return "Skipped";
  if (action.status === "excluded") return "Excluded";
  if (action.dry_run && action.success === null) return "Dry-run proposal";
  if (action.success === true) return "Applied";
  if (action.success === false) return "Failed";
  return "Skipped";
}

function latestActionForProcess(actions, process) {
  if (process.create_time == null) return undefined;
  return actions
    .filter((action) => (
      action.pid === process.pid
      && action.create_time === process.create_time
      && action.action !== "priority_evaluation"
    ))
    .sort((left, right) => new Date(right.timestamp) - new Date(left.timestamp))[0];
}

function formatPriority(priority) {
  const scheme = priority.priority_scheme ?? priority.scheme;
  const value = priority.priority_value ?? priority.value ?? priority.nice_value;
  if (scheme === "windows_priority_class") {
    const label = priority.priority_label ?? priority.label;
    if (typeof label === "string") {
      return `${label
        .replace(/_PRIORITY_CLASS$/, "")
        .split("_")
        .map((part) => part[0] + part.slice(1).toLowerCase())
        .join(" ")} (Windows)`;
    }
    return Number.isInteger(value)
      ? `Windows class 0x${value.toString(16).toUpperCase()}`
      : "Unavailable";
  }
  if (typeof priority.label === "string") return priority.label;
  return value == null ? "Unavailable" : `${value > 0 ? "+" : ""}${value}`;
}

function formatPriorityUnavailableReason(reason) {
  return ({
    AccessDenied: "Access denied by the operating system",
    NoSuchProcess: "Process ended during sampling",
    ZombieProcess: "Process is a zombie",
  }[reason] ?? (reason ? `Priority unavailable: ${reason}` : null));
}

function evaluationDecisionLabel(evaluation) {
  if (!evaluation) return "Awaiting evaluation";
  if (evaluation.decision === "below_threshold") return "Normal";
  if (evaluation.decision === "adjustment_disabled") return "Threshold exceeded · adjustment disabled";
  if (evaluation.decision === "proposal") return "Proposed";
  if (evaluation.decision === "excluded") return "Excluded";
  if (evaluation.decision === "waiting_for_sustained_cpu") return "Threshold exceeded · awaiting sustained CPU";
  return evaluation.decision
    .replaceAll("_", " ")
    .replace(/^./, (letter) => letter.toUpperCase());
}

function PriorityManagement({
  processes,
  priorityEvaluations,
  priorityActions,
  backendStatus,
  connectionState,
  monitoring,
  demoMode,
  error,
  stale,
  onRefresh,
  refreshing,
  refreshMessage,
  refreshError,
}) {
  const adjustment = backendStatus?.priority_adjustment;
  const highThreshold = backendStatus?.high_cpu_threshold_percent;
  const lowThreshold = backendStatus?.low_cpu_threshold_percent;
  const latestEvaluations = new Map();
  for (const evaluation of priorityEvaluations) {
    latestEvaluations.set(`${evaluation.pid}:${evaluation.create_time ?? "unknown"}`, evaluation);
  }

  return (
    <section className="page-section">
      <div className="page-heading">
        <div><div className="eyebrow"><span>PROCESS GOVERNANCE</span><i /> CPU EVALUATION</div><h1>Priority <span>management.</span></h1><p>Live process samples evaluated against the backend’s configured rolling-average thresholds.</p></div>
        {connectionState === "connected" && !demoMode && <button className="priority-refresh" disabled={refreshing} onClick={onRefresh}>
          <RefreshCw aria-hidden="true" className={refreshing ? "refresh-spinning" : ""} size={14} />
          {refreshing ? "Refreshing…" : "Refresh evaluations"}
        </button>}
      </div>
      {demoMode
        ? <div className="demo-banner" role="note"><span className="demo-banner-icon"><ShieldCheck size={17} /></span><span><strong>Evaluation unavailable in demo mode</strong><span className="banner-copy">Connect to the backend to view real process measurements and recorded priority decisions.</span></span></div>
        : connectionState === "loading"
          ? <div className="demo-banner" role="status"><span className="demo-banner-icon"><Radio size={17} /></span><span><strong>Connecting to ProcNanny</strong><span className="banner-copy">Loading process samples and the configured CPU thresholds.</span></span></div>
          : connectionState !== "connected"
          ? <div className="demo-banner backend-warning" role="alert"><span className="demo-banner-icon"><Shield size={17} /></span><span><strong>Backend data unavailable</strong><span className="banner-copy">{error || "Connect to the ProcNanny backend to load process evaluations."}</span></span></div>
          : <>
            {refreshError && <div className="priority-refresh-error" role="alert">{refreshError}</div>}
            {refreshMessage && !refreshError && <div className="priority-refresh-message" role="status">{refreshMessage}</div>}
            <div className={`demo-banner ${stale ? "backend-warning" : "backend-live"}`} role={stale ? "alert" : "status"}>
              <span className="demo-banner-icon"><Radio size={17} /></span>
              <span><strong>{monitoring ? "Monitoring active" : "Monitoring stopped"}</strong><span className="banner-copy">{stale ? "The process snapshot is stale; terminated processes may no longer appear in the current list." : monitoring ? "Decisions are generated by the backend during monitoring cycles. Refresh retrieves the latest recorded evaluation; it does not start a new evaluation cycle." : "Showing the last backend snapshot and recorded decisions. Start monitoring to produce new observations."}</span></span>
              <span className="read-only-tag"><Shield size={12} /> {adjustment?.enabled ? adjustment.dry_run ? "DRY RUN" : "LIVE MODE" : "ADJUSTMENT OFF"}</span>
            </div>
            <div className="priority-summary-grid">
              <article className="stat-card"><span className="stat-label">MONITOR STATUS</span><div className="stat-value priority-mode">{monitoring ? "RUNNING" : "STOPPED"}</div><div className="stat-footer">{stale ? "Last process snapshot is stale" : "Backend monitoring worker"}</div></article>
              <article className="stat-card"><span className="stat-label">HIGH CPU THRESHOLD</span><div className="stat-value">{highThreshold == null ? "—" : `${highThreshold}%`}</div><div className="stat-footer">Sustained rolling average</div></article>
              <article className="stat-card"><span className="stat-label">RECOVERY THRESHOLD</span><div className="stat-value">{lowThreshold == null ? "—" : `${lowThreshold}%`}</div><div className="stat-footer">Below this value accumulates recovery time</div></article>
              <article className="stat-card"><span className="stat-label">ADJUSTMENT MODE</span><div className="stat-value priority-mode">{!adjustment?.enabled ? "DISABLED" : adjustment.dry_run ? "DRY RUN" : adjustment.supported ? "LIVE" : "UNSUPPORTED"}</div><div className="stat-footer">{adjustment?.enabled ? adjustment.dry_run ? "Enabled · proposals only · no OS changes" : adjustment.supported ? "Enabled · configured live adjustment" : "Enabled · live adjustment unavailable on this OS" : "Evaluation active · priority changes disabled"}</div></article>
            </div>
            <section className="panel priority-panel">
              <div className="panel-heading"><div><h2>CPU evaluation by process</h2><p>{stale ? "Last successful samples · stale" : "Current backend process samples"}</p></div><span className="process-count">{processes.length.toString().padStart(2, "0")}</span></div>
              <div className="process-table-wrap">
                <table className="process-table priority-table">
                  <thead><tr><th>PROCESS / PID</th><th>CPU NOW</th><th>ROLLING AVG / THRESHOLD</th><th>CURRENT PRIORITY</th><th>POLICY DECISION</th><th>PROPOSAL / REASON / LAST ACTION</th></tr></thead>
                  <tbody>
                    {processes.map((process) => {
                      const average = process.average_cpu_percent;
                      const evaluation = latestEvaluations.get(`${process.pid}:${process.create_time ?? "unknown"}`);
                      const lastAction = latestActionForProcess(priorityActions, process);
                      const currentPriority = formatPriority(
                        evaluation?.current_priority ?? process,
                      );
                      const unavailableReason = formatPriorityUnavailableReason(
                        process.priority_unavailable_reason,
                      );
                      const proposed = evaluation?.proposed_priority;
                      const lastActionPriority = lastAction?.resulting_priority
                        ?? lastAction?.proposed_priority
                        ?? lastAction?.requested_priority;
                      const actionStatus = lastAction ? priorityActionStatus(lastAction) : null;
                      return <tr key={process.pid}>
                        <td><div className="process-name-cell"><span className="process-symbol"><TerminalSquare size={15} /></span><span><strong>{process.name}</strong><small>PID {process.pid}</small></span></div></td>
                        <td>{typeof process.cpu_percent === "number" ? `${process.cpu_percent.toFixed(1)}%` : "—"}</td>
                        <td>{typeof evaluation?.average_cpu_percent === "number" ? `${evaluation.average_cpu_percent.toFixed(1)}% / ${evaluation.threshold_percent}%` : typeof average === "number" ? `${average.toFixed(1)}% / ${highThreshold ?? "—"}%` : `— / ${highThreshold ?? "—"}%`}</td>
                        <td><span className="nice-value">{currentPriority}</span>{unavailableReason && <small className="muted-copy">{unavailableReason}</small>}</td>
                        <td><span className={`priority-badge ${evaluation?.decision === "excluded" ? "excluded" : evaluation?.decision === "proposal" ? "proposed" : evaluation?.decision === "adjustment_disabled" ? "status-high" : ""}`}>{evaluationDecisionLabel(evaluation)}</span></td>
                        <td>
                          {proposed && <><span className="priority-badge proposed">Proposed</span><small className="priority-action-detail">{proposed.label ?? `Priority ${proposed.value}`} · {evaluation.explanation}</small></>}
                          {!proposed && <small className="muted-copy">{evaluation?.explanation ?? "No backend evaluation is available yet."}</small>}
                          {lastAction && <small className="priority-action-detail">Latest action: <span className={`priority-badge ${actionStatus.toLowerCase()}`}>{actionStatus}</span>{lastActionPriority != null && ` · priority ${lastActionPriority}`}{lastAction.error ? ` · ${lastAction.error}` : ""}</small>}
                        </td>
                      </tr>;
                    })}
                    {processes.length === 0 && <tr><td className="empty-row" colSpan="6">{stale ? "No process samples are available in the retained snapshot." : "No readable process samples yet. Start monitoring and wait for CPU counters to be primed."}</td></tr>}
                  </tbody>
                </table>
              </div>
              <p className="priority-footnote">Policy decisions and threshold comparisons come from the backend evaluator. A proposal is not an operating-system priority change. Actions are shown only when the backend records them for the same process instance.</p>
            </section>
          </>}
    </section>
  );
}

function PriorityLogs({ demoMode }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All events");
  const [priorityActions, setPriorityActions] = useState([]);
  const [loading, setLoading] = useState(!demoMode);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [page, setPage] = useState(0);
  const pageSize = 25;

  const loadActions = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setLoadError("");
    try {
      const actions = await fetchPriorityActions();
      setPriorityActions(actions);
    } catch (requestError) {
      setLoadError(requestError.message || "Priority history could not be loaded.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (!demoMode) void loadActions();
  }, [demoMode, loadActions]);

  const sortedActions = useMemo(() => [...priorityActions].sort(
    (left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime(),
  ), [priorityActions]);

  const filteredActions = sortedActions.filter((action) => {
    const status = priorityActionStatus(action);
    const matchesStatus = filter === "All events" || status === filter;
    const matchesQuery = `${action.process_name ?? ""} ${action.pid ?? ""}`.toLowerCase().includes(query.trim().toLowerCase());
    return matchesStatus && matchesQuery;
  });
  const pageCount = Math.ceil(filteredActions.length / pageSize);
  const visibleActions = filteredActions.slice(page * pageSize, (page + 1) * pageSize);
  const updateQuery = (value) => {
    setQuery(value);
    setPage(0);
  };
  const updateFilter = (value) => {
    setFilter(value);
    setPage(0);
  };

  const formatPriority = (value) => {
    if (value == null) return "Not recorded";
    if (typeof value === "object" && typeof value.label === "string") return value.label;
    if (typeof value === "object" && value.value != null) return String(value.value);
    return String(value);
  };

  const formatTimestamp = (timestamp) => {
    if (typeof timestamp !== "string" || !timestamp) return "Not recorded";
    const parsed = new Date(timestamp);
    return Number.isNaN(parsed.getTime()) ? timestamp : parsed.toLocaleString();
  };

  const actionLabel = (action) => {
    if (action === "priority_demote") return "Demotion";
    if (action === "priority_restore") return "Restoration";
    if (action === "priority_evaluation") return "Policy evaluation";
    return typeof action === "string" && action ? action.replaceAll("_", " ") : "Not recorded";
  };

  return (
    <section className="page-section">
      <div className="page-heading">
        <div><div className="eyebrow"><span>PROCESS GOVERNANCE</span><i /> AUDIT TRAIL</div><h1>Priority <span>management logs.</span></h1><p>Recorded proposals and priority-change outcomes from the backend JSONL history.</p></div>
        {!demoMode && <button className="priority-refresh" disabled={loading || refreshing} onClick={() => void loadActions(true)}>
          <RefreshCw aria-hidden="true" className={refreshing ? "refresh-spinning" : ""} size={14} />
          {refreshing ? "Refreshing…" : "Refresh history"}
        </button>}
      </div>
      {demoMode
        ? <div className="demo-banner" role="note"><span className="demo-banner-icon"><ShieldCheck size={17} /></span><span><strong>Priority history unavailable in demo mode</strong><span className="banner-copy">Only genuine backend priority-action records appear here.</span></span></div>
        : loading
          ? <div className="demo-banner" role="status"><span className="demo-banner-icon"><Radio size={17} /></span><span><strong>Loading priority history</strong><span className="banner-copy">Requesting up to 200 recorded events from the backend priority-action history.</span></span></div>
          : loadError
            ? <div className="demo-banner backend-warning" role="alert"><span className="demo-banner-icon"><Shield size={17} /></span><span><strong>Priority history unavailable</strong><span className="banner-copy">{loadError}</span></span><button className="retry-button" onClick={() => void loadActions()}>Retry</button></div>
            : <section className="panel priority-panel">
            <div className="panel-heading priority-log-heading"><div><h2>Recorded priority actions</h2><p>Monitoring samples are excluded from this history.</p></div><div className="table-actions">
              <label className="search-field"><Search size={14} /><input aria-label="Search priority logs" onChange={(event) => updateQuery(event.target.value)} placeholder="Search process name or PID…" type="search" value={query} /></label>
              <label className="filter-button"><ListFilter size={14} /><select aria-label="Filter priority log status" onChange={(event) => updateFilter(event.target.value)} value={filter}><option>All events</option><option>Dry-run proposal</option><option>Proposed</option><option>Applied</option><option>Failed</option><option>Skipped</option><option>Excluded</option></select><ChevronDown size={13} /></label>
            </div></div>
            <div className="process-table-wrap">
              <table className="process-table priority-log-table">
                <thead><tr><th>TIMESTAMP</th><th>PROCESS</th><th>PID</th><th>CPU AT EVALUATION</th><th>PREVIOUS</th><th>PROPOSED</th><th>RESULTING</th><th>ACTION</th><th>STATUS / REASON</th></tr></thead>
                <tbody>
                  {visibleActions.map((action, index) => {
                    const status = priorityActionStatus(action);
                    const statusClass = status.toLowerCase().replaceAll(" ", "-");
                    const reason = action.error || action.reason;
                    return <tr key={action.event_id ?? `${action.timestamp}-${action.pid}-${index}`}>
                    <td><time>{formatTimestamp(action.timestamp)}</time></td>
                    <td><div className="process-name-cell"><span className="process-symbol"><TerminalSquare size={15} /></span><strong>{action.process_name || "Not recorded"}</strong></div></td>
                    <td>{action.pid ?? "Not recorded"}</td>
                    <td className="muted-copy">{typeof action.cpu_percent === "number" ? `${action.cpu_percent.toFixed(1)}%` : "Not recorded"}</td>
                    <td><span className="nice-value">{formatPriority(action.previous_priority)}</span></td>
                    <td><span className="nice-value">{formatPriority(action.proposed_priority ?? action.requested_priority)}</span></td>
                    <td><span className="nice-value">{formatPriority(action.resulting_priority)}</span></td>
                    <td>{actionLabel(action.action)}</td>
                    <td><span className={`priority-badge ${statusClass}`}>{status}</span><small className="priority-action-detail">{reason || "No reason recorded"}</small></td>
                  </tr>;
                  })}
                  {visibleActions.length === 0 && <tr><td className="empty-row" colSpan="9">{priorityActions.length === 0 ? "No priority evaluation or action events have been recorded yet. Events will appear after the backend evaluates process observations." : "No priority events match the selected process search or status filter."}</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="table-footer priority-log-footer">
              <span>Showing <strong>{visibleActions.length}</strong> of {filteredActions.length} matching events · latest 200 backend records</span>
              <div className="priority-pagination">
                <button aria-label="Previous priority history page" disabled={page === 0} onClick={() => setPage((current) => current - 1)}>Previous</button>
                <span>{pageCount === 0 ? 0 : page + 1} / {pageCount}</span>
                <button aria-label="Next priority history page" disabled={page + 1 >= pageCount} onClick={() => setPage((current) => current + 1)}>Next</button>
              </div>
            </div>
          </section>}
    </section>
  );
}

function SettingsPage({ backendStatus, connectionState, error, demoMode, onSave }) {
  const [draft, setDraft] = useState(null);
  const [confirmLive, setConfirmLive] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveMessage, setSaveMessage] = useState("");
  const adjustment = backendStatus?.priority_adjustment ?? {};

  useEffect(() => {
    if (!draft && connectionState === "connected" && backendStatus) {
      setDraft({
        highCpu: backendStatus.high_cpu_threshold_percent,
        lowCpu: backendStatus.low_cpu_threshold_percent,
        pollInterval: backendStatus.poll_interval_seconds,
        historyWindow: backendStatus.history_window_seconds,
        excludedProcesses: (backendStatus.excluded_processes ?? []).join(", "),
        enabled: adjustment.enabled === true,
        dryRun: adjustment.dry_run !== false,
        cooldown: adjustment.cooldown_seconds,
        highDuration: adjustment.high_duration_seconds,
        recoveryDuration: adjustment.recovery_duration_seconds,
        step: adjustment.step,
      });
    }
  }, [adjustment, backendStatus, connectionState, draft]);

  const setNumber = (key) => (event) => {
    setDraft((current) => ({
      ...current,
      [key]: event.target.value === "" ? "" : Number(event.target.value),
    }));
  };

  const handleSave = async (event) => {
    event.preventDefault();
    setSaving(true);
    setSaveError("");
    setSaveMessage("");
    try {
      const status = await onSave({
        high_cpu_threshold_percent: draft.highCpu,
        low_cpu_threshold_percent: draft.lowCpu,
        poll_interval_seconds: draft.pollInterval,
        history_window_seconds: draft.historyWindow,
        excluded_processes: draft.excludedProcesses.split(",").map((name) => name.trim()).filter(Boolean),
        priority_adjustment_enabled: draft.enabled,
        priority_adjustment_dry_run: draft.dryRun,
        cooldown_seconds: draft.cooldown,
        high_duration_seconds: draft.highDuration,
        recovery_duration_seconds: draft.recoveryDuration,
        step: draft.step,
        confirm_live_adjustment: requiresLiveConfirmation && confirmLive,
      });
      setDraft({
        highCpu: status.high_cpu_threshold_percent,
        lowCpu: status.low_cpu_threshold_percent,
        pollInterval: status.poll_interval_seconds,
        historyWindow: status.history_window_seconds,
        excludedProcesses: (status.excluded_processes ?? []).join(", "),
        enabled: status.priority_adjustment?.enabled === true,
        dryRun: status.priority_adjustment?.dry_run !== false,
        cooldown: status.priority_adjustment?.cooldown_seconds,
        highDuration: status.priority_adjustment?.high_duration_seconds,
        recoveryDuration: status.priority_adjustment?.recovery_duration_seconds,
        step: status.priority_adjustment?.step,
      });
      setConfirmLive(false);
      setSaveMessage("Configuration saved and applied to the monitoring pipeline.");
    } catch (saveFailure) {
      setSaveError(saveFailure.message);
    } finally {
      setSaving(false);
    }
  };

  if (demoMode) {
    return (
      <section className="page-section">
        <div className="page-heading"><div><div className="eyebrow"><span>PROC NANNY</span><i /> CONFIGURATION</div><h1>Active <span>settings.</span></h1><p>Demo mode cannot change the live backend configuration.</p></div></div>
        <div className="demo-banner" role="note"><span className="demo-banner-icon"><ShieldCheck size={17} /></span><span><strong>Configuration unavailable in demo mode</strong><span className="banner-copy">Demo mode is not connected to the backend configuration source.</span></span></div>
      </section>
    );
  }

  if (connectionState !== "connected" || !backendStatus) {
    return (
      <section className="page-section">
        <div className="page-heading"><div><div className="eyebrow"><span>PROC NANNY</span><i /> CONFIGURATION</div><h1>Active <span>settings.</span></h1><p>Validated settings currently loaded by the backend.</p></div></div>
        <div className="demo-banner backend-warning" role="alert"><span className="demo-banner-icon"><Shield size={17} /></span><span><strong>Active configuration unavailable</strong><span className="banner-copy">{error || "Connect to the backend to view validated settings. No defaults are substituted here."}</span></span></div>
      </section>
    );
  }

  const excluded = backendStatus.excluded_processes;
  const liveModeSelected = draft?.enabled && !draft?.dryRun;
  const requiresLiveConfirmation = liveModeSelected
    && (!adjustment.enabled || adjustment.dry_run);

  return (
    <section className="page-section">
      <div className="page-heading"><div><div className="eyebrow"><span>PROC NANNY</span><i /> CONFIGURATION</div><h1>Active <span>settings.</span></h1><p>Update the validated backend configuration without restarting the monitor.</p></div></div>
      <div className="demo-banner backend-live" role="status"><span className="demo-banner-icon"><ShieldCheck size={17} /></span><span><strong>Backend configuration active</strong><span className="banner-copy">Settings are persisted to the backend YAML and applied to subsequent monitoring cycles. Saving settings does not itself change process priorities.</span></span><span className="read-only-tag"><Shield size={12} /> VALIDATED</span></div>
      {backendStatus.settings_persistent === false && <div className="demo-banner backend-warning" role="alert"><span className="demo-banner-icon"><Shield size={17} /></span><span><strong>Persistent settings unavailable</strong><span className="banner-copy">This backend was created with an in-memory configuration and cannot save changes to disk.</span></span></div>}
      {saveError && <div className="priority-refresh-error" role="alert">{saveError}</div>}
      {saveMessage && <div className="priority-refresh-message" role="status">{saveMessage}</div>}
      <form className="panel settings-panel settings-form" onSubmit={handleSave}>
        <div className="panel-heading"><div><h2>Monitoring and priority policy</h2><p>All values are validated by the backend before persistence.</p></div></div>
        {!draft ? <p role="status">Loading active configuration…</p> : <>
          <div className="settings-form-grid">
            <label>High CPU threshold (%)<input aria-label="High CPU threshold (%)" type="number" min="0" step="any" required value={draft.highCpu} onChange={setNumber("highCpu")} /></label>
            <label>Recovery CPU threshold (%)<input aria-label="Recovery CPU threshold (%)" type="number" min="0" step="any" required value={draft.lowCpu} onChange={setNumber("lowCpu")} /></label>
            <label>Monitoring interval (seconds)<input aria-label="Monitoring interval (seconds)" type="number" min="0.1" step="any" required value={draft.pollInterval} onChange={setNumber("pollInterval")} /></label>
            <label>CPU history window (seconds)<input aria-label="CPU history window (seconds)" type="number" min="0.1" step="any" required value={draft.historyWindow} onChange={setNumber("historyWindow")} /></label>
            <label>Sustained high CPU duration (seconds)<input aria-label="Sustained high CPU duration (seconds)" type="number" min="0.1" step="any" required value={draft.highDuration} onChange={setNumber("highDuration")} /></label>
            <label>Recovery duration (seconds)<input aria-label="Recovery duration (seconds)" type="number" min="0.1" step="any" required value={draft.recoveryDuration} onChange={setNumber("recoveryDuration")} /></label>
            <label>Action cooldown (seconds)<input aria-label="Action cooldown (seconds)" type="number" min="0" step="any" required value={draft.cooldown} onChange={setNumber("cooldown")} /></label>
            <label>Priority step<input aria-label="Priority step" type="number" min="1" step="1" required value={draft.step} onChange={setNumber("step")} /></label>
            <label className="settings-form-wide">Excluded process names (comma-separated)<input aria-label="Excluded process names (comma-separated)" type="text" value={draft.excludedProcesses} onChange={(event) => setDraft((current) => ({ ...current, excludedProcesses: event.target.value }))} /></label>
          </div>
          <div className="settings-toggles">
            <label><input type="checkbox" checked={draft.enabled} onChange={(event) => { setConfirmLive(false); setDraft((current) => ({ ...current, enabled: event.target.checked })); }} /> Enable automatic priority adjustment</label>
            <label><input type="checkbox" checked={draft.dryRun} onChange={(event) => { setConfirmLive(false); setDraft((current) => ({ ...current, dryRun: event.target.checked })); }} /> Dry-run only (never change process priority)</label>
          </div>
          {liveModeSelected && <div className="demo-banner backend-warning" role="alert"><span className="demo-banner-icon"><Shield size={17} /></span><span><strong>Live priority changes selected</strong><span className="banner-copy">The backend will change eligible process priorities only after sustained CPU policy checks. Windows may deny individual changes; no privileges are elevated, and protected/system processes remain excluded.</span></span></div>}
          {requiresLiveConfirmation && <label className="settings-live-confirm"><input type="checkbox" checked={confirmLive} onChange={(event) => setConfirmLive(event.target.checked)} /> I explicitly authorize live priority changes on eligible processes managed by ProcNanny.</label>}
          <button className="priority-refresh settings-save" type="submit" disabled={saving || backendStatus.settings_persistent === false || (requiresLiveConfirmation && !confirmLive)}>
            {saving ? "Saving configuration…" : "Save backend settings"}
          </button>
        </>}
      </form>
      <section className="panel settings-panel">
        <div className="panel-heading"><div><h2>Current active safeguards</h2><p>Reported directly by the running backend.</p></div></div>
        <dl className="settings-grid">
          <div className="settings-item"><dt>Priority adjustment</dt><dd>{adjustment.enabled ? "Enabled" : "Disabled"}</dd></div>
          <div className="settings-item"><dt>Mode</dt><dd>{adjustment.dry_run ? "Dry-run · proposals only" : "Live mode"}</dd></div>
          <div className="settings-item"><dt>Platform support</dt><dd>{adjustment.supported ? `Supported · ${adjustment.platform}` : `Unsupported · ${adjustment.platform}`}</dd></div>
          <div className="settings-item settings-wide"><dt>Excluded processes</dt><dd>{Array.isArray(excluded) && excluded.length ? excluded.join(", ") : "None configured"}</dd></div>
        </dl>
      </section>
    </section>
  );
}

function DataBanner({ connectionState, demoMode, monitoring, monitorState, snapshot, priorityAdjustment, error, onRetry }) {
  if (demoMode) {
    return (
      <div className="demo-banner" role="note">
        <span className="demo-banner-icon"><ShieldCheck size={17} /></span>
        <span><strong>Demo environment</strong><span className="banner-copy">Sample data for UI preview only. No live processes are being monitored and no priorities will be changed.</span></span>
        <span className="read-only-tag"><Shield size={12} /> READ ONLY</span>
      </div>
    );
  }
  const stale = snapshot?.stale;
  const title = connectionState === "loading"
    ? "Connecting to ProcNanny"
    : connectionState !== "connected"
      ? "Backend disconnected"
      : monitorState === "error"
        ? "Monitor worker error"
      : stale
        ? "Live data is stale"
        : monitoring
          ? "Live backend data"
          : "Backend connected · monitoring stopped";
  const message = connectionState === "loading"
    ? "Waiting for the local Python API. No process data is shown until it responds."
    : connectionState !== "connected"
    ? `${error || "The API at 127.0.0.1:8000 is unavailable."} ${snapshot?.updated_at ? "Previously fetched measurements are retained but stale." : "No demo process data is substituted."}`
    : monitorState === "error"
      ? backendStatusMessage(monitorState, error)
    : stale
      ? monitorState === "stopped"
        ? "Monitoring has stopped. The values below are the last samples and are no longer updating."
        : "The last successful process sample is retained and marked stale while the backend recovers."
        : monitoring
          ? priorityAdjustment?.enabled && !priorityAdjustment?.dry_run
            ? "Live measurements are collected; configured priority adjustments may be applied automatically."
            : priorityAdjustment?.enabled
              ? "Live measurements are collected and priority changes are recorded as dry-run proposals only."
              : "Values below are read-only measurements; CPU evaluation remains active while automatic priority adjustment is disabled."
          : "Start monitoring to collect fresh process and CPU samples.";
  const priorityLabel = priorityAdjustment?.enabled
    ? priorityAdjustment.dry_run ? "DRY RUN" : priorityAdjustment.supported ? "AUTO PRIORITY" : "UNSUPPORTED OS"
    : "ADJUSTMENT OFF";
  return (
    <div className={`demo-banner ${connectionState !== "connected" || stale || monitorState === "error" ? "backend-warning" : "backend-live"}`} role={connectionState !== "connected" || monitorState === "error" ? "alert" : "note"}>
      <span className="demo-banner-icon"><ShieldCheck size={17} /></span>
      <span><strong>{title}</strong><span className="banner-copy">{message}</span></span>
      {connectionState !== "connected" && <button className="retry-button" onClick={onRetry}>Retry</button>}
      {connectionState === "connected" && <span className="read-only-tag"><Shield size={12} /> {priorityLabel}</span>}
    </div>
  );
}

function backendStatusMessage(monitorState, error) {
  return monitorState === "error"
    ? error || "The monitor worker stopped unexpectedly. Use Start monitoring to retry."
    : "";
}

function StatCard({ icon: Icon, label, value, suffix, detail, trend, accent, progress }) {
  return (
    <article className="stat-card">
      <div className="stat-heading"><span className={`stat-icon ${accent}`}><Icon size={16} /></span><span className="stat-label">{label}</span><button className="stat-more" aria-label={`${label} info`} title="Sample value only"><MoreHorizontal size={17} /></button></div>
      <div className="stat-value">{value}<span>{suffix}</span></div>
      <div className="stat-footer"><span className="stat-detail">{trend && (trend === "down" ? <ArrowDownRight size={13} /> : <ArrowUpRight size={13} />)}{detail}</span>{progress !== undefined && <span className="mini-progress"><i style={{ width: `${progress}%` }} /></span>}</div>
    </article>
  );
}

function UsageChart({ monitoring, demoMode, cpuHistory, highThreshold, priorityAdjustment }) {
  const values = demoMode ? chartValues : cpuHistory.map((sample) => sample.value);
  const chart = getChartGeometry(values);
  return (
    <section className="panel chart-panel">
      <div className="panel-heading">
        <div><div className="panel-title-line"><h2>System CPU usage</h2><span className={`chart-live ${demoMode ? "" : "live-source"}`}><i />{demoMode ? monitoring ? "DEMO STREAM" : "DEMO PAUSED" : monitoring ? "LIVE SAMPLE" : "MONITOR STOPPED"}</span></div><p>{demoMode ? "Illustrative sample · last 2 minutes" : "Backend samples · actual system CPU"}</p></div>
        <button className="select-button">Last 2 min <ChevronDown size={14} /></button>
      </div>
      <div className="chart-legend"><span><i className="legend-cpu" />CPU usage</span><span><i className="legend-threshold" />High threshold <b>{highThreshold}%</b></span></div>
      <div className="chart-wrap">
        <div className="chart-y-labels"><span>100%</span><span>75%</span><span>50%</span><span>25%</span><span>0%</span></div>
        <div className="chart-main">
          <div className="chart-grid"><i /><i /><i /><i /><i /></div>
          <div className="threshold-line" style={{ top: `${Math.min(100, Math.max(0, 100 - highThreshold))}%` }}><span>{highThreshold}%</span></div>
          <svg aria-label={demoMode ? "Illustrative CPU usage chart, not live measurements" : "Live system CPU samples returned by the backend"} className="usage-chart" viewBox="0 0 640 132" preserveAspectRatio="none" role="img">
            <defs><linearGradient id="chart-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#45d6a0" stopOpacity=".23" /><stop offset="100%" stopColor="#45d6a0" stopOpacity="0" /></linearGradient></defs>
            {values.length > 0 && <polygon points={chart.area} fill="url(#chart-fill)" />}
            {values.length > 0 && <polyline points={chart.line} fill="none" stroke="#45d6a0" strokeWidth="2.4" vectorEffect="non-scaling-stroke" />}
            {values.length > 0 && <circle cx="640" cy={132 - values.at(-1) * 1.2} r="4" fill="#45d6a0" stroke="#10221e" strokeWidth="3" vectorEffect="non-scaling-stroke" />}
          </svg>
          {values.length === 0 && <span className="chart-empty">{demoMode ? "No sample points available." : "Waiting for backend CPU samples…"}</span>}
          <div className="chart-x-labels"><span>−2m</span><span>−90s</span><span>−60s</span><span>−30s</span><span>Now</span></div>
        </div>
      </div>
      <div className="chart-caption"><span><span className="caption-dot" />{demoMode ? "Illustrative sample" : "Sampled by backend polling"}</span><span className="chart-caption-note">{demoMode ? "Demo values · not live" : priorityAdjustment?.enabled && !priorityAdjustment?.supported ? "Priority adjustment unsupported on this OS" : priorityAdjustment?.enabled && !priorityAdjustment?.dry_run ? "Priority policy may adjust processes" : priorityAdjustment?.enabled ? "Priority changes are dry-run proposals" : "Priority adjustment disabled"}</span></div>
    </section>
  );
}

function MonitorCard({ monitoring, monitorState, onToggle, actionPending, connectionState, demoMode, actionError, priorityAdjustment }) {
  const buttonDisabled = actionPending || (!demoMode && (connectionState !== "connected" || monitorState === "starting" || monitorState === "stopping"));
  const active = demoMode ? monitoring : connectionState === "connected" && (monitorState === "running" || monitorState === "starting");
  const statusText = demoMode
    ? active ? "Monitoring active" : "Monitoring paused"
    : connectionState !== "connected" ? "Backend unavailable" : monitorState === "starting" ? "Starting monitor…" : monitorState === "stopping" ? "Stopping monitor…" : active ? "Monitoring active" : monitorState === "error" ? "Monitoring error" : "Monitoring stopped";
  return (
    <section className="panel monitor-panel">
      <div className="panel-heading"><div><h2>Monitor status</h2><p>{demoMode ? "Local preview controls" : "Backend lifecycle controls"}</p></div><span className={`monitor-chip ${demoMode ? "" : "live-source"}`}><Radio size={12} /> {demoMode ? "DEMO" : "API"}</span></div>
      <div className="monitor-state">
        <span className={`monitor-state-icon ${active ? "on" : "off"}`}>{active ? <Activity size={19} /> : <Pause size={18} />}</span>
        <div><strong>{statusText}</strong><span>{demoMode ? "Simulated polling · demo only" : connectionState !== "connected" ? "Status unavailable while disconnected" : monitorState === "running" ? "Polling host processes" : "No process scan is running"}</span></div>
      </div>
      <div className="monitor-divider" />
      <div className="monitor-meta"><span><Clock3 size={14} />Poll interval</span><strong>{demoMode ? "5 seconds · demo" : monitorState === "running" ? "Configured in backend" : "—"}</strong></div>
      <div className="monitor-meta"><span><ShieldCheck size={14} />Priority adjustment</span><strong className="no-actions"><Check size={13} />{demoMode || !priorityAdjustment?.enabled ? " Disabled" : priorityAdjustment.dry_run ? " Dry run" : priorityAdjustment.supported ? " Enabled" : " Unsupported OS"}</strong></div>
      <button aria-label={active ? demoMode ? "Pause demo monitoring" : "Stop monitoring" : demoMode ? "Resume demo monitoring" : "Start monitoring"} className={`monitor-toggle ${active ? "stop" : "start"}`} disabled={buttonDisabled} onClick={onToggle}>
        {actionPending ? "Updating…" : active ? <><Pause size={15} /> {demoMode ? "Pause demo" : "Stop monitoring"}</> : <><Play size={15} /> {demoMode ? "Resume demo" : "Start monitoring"}</>}
      </button>
      {actionError && <span className="action-error" role="alert">{actionError}</span>}
      <span className="monitor-note">{demoMode ? "Only pauses the sample animation. No system process is affected." : priorityAdjustment?.enabled && !priorityAdjustment?.dry_run && priorityAdjustment?.supported ? "Monitoring controls the worker; configured process priorities may be adjusted automatically." : priorityAdjustment?.enabled && !priorityAdjustment?.supported ? "Priority adjustment is unsupported on this operating system." : priorityAdjustment?.enabled ? "Priority adjustments are proposals only while dry-run is active." : "Controls the monitor lifecycle. Automatic priority adjustment is disabled."}</span>
    </section>
  );
}

function ProcessTable({ query, onQueryChange, filter, onFilterChange, processes, demoMode, connected, connectionState, stale, highThreshold }) {
  const filteredProcesses = processes.filter((process) => {
    const matchesQuery = `${process.name} ${process.pid}`.toLowerCase().includes(query.toLowerCase());
    const highCpu = process.cpu_percent >= highThreshold;
    return matchesQuery && (filter === "All processes" || (filter === "High CPU" ? highCpu : !highCpu));
  });

  return (
    <section className="panel processes-panel" id="processes">
      <div className="panel-heading processes-heading">
        <div><div className="panel-title-line"><h2>Processes</h2><span className="process-count">{processes.length.toString().padStart(2, "0")}</span></div><p>{demoMode ? "Visible processes in demo dataset" : connected ? "Visible processes from the backend" : "No live process data available"}</p></div>
        <div className="table-actions">
          <label className="search-field"><Search size={14} /><input aria-label="Search processes" onChange={(event) => onQueryChange(event.target.value)} placeholder="Search processes..." type="search" value={query} /></label>
          <label className="filter-button"><ListFilter size={14} /><select aria-label="Filter processes" onChange={(event) => onFilterChange(event.target.value)} value={filter}><option>All processes</option><option>High CPU</option><option>Normal</option></select><ChevronDown size={13} /></label>
        </div>
      </div>
      <div className="process-table-wrap">
        <table className="process-table">
          <thead><tr><th>PROCESS <span className="sort-indicator">↕</span></th><th>CPU USAGE</th><th>PRIORITY</th><th>STATUS</th></tr></thead>
          <tbody>
            {filteredProcesses.map((process) => (
                <tr key={process.pid}>
                  <td><div className="process-name-cell"><span className={`process-symbol ${process.cpu_percent >= highThreshold ? "hot" : ""}`}><TerminalSquare size={15} /></span><span><strong>{process.name}</strong><small>PID {process.pid}</small></span></div></td>
                  <td><div className="cpu-cell"><span className={process.cpu_percent >= highThreshold ? "cpu-high" : ""}>{process.cpu_percent.toFixed(1)}%</span><span className="cpu-bar"><i className={process.cpu_percent >= highThreshold ? "bar-high" : ""} style={{ width: `${Math.min(process.cpu_percent, 100)}%` }} /></span></div></td>
                  <td><span className={`nice-value ${process.priority_scheme !== "windows_priority_class" && process.nice_value != null && process.nice_value < 0 ? "nice-negative" : ""}`}>{formatPriority(process)}</span>{process.priority_unavailable_reason && <small className="muted-copy">{formatPriorityUnavailableReason(process.priority_unavailable_reason)}</small>}</td>
                  <td><span className={`process-status ${process.cpu_percent >= highThreshold ? "status-high" : ""}`}><i />{process.status ?? (process.cpu_percent >= highThreshold ? "High usage" : "Normal")}</span></td>
                </tr>
              ))}
              {filteredProcesses.length === 0 && <tr><td className="empty-row" colSpan="4">{query || filter !== "All processes" ? "No process matches your filters." : demoMode ? "No demo processes available." : connected ? "No readable process samples yet. The monitor primes CPU counters on its first scan." : connectionState === "loading" ? "Connecting to the backend; no process sample is available yet." : "Start the backend to view live process data."}</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="table-footer"><span>Showing <strong>{filteredProcesses.length}</strong> of {processes.length} {demoMode ? "demo processes" : "processes"}</span><span><span className={`table-updated-dot ${connected && !stale && !demoMode ? "live-source" : ""}`} />{demoMode ? "Sample data · not live" : !connected ? "Backend disconnected" : stale ? "Last live sample · stale" : "Live backend data"}</span></div>
    </section>
  );
}

function ActivityLog({ logs, demoMode, connected, highThreshold }) {
  const events = demoMode ? demoActivity : logs.map((log) => {
    const isPriorityAction = log.action?.startsWith("priority_");
    const successful = log.success === true;
    return {
      time: new Date(log.timestamp).toLocaleTimeString(),
      pid: log.pid,
      tone: log.action === "priority_demote" || log.success === false || log.cpu_percent >= highThreshold ? "amber" : "green",
      title: log.action === "monitor"
        ? "Process sample recorded"
        : log.action === "priority_demote"
          ? log.dry_run ? "Priority demotion proposed" : successful ? "Priority lowered" : "Priority demotion failed"
          : log.action === "priority_restore"
            ? log.dry_run ? "Priority restoration proposed" : successful ? "Priority restored" : "Priority restoration failed"
            : `Backend action: ${log.action}`,
      detail: isPriorityAction
        ? `${log.process_name} · PID ${log.pid} · nice ${log.previous_priority} → ${log.requested_priority}${log.dry_run ? " · dry run" : successful ? " · applied" : " · failed"}`
        : `${log.process_name} · PID ${log.pid} · ${Number(log.cpu_percent).toFixed(1)}% CPU · nice ${log.nice_value}`,
      reason: log.reason,
    };
  });
  return (
    <section className="panel activity-panel" id="activity">
      <div className="panel-heading"><div><h2>Recent activity</h2><p>{demoMode ? "Illustrative event stream" : "Backend JSONL log records"}</p></div></div>
      <div className="activity-list">
        {events.map((event, index) => (
          <article className="activity-item" key={`${event.time}-${event.reason}-${event.pid ?? "demo"}-${index}`}>
            <span className={`activity-mark ${event.tone}`}>{event.tone === "amber" ? <ArrowUpRight size={14} /> : event.tone === "blue" ? <Play size={12} /> : <Check size={13} />}</span>
            <span className="activity-copy"><strong>{event.title}</strong><span>{event.detail}</span></span>
            <span className="activity-trail"><time>{event.time}</time><code>{event.reason}</code></span>
            {index < demoActivity.length - 1 && <i className="activity-line" />}
          </article>
        ))}
        {events.length === 0 && <p className="empty-activity">{demoMode ? "No demo activity available." : connected ? "No backend log records yet." : "Backend logs unavailable while disconnected."}</p>}
      </div>
      <span className="activity-source">{demoMode ? "Illustrative demo events · not live" : connected ? "Actual JSONL records from the backend" : logs.length ? "Previously fetched backend records · stale" : "No demo events substituted"}</span>
    </section>
  );
}

function DemoModal({ onClose, demoMode }) {
  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <section aria-labelledby="demo-modal-title" aria-modal="true" className="demo-modal" onClick={(event) => event.stopPropagation()} role="dialog">
        <button aria-label="Close demo information" className="modal-close" onClick={onClose}><X size={17} /></button>
        <span className="modal-mark"><Sparkles size={20} /></span>
        <span className="modal-eyebrow">PROJECT PREVIEW</span>
        <h2 id="demo-modal-title">{demoMode ? "A clearly labeled dashboard demo." : "A safe process monitor with opt-in adjustment."}</h2>
        <p>{demoMode ? "This preview uses illustrative sample values and is not connected to the Python backend. Its controls only change local demo state." : "The dashboard reads process samples and JSONL action records from the local ProcNanny API. Priority adjustment is disabled by default and dry-run is enabled by default."}</p>
        <div className="modal-facts"><span><Check size={14} />{demoMode ? "All shown process and CPU values are sample data" : "Live backend data is labeled separately from demo mode"}</span><span><Check size={14} />{demoMode ? "No system process is affected" : "No invented values replace missing backend data"}</span><span><Check size={14} />{demoMode ? "The demo cannot change process priorities" : "Priority changes require explicit configuration and are logged"}</span></div>
        <button className="modal-done" onClick={onClose}>Got it <Check size={15} /></button>
      </section>
    </div>
  );
}

export default function App() {
  const demoMode = import.meta.env.VITE_DEMO_MODE === "true";
  const [activePage, setActivePage] = useState(() => {
    const page = window.location.hash.slice(1);
    return page === "priority-management" || page === "priority-logs" || page === "settings" ? page : "overview";
  });
  const [demoMonitoring, setDemoMonitoring] = useState(true);
  const [connectionState, setConnectionState] = useState(demoMode ? "demo" : "loading");
  const [backendStatus, setBackendStatus] = useState(null);
  const [snapshot, setSnapshot] = useState(null);
  const [logs, setLogs] = useState([]);
  const [priorityActions, setPriorityActions] = useState([]);
  const [priorityEvaluations, setPriorityEvaluations] = useState([]);
  const [cpuHistory, setCpuHistory] = useState([]);
  const [apiError, setApiError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionPending, setActionPending] = useState(false);
  const [priorityRefreshPending, setPriorityRefreshPending] = useState(false);
  const [priorityRefreshMessage, setPriorityRefreshMessage] = useState("");
  const [priorityRefreshError, setPriorityRefreshError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All processes");
  const [showDemoInfo, setShowDemoInfo] = useState(false);
  const monitoring = demoMode ? demoMonitoring : Boolean(backendStatus?.monitoring);
  const processes = demoMode
    ? demoProcesses.map((process) => ({
      ...process,
      cpu_percent: process.cpu,
      nice_value: process.nice,
    }))
    : snapshot?.processes ?? [];
  const displayedLogs = demoMode ? demoActivity.map((event) => ({
    timestamp: `2026-10-12T${event.time}+00:00`,
    pid: 0,
    process_name: event.detail,
    cpu_percent: 0,
    nice_value: 0,
    action: "demo",
    reason: event.reason,
  })) : [...new Map(
    [...logs, ...priorityActions].map((log) => [
      `${log.timestamp}-${log.pid}-${log.action}`,
      log,
    ]),
  ).values()].sort((left, right) => new Date(left.timestamp) - new Date(right.timestamp));

  const refreshDashboard = useCallback(async () => {
    if (demoMode) return;
    try {
      const data = await fetchDashboardData();
      setBackendStatus(data.status);
      setSnapshot(data.processSnapshot);
      setLogs(data.logs);
      setPriorityActions(data.priorityActions ?? []);
      setPriorityEvaluations(data.priorityEvaluations ?? []);
      setConnectionState("connected");
      setApiError("");
      if (
        typeof data.processSnapshot.system_cpu_percent === "number"
        && data.processSnapshot.updated_at
      ) {
        setCpuHistory((current) => {
          if (current.at(-1)?.timestamp === data.processSnapshot.updated_at) return current;
          return [...current, {
            timestamp: data.processSnapshot.updated_at,
            value: data.processSnapshot.system_cpu_percent,
          }].slice(-24);
        });
      }
    } catch (error) {
      setConnectionState("disconnected");
      setApiError(error.message);
      setSnapshot((current) => current ? { ...current, stale: true } : current);
    }
  }, [demoMode]);

  const refreshPriorityData = async () => {
    setPriorityRefreshPending(true);
    setPriorityRefreshMessage("");
    setPriorityRefreshError("");
    try {
      const data = await fetchPriorityData();
      setBackendStatus(data.status);
      setSnapshot(data.processSnapshot);
      setPriorityActions(data.priorityActions ?? []);
      setPriorityEvaluations(data.priorityEvaluations ?? []);
      setConnectionState("connected");
      setApiError("");
      setPriorityRefreshMessage(
        "Latest backend evaluation and action records refreshed. New evaluations are produced by monitoring cycles.",
      );
    } catch (error) {
      setPriorityRefreshError(error.message);
    } finally {
      setPriorityRefreshPending(false);
    }
  };

  useEffect(() => {
    if (demoMode) {
      setConnectionState("demo");
      return undefined;
    }
    void refreshDashboard();
    const refreshInterval = Math.max(
      1000,
      (backendStatus?.poll_interval_seconds ?? 5) * 1000,
    );
    const intervalId = window.setInterval(() => void refreshDashboard(), refreshInterval);
    return () => window.clearInterval(intervalId);
  }, [backendStatus?.poll_interval_seconds, demoMode, refreshDashboard]);

  const toggleMonitoring = async () => {
    if (demoMode) {
      setDemoMonitoring((active) => !active);
      return;
    }
    setActionPending(true);
    setActionError("");
    try {
      await setMonitoringEnabled(!monitoring);
      await refreshDashboard();
    } catch (error) {
      setActionError(error.message);
    } finally {
      setActionPending(false);
    }
  };
  const saveBackendSettings = async (configuration) => {
    const status = await updateSettings(configuration);
    setBackendStatus(status);
    setConnectionState("connected");
    setApiError("");
    return status;
  };
  const highThreshold = backendStatus?.high_cpu_threshold_percent ?? 70;
  const systemCpu = demoMode ? 52.8 : snapshot?.system_cpu_percent;
  const activeProcesses = demoMode ? 142 : processes.length;
  const highCpuCount = demoMode ? 3 : snapshot?.updated_at
    ? processes.filter((process) => process.cpu_percent >= highThreshold).length
    : null;

  useEffect(() => {
    const handleHashChange = () => {
      const page = window.location.hash.slice(1);
      setActivePage(page === "priority-management" || page === "priority-logs" || page === "settings" ? page : "overview");
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  return (
    <div className="app-shell">
      <Sidebar onDemoInfo={() => setShowDemoInfo(true)} connectionState={connectionState} demoMode={demoMode} processCount={processes.length} activePage={activePage} />
      <main className="main-shell" id="overview">
        <Topbar monitoring={monitoring} connectionState={connectionState} demoMode={demoMode} activePage={activePage} />
        <div className="dashboard-content">
          {activePage === "priority-management"
            ? <PriorityManagement processes={processes} priorityEvaluations={priorityEvaluations} priorityActions={priorityActions} backendStatus={backendStatus} connectionState={connectionState} monitoring={monitoring} demoMode={demoMode} error={apiError} stale={Boolean(snapshot?.stale)} onRefresh={refreshPriorityData} refreshing={priorityRefreshPending} refreshMessage={priorityRefreshMessage} refreshError={priorityRefreshError} />
            : activePage === "priority-logs"
              ? <PriorityLogs demoMode={demoMode} />
              : activePage === "settings"
                ? <SettingsPage backendStatus={backendStatus} connectionState={connectionState} error={apiError} demoMode={demoMode} onSave={saveBackendSettings} />
              : <>
          <div className="page-heading">
            <div><div className="eyebrow"><span>PROCESS GOVERNANCE</span><i /> SYSTEM OVERVIEW</div><h1>Good morning, <span>operator.</span></h1><p>Process telemetry and optional, safety-gated priority adjustment.</p></div>
            <div className="page-heading-right"><span className="local-time"><Clock3 size={14} />{snapshot?.updated_at ? new Date(snapshot.updated_at).toLocaleString() : "No sample yet"}</span><button className="settings-button" onClick={() => setShowDemoInfo(true)}><SlidersHorizontal size={14} /> About</button></div>
          </div>
          <DataBanner connectionState={connectionState} demoMode={demoMode} monitoring={monitoring} monitorState={backendStatus?.state} snapshot={snapshot} priorityAdjustment={backendStatus?.priority_adjustment} error={backendStatus?.last_error || apiError} onRetry={() => void refreshDashboard()} />
          <section aria-label={demoMode ? "Demo system statistics" : "Live system statistics"} className="stats-grid">
            <StatCard accent="mint" icon={Cpu} label="CPU UTILIZATION" value={systemCpu == null ? "—" : systemCpu.toFixed(1)} suffix={systemCpu == null ? "" : "%"} detail={demoMode ? "Illustrative value · not live" : snapshot?.stale ? "Last sample · stale" : "Actual system CPU sample"} progress={systemCpu == null ? undefined : Math.min(systemCpu, 100)} />
            <StatCard accent="violet" icon={TerminalSquare} label="VISIBLE PROCESSES" value={demoMode ? "142" : activeProcesses.toString()} suffix="" detail={demoMode ? "Demo-only count" : "Readable processes in latest scan"} />
            <StatCard accent="amber" icon={Gauge} label="HIGH CPU PROCESSES" value={highCpuCount == null ? "—" : highCpuCount.toString().padStart(2, "0")} suffix="" detail={`Above ${highThreshold}% threshold`} />
            <StatCard accent="blue" icon={ShieldCheck} label="PRIORITY ADJUSTMENT" value={demoMode || !backendStatus?.priority_adjustment?.enabled ? "DISABLED" : backendStatus.priority_adjustment.dry_run ? "DRY RUN" : "ENABLED"} suffix="" detail={demoMode ? "Unavailable in demo mode" : !backendStatus?.priority_adjustment?.enabled ? "Evaluation active · changes disabled" : backendStatus.priority_adjustment.dry_run ? "Proposals only · no changes" : backendStatus.priority_adjustment.supported ? "Automatic changes enabled" : "Unsupported operating system"} />
          </section>
          <div className="overview-grid"><UsageChart monitoring={monitoring} demoMode={demoMode} cpuHistory={cpuHistory} highThreshold={highThreshold} priorityAdjustment={backendStatus?.priority_adjustment} /><MonitorCard monitoring={monitoring} monitorState={backendStatus?.state ?? "stopped"} onToggle={toggleMonitoring} actionPending={actionPending} connectionState={connectionState} demoMode={demoMode} actionError={actionError} priorityAdjustment={backendStatus?.priority_adjustment} /></div>
          <ProcessTable filter={filter} onFilterChange={setFilter} onQueryChange={setQuery} query={query} processes={processes} demoMode={demoMode} connected={connectionState === "connected"} connectionState={connectionState} stale={Boolean(snapshot?.stale)} highThreshold={highThreshold} />
          <ActivityLog logs={displayedLogs} demoMode={demoMode} connected={connectionState === "connected"} highThreshold={highThreshold} />
          <footer className="page-footer"><span><span className="footer-heart">✳</span> Built for the Operating Systems Lab</span><span>PROC NANNY <i>·</i> {demoMode ? "DEMO DATA" : connectionState === "connected" ? "LIVE BACKEND DATA" : "NO LIVE DATA"} <i>·</i> PRIORITY ADJUSTMENT {demoMode || !backendStatus?.priority_adjustment?.enabled ? "DISABLED" : backendStatus.priority_adjustment.dry_run ? "DRY RUN" : "ENABLED"}</span></footer>
              </>}
        </div>
      </main>
      {showDemoInfo && <DemoModal onClose={() => setShowDemoInfo(false)} demoMode={demoMode} />}
    </div>
  );
}
