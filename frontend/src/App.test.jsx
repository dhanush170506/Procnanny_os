import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";

const api = vi.hoisted(() => ({
  fetchDashboardData: vi.fn(),
  fetchPriorityActions: vi.fn(),
  fetchPriorityData: vi.fn(),
  setMonitoringEnabled: vi.fn(),
  updateSettings: vi.fn(),
}));

vi.mock("./api.js", () => api);

describe("ProcNanny dashboard demo", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    api.fetchDashboardData.mockReset();
    api.fetchPriorityActions.mockReset();
    api.fetchPriorityActions.mockResolvedValue([]);
    api.fetchPriorityData.mockReset();
    api.setMonitoringEnabled.mockReset();
    api.updateSettings.mockReset();
  });

  it("labels metrics and processes as illustrative demo data", () => {
    render(<App />);

    expect(screen.getByText("DEMO DATA")).toBeInTheDocument();
    expect(screen.getByText(/No live processes are being monitored/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Illustrative CPU usage chart/ })).toBeInTheDocument();
    expect(screen.getByText("python · model_train.py")).toBeInTheDocument();
    expect(screen.getByText(/Sample data · not live/)).toBeInTheDocument();
    expect(screen.getByText("03")).toBeInTheDocument();
    expect(screen.getByText("DISABLED")).toBeInTheDocument();
  });

  it("filters demo processes by name and process type", () => {
    render(<App />);

    fireEvent.change(screen.getByRole("searchbox", { name: "Search processes" }), {
      target: { value: "postgres" },
    });
    expect(screen.getByText("postgres")).toBeInTheDocument();
    expect(screen.queryByText("chrome")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "Search processes" }), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("Filter processes"), {
      target: { value: "High CPU" },
    });
    expect(screen.getByText("python · model_train.py")).toBeInTheDocument();
    expect(screen.queryByText("postgres")).not.toBeInTheDocument();
  });

  it("pauses and resumes only the local demo", () => {
    render(<App />);

    expect(screen.getByRole("status")).toHaveTextContent("Demo on");
    fireEvent.click(screen.getByRole("button", { name: "Pause demo monitoring" }));
    expect(screen.getByRole("status")).toHaveTextContent("Demo paused");
    expect(screen.getByText("Monitoring paused")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume demo monitoring" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Resume demo monitoring" }));
    expect(screen.getByRole("status")).toHaveTextContent("Demo on");
  });

  it("explains the read-only demo and backend integration", () => {
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: /About this demo/ }));
    const dialog = screen.getByRole("dialog", { name: /clearly labeled dashboard demo/i });
    expect(within(dialog).getByText(/not connected to the Python backend/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/The demo cannot change process priorities/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /Got it/ }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("ProcNanny live dashboard", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_DEMO_MODE", "false");
    api.fetchDashboardData.mockReset();
    api.fetchPriorityActions.mockReset();
    api.fetchPriorityActions.mockResolvedValue([]);
    api.fetchPriorityData.mockReset();
    api.setMonitoringEnabled.mockReset();
    api.updateSettings.mockReset();
  });

  it("renders backend measurements/logs and calls the monitor lifecycle API", async () => {
    api.fetchDashboardData.mockResolvedValue({
      status: {
        state: "stopped",
        monitoring: false,
        poll_interval_seconds: 5,
        high_cpu_threshold_percent: 70,
        priority_adjustment_enabled: false,
      },
      processSnapshot: {
        source: "live",
        updated_at: "2026-10-12T06:00:00+00:00",
        stale: false,
        system_cpu_percent: 24.5,
        processes: [{
          pid: 42,
          name: "worker",
          status: "sleeping",
          cpu_percent: 24.5,
          average_cpu_percent: 24.5,
          nice_value: null,
          priority_value: null,
          priority_scheme: "windows_priority_class",
          priority_label: null,
          priority_unavailable_reason: "AccessDenied",
        }, {
          pid: 43,
          name: "svchost.exe",
          status: "running",
          cpu_percent: 10,
          average_cpu_percent: 10,
          nice_value: 32,
          priority_value: 32,
          priority_scheme: "windows_priority_class",
          priority_label: "NORMAL_PRIORITY_CLASS",
          priority_unavailable_reason: null,
        }],
      },
      logs: [],
      priorityActions: [{
        timestamp: "2026-10-12T06:00:00+00:00",
        pid: 42,
        process_name: "worker",
        cpu_percent: 24.5,
        nice_value: 7,
        action: "monitor",
        reason: "periodic_sample",
      }, {
        timestamp: "2026-10-12T06:00:00+00:00",
        pid: 43,
        process_name: "worker-child",
        cpu_percent: 12,
        nice_value: 7,
        action: "monitor",
        reason: "periodic_sample",
      }],
    });
    api.setMonitoringEnabled.mockResolvedValue({ state: "starting", monitoring: true });

    render(<App />);

    expect(await screen.findByText("worker")).toBeInTheDocument();
    expect(screen.getByText("sleeping")).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(screen.getByText("Evaluation active · changes disabled")).toBeInTheDocument();
    expect(screen.getByText("Access denied by the operating system")).toBeInTheDocument();
    expect(screen.getByText("Normal (Windows)")).toBeInTheDocument();
    expect(screen.queryByText("+32")).not.toBeInTheDocument();
    expect(screen.getByText(/worker-child · PID 43/)).toBeInTheDocument();
    expect(screen.getByText("LIVE DATA")).toBeInTheDocument();
    expect(screen.getByText("Actual JSONL records from the backend")).toBeInTheDocument();
    expect(screen.queryByText("python · model_train.py")).not.toBeInTheDocument();
    const startButton = await screen.findByRole("button", { name: "Start monitoring" });
    await vi.waitFor(() => expect(startButton).toBeEnabled());
    fireEvent.click(startButton);
    expect(api.setMonitoringEnabled).toHaveBeenCalledWith(true);
  });

  it("shows backend disconnection without substituting demo process data", async () => {
    api.fetchDashboardData.mockRejectedValue(new Error("Cannot reach the ProcNanny backend."));

    render(<App />);

    expect(await screen.findByText("Backend disconnected")).toBeInTheDocument();
    expect(screen.getByText(/No demo process data is substituted/)).toBeInTheDocument();
    expect(screen.queryByText("python · model_train.py")).not.toBeInTheDocument();
    expect(screen.getByText("Start the backend to view live process data.")).toBeInTheDocument();
  });

  it("shows dry-run status and priority proposals from backend logs", async () => {
    api.fetchDashboardData.mockResolvedValue({
      status: {
        state: "running",
        monitoring: true,
        poll_interval_seconds: 5,
        high_cpu_threshold_percent: 70,
        priority_adjustment_enabled: true,
        priority_adjustment: {
          enabled: true,
          dry_run: true,
          supported: true,
          platform: "linux",
        },
      },
      processSnapshot: {
        source: "live",
        updated_at: "2026-10-12T06:00:00+00:00",
        stale: false,
        system_cpu_percent: 24.5,
        processes: [],
      },
      logs: [{
        timestamp: "2026-10-12T06:00:00+00:00",
        pid: 42,
        process_name: "worker",
        previous_priority: 0,
        requested_priority: 1,
        action: "priority_demote",
        reason: "sustained_high_cpu",
        success: null,
        dry_run: true,
      }],
    });

    render(<App />);

    await screen.findByText("Priority demotion proposed");
    expect(screen.getAllByText("DRY RUN").length).toBeGreaterThan(0);
    expect(screen.getByText("Priority demotion proposed")).toBeInTheDocument();
    expect(screen.getByText(/worker · PID 42 · nice 0 → 1 · dry run/)).toBeInTheDocument();
  });

  it("shows live priority evaluation from backend policy decisions", async () => {
    window.history.replaceState(null, "", "/#priority-management");
    api.fetchDashboardData.mockResolvedValue({
      status: {
        state: "running",
        monitoring: true,
        poll_interval_seconds: 5,
        high_cpu_threshold_percent: 70,
        low_cpu_threshold_percent: 10,
        priority_adjustment: { enabled: true, dry_run: true, supported: true },
      },
      processSnapshot: {
        source: "live",
        updated_at: "2026-10-12T06:00:00+00:00",
        stale: false,
        system_cpu_percent: 82,
        processes: [{
          pid: 42,
          name: "worker",
          create_time: 1234,
          cpu_percent: 82,
          average_cpu_percent: 75,
          nice_value: 0,
        }],
      },
      logs: [],
      priorityActions: [{
        timestamp: "2026-10-12T06:00:00+00:00",
        pid: 42,
        process_name: "worker",
        create_time: 1234,
        previous_priority: 0,
        requested_priority: 1,
        action: "priority_demote",
        reason: "sustained_high_cpu",
        success: null,
        dry_run: true,
      }],
      priorityEvaluations: [{
        timestamp: "2026-10-12T06:00:00+00:00",
        process_name: "worker",
        pid: 42,
        create_time: 1234,
        cpu_percent: 82,
        average_cpu_percent: 75,
        threshold_percent: 70,
        current_priority: { scheme: "unix_nice", value: 0, label: "nice 0" },
        decision: "proposal",
        proposed_priority: { scheme: "unix_nice", value: 1, label: "nice 1" },
        explanation: "Sustained rolling-average CPU above the configured threshold supports one demotion step.",
        eligible: true,
        eligibility_reason: null,
        action: "priority_demote",
        action_status: "proposed",
      }],
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: /Priority management\./i })).toBeInTheDocument();
    expect(screen.getAllByText("Proposed").length).toBeGreaterThan(0);
    expect(screen.getByText("75.0% / 70%")).toBeInTheDocument();
    expect(screen.getAllByText("Proposed").length).toBeGreaterThan(0);
    expect(screen.getByText(/nice 1 · Sustained rolling-average CPU above the configured threshold/)).toBeInTheDocument();
    expect(screen.getAllByText("DRY RUN").length).toBeGreaterThan(0);
  });

  it("refreshes evaluations and action outcomes from the backend APIs", async () => {
    window.history.replaceState(null, "", "/#priority-management");
    const baseData = {
      status: {
        state: "running",
        monitoring: true,
        poll_interval_seconds: 5,
        high_cpu_threshold_percent: 70,
        low_cpu_threshold_percent: 10,
        priority_adjustment: { enabled: true, dry_run: true, supported: true },
      },
      processSnapshot: {
        source: "live",
        updated_at: "2026-10-12T06:00:00+00:00",
        stale: false,
        system_cpu_percent: 82,
        processes: [{
          pid: 42,
          name: "worker",
          create_time: 1234,
          cpu_percent: 82,
          average_cpu_percent: 75,
          nice_value: 0,
        }],
      },
      logs: [],
      priorityActions: [],
      priorityEvaluations: [{
        timestamp: "2026-10-12T06:00:00+00:00",
        process_name: "worker",
        pid: 42,
        create_time: 1234,
        cpu_percent: 82,
        average_cpu_percent: 75,
        threshold_percent: 70,
        current_priority: { scheme: "unix_nice", value: 0, label: "nice 0" },
        decision: "waiting_for_sustained_cpu",
        proposed_priority: null,
        explanation: "Waiting for sustained CPU.",
        eligible: true,
        action_status: "not_executed",
      }],
    };
    api.fetchDashboardData.mockResolvedValue(baseData);
    api.fetchPriorityData.mockResolvedValue({
      ...baseData,
      priorityEvaluations: [{
        ...baseData.priorityEvaluations[0],
        decision: "proposal",
        proposed_priority: { scheme: "unix_nice", value: 1, label: "nice 1" },
        explanation: "Sustained CPU supports a demotion.",
        action_status: "proposed",
      }],
      priorityActions: [{
        timestamp: "2026-10-12T06:00:01+00:00",
        pid: 42,
        create_time: 1234,
        process_name: "worker",
        action: "priority_demote",
        status: "dry_run",
        dry_run: true,
        proposed_priority: 1,
        reason: "Sustained CPU supports a demotion.",
      }],
    });

    render(<App />);

    expect(await screen.findByText("Waiting for sustained CPU.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh evaluations" }));

    expect(await screen.findByText(/nice 1 · Sustained CPU supports a demotion/)).toBeInTheDocument();
    expect(screen.getByText((_, element) => (
      element.tagName === "SMALL" && element.textContent.includes("Latest action:")
    ))).toHaveTextContent("Latest action: Dry-run proposal · priority 1");
    expect(screen.getByText(/Latest backend evaluation and action records refreshed/)).toBeInTheDocument();
    expect(api.fetchPriorityData).toHaveBeenCalledTimes(1);
  });

  it("shows refresh loading and visible API errors", async () => {
    window.history.replaceState(null, "", "/#priority-management");
    api.fetchDashboardData.mockResolvedValue({
      status: {
        state: "running",
        monitoring: true,
        poll_interval_seconds: 5,
        high_cpu_threshold_percent: 70,
        low_cpu_threshold_percent: 10,
        priority_adjustment: { enabled: false, dry_run: true },
      },
      processSnapshot: {
        source: "live",
        updated_at: null,
        stale: false,
        system_cpu_percent: null,
        processes: [],
      },
      logs: [],
      priorityActions: [],
      priorityEvaluations: [],
    });
    let rejectRequest;
    api.fetchPriorityData.mockImplementation(() => new Promise((_, reject) => {
      rejectRequest = reject;
    }));

    render(<App />);

    const refreshButton = await screen.findByRole("button", { name: "Refresh evaluations" });
    fireEvent.click(refreshButton);
    expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled();
    rejectRequest(new Error("Priority evaluation records are unavailable."));

    expect(await screen.findByRole("alert")).toHaveTextContent("Priority evaluation records are unavailable.");
    expect(screen.getByRole("button", { name: "Refresh evaluations" })).toBeEnabled();
  });

  it("renders normal and excluded evaluations from backend results", async () => {
    window.history.replaceState(null, "", "/#priority-management");
    api.fetchDashboardData.mockResolvedValue({
      status: {
        state: "running",
        monitoring: true,
        poll_interval_seconds: 5,
        high_cpu_threshold_percent: 70,
        low_cpu_threshold_percent: 10,
        priority_adjustment: { enabled: false, dry_run: true },
      },
      processSnapshot: {
        source: "live",
        updated_at: "2026-10-12T06:00:00+00:00",
        stale: false,
        system_cpu_percent: 24,
        processes: [
          { pid: 10, name: "normal-worker", create_time: 100, cpu_percent: 10, average_cpu_percent: 10, nice_value: 0 },
          { pid: 11, name: "protected-worker", create_time: 110, cpu_percent: 90, average_cpu_percent: 90, nice_value: 0 },
        ],
      },
      logs: [],
      priorityActions: [],
      priorityEvaluations: [
        {
          timestamp: "2026-10-12T06:00:00+00:00",
          process_name: "normal-worker",
          pid: 10,
          create_time: 100,
          decision: "below_threshold",
          current_priority: { scheme: "unix_nice", value: 0, label: "nice 0" },
          proposed_priority: null,
          explanation: "CPU is below threshold.",
        },
        {
          timestamp: "2026-10-12T06:00:00+00:00",
          process_name: "protected-worker",
          pid: 11,
          create_time: 110,
          decision: "excluded",
          current_priority: { scheme: "unix_nice", value: 0, label: "nice 0" },
          proposed_priority: null,
          explanation: "Process is protected.",
        },
      ],
    });

    render(<App />);

    expect(await screen.findByText("Normal")).toBeInTheDocument();
    expect(screen.getByText("Excluded")).toBeInTheDocument();
    expect(screen.getByText("Evaluation active · priority changes disabled")).toBeInTheDocument();
    expect(screen.getByText("MONITOR STATUS")).toBeInTheDocument();
  });

  it("opens Settings and edits validated backend configuration", async () => {
    const status = {
      state: "stopped",
      monitoring: false,
      settings_persistent: true,
      poll_interval_seconds: 2.5,
      history_window_seconds: 15,
      high_cpu_threshold_percent: 85,
      low_cpu_threshold_percent: 15,
      excluded_processes: ["backup", "worker"],
      priority_adjustment_enabled: false,
      priority_adjustment: {
        enabled: false,
        dry_run: true,
        supported: true,
        platform: "linux",
        cooldown_seconds: 20,
        high_duration_seconds: 10,
        recovery_duration_seconds: 12,
        step: 2,
        min_nice_value: 0,
        max_nice_value: 19,
      },
    };
    api.fetchDashboardData.mockResolvedValue({
      status,
      processSnapshot: { source: "live", updated_at: null, stale: false, system_cpu_percent: null, processes: [] },
      logs: [],
      priorityActions: [],
      priorityEvaluations: [],
    });
    api.updateSettings.mockResolvedValue({
      ...status,
      priority_adjustment_enabled: true,
      priority_adjustment: { ...status.priority_adjustment, enabled: true },
    });

    render(<App />);

    fireEvent.click(screen.getByRole("link", { name: "Settings" }));
    expect(await screen.findByRole("heading", { name: /Active settings/i })).toBeInTheDocument();
    expect(screen.getByText("Disabled")).toBeInTheDocument();
    expect(screen.getByText("backup, worker")).toBeInTheDocument();
    expect(screen.getByLabelText("High CPU threshold (%)")).toHaveValue(85);
    expect(screen.getByLabelText("Monitoring interval (seconds)")).toHaveValue(2.5);
    expect(screen.getByLabelText("Excluded process names (comma-separated)")).toHaveValue("backup, worker");

    fireEvent.click(screen.getByLabelText("Enable automatic priority adjustment"));
    fireEvent.click(screen.getByRole("button", { name: "Save backend settings" }));
    await screen.findByText("Configuration saved and applied to the monitoring pipeline.");
    expect(api.updateSettings).toHaveBeenCalledWith(expect.objectContaining({
      high_cpu_threshold_percent: 85,
      poll_interval_seconds: 2.5,
      excluded_processes: ["backup", "worker"],
      priority_adjustment_enabled: true,
      priority_adjustment_dry_run: true,
      confirm_live_adjustment: false,
    }));
  });

  it("requires explicit confirmation before saving live priority changes", async () => {
    const status = {
      state: "stopped",
      monitoring: false,
      settings_persistent: true,
      poll_interval_seconds: 5,
      history_window_seconds: 30,
      high_cpu_threshold_percent: 70,
      low_cpu_threshold_percent: 10,
      excluded_processes: [],
      priority_adjustment: {
        enabled: false,
        dry_run: true,
        supported: true,
        platform: "win32",
        cooldown_seconds: 30,
        high_duration_seconds: 15,
        recovery_duration_seconds: 15,
        step: 1,
      },
    };
    api.fetchDashboardData.mockResolvedValue({
      status,
      processSnapshot: { source: "live", updated_at: null, stale: false, system_cpu_percent: null, processes: [] },
      logs: [],
      priorityActions: [],
      priorityEvaluations: [],
    });
    api.updateSettings.mockResolvedValue({
      ...status,
      priority_adjustment: { ...status.priority_adjustment, enabled: true, dry_run: false },
    });

    render(<App />);
    fireEvent.click(screen.getByRole("link", { name: "Settings" }));
    fireEvent.click(await screen.findByLabelText("Enable automatic priority adjustment"));
    fireEvent.click(screen.getByLabelText("Dry-run only (never change process priority)"));

    const saveButton = screen.getByRole("button", { name: "Save backend settings" });
    expect(saveButton).toBeDisabled();
    const confirmation = screen.getByLabelText(/I explicitly authorize live priority changes/);
    fireEvent.click(confirmation);
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);

    await screen.findByText("Configuration saved and applied to the monitoring pipeline.");
    expect(api.updateSettings).toHaveBeenCalledWith(expect.objectContaining({
      priority_adjustment_enabled: true,
      priority_adjustment_dry_run: false,
      confirm_live_adjustment: true,
    }));
  });

  it("filters genuine priority history and explains an empty history", async () => {
    window.history.replaceState(null, "", "/#priority-logs");
    api.fetchPriorityActions.mockResolvedValue([{
      event_id: "event-1",
      timestamp: "2026-10-12T06:00:00+00:00",
      pid: 42,
      process_name: "worker",
      create_time: 1234,
      previous_priority: 0,
      requested_priority: 1,
      action: "priority_demote",
      status: "dry_run",
      reason: "sustained_high_cpu",
      success: null,
      dry_run: true,
    }]);
    api.fetchDashboardData.mockResolvedValue({
      status: { state: "stopped", monitoring: false, poll_interval_seconds: 5, high_cpu_threshold_percent: 70, low_cpu_threshold_percent: 10, priority_adjustment: { enabled: false, dry_run: true } },
      processSnapshot: { source: "live", updated_at: null, stale: false, system_cpu_percent: null, processes: [] },
      logs: [],
      priorityActions: [],
    });

    render(<App />);

    expect((await screen.findAllByText("Not recorded")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("Dry-run proposal")).length).toBeGreaterThan(1);
    fireEvent.change(screen.getByLabelText("Filter priority log status"), { target: { value: "Applied" } });
    expect(screen.getByText("No priority events match the selected process search or status filter.")).toBeInTheDocument();
  });

  it("shows the priority-action empty state without inventing events", async () => {
    window.history.replaceState(null, "", "/#priority-logs");
    api.fetchDashboardData.mockResolvedValue({
      status: { state: "stopped", monitoring: false, poll_interval_seconds: 5, high_cpu_threshold_percent: 70, low_cpu_threshold_percent: 10, priority_adjustment: { enabled: false, dry_run: true } },
      processSnapshot: { source: "live", updated_at: null, stale: false, system_cpu_percent: null, processes: [] },
      logs: [{
        timestamp: "2026-10-12T06:00:00+00:00",
        pid: 42,
        process_name: "worker",
        action: "monitor",
        reason: "periodic_sample",
      }],
      priorityActions: [],
    });
    api.fetchPriorityActions.mockResolvedValue([]);

    render(<App />);

    expect(await screen.findByText(/No priority evaluation or action events have been recorded yet/)).toBeInTheDocument();
    expect(screen.queryByText("Process sample recorded")).not.toBeInTheDocument();
  });

  it("searches by process name or PID, filters statuses, sorts newest first, and refreshes", async () => {
    window.history.replaceState(null, "", "/#priority-logs");
    const records = [
      {
        event_id: "old-failure",
        timestamp: "2026-10-12T05:00:00+00:00",
        process_name: "worker-old",
        pid: 42,
        cpu_percent: 88.1,
        previous_priority: 0,
        proposed_priority: 1,
        action: "priority_demote",
        status: "failed",
        reason: "permission denied",
        error: "Access denied",
      },
      {
        event_id: "new-applied",
        timestamp: "2026-10-12T06:00:00+00:00",
        process_name: "worker-new",
        pid: 43,
        cpu_percent: 91.2,
        previous_priority: 0,
        proposed_priority: 1,
        resulting_priority: 1,
        action: "priority_demote",
        status: "applied",
        reason: "verified",
      },
      {
        event_id: "middle-proposal",
        timestamp: "2026-10-12T05:30:00+00:00",
        process_name: "queue",
        pid: 44,
        previous_priority: 0,
        proposed_priority: 2,
        action: "priority_demote",
        status: "dry_run",
        reason: "dry-run decision",
      },
    ];
    api.fetchPriorityActions.mockResolvedValue(records);
    api.fetchDashboardData.mockResolvedValue({
      status: { state: "stopped", monitoring: false, poll_interval_seconds: 5, high_cpu_threshold_percent: 70, low_cpu_threshold_percent: 10, priority_adjustment: { enabled: false, dry_run: true } },
      processSnapshot: { source: "live", updated_at: null, stale: false, system_cpu_percent: null, processes: [] },
      logs: [],
      priorityActions: [],
    });

    render(<App />);

    expect(await screen.findByText("worker-new")).toBeInTheDocument();
    expect(screen.getAllByText("Applied")[0]).toBeInTheDocument();
    expect(screen.getAllByText("Failed").length).toBeGreaterThan(1);
    expect(screen.getAllByText("Dry-run proposal").length).toBeGreaterThan(1);
    expect(screen.getByText("Access denied")).toBeInTheDocument();
    expect(screen.getByText("91.2%")).toBeInTheDocument();
    expect(screen.getByText("worker-new").closest("tr")).toHaveTextContent("1");

    fireEvent.change(screen.getByLabelText("Search priority logs"), { target: { value: "44" } });
    expect(screen.getByText("queue")).toBeInTheDocument();
    expect(screen.queryByText("worker-new")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search priority logs"), { target: { value: "worker-old" } });
    expect(screen.getByText("worker-old")).toBeInTheDocument();
    expect(screen.queryByText("queue")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Search priority logs"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Filter priority log status"), { target: { value: "Applied" } });
    expect(screen.getByText("worker-new")).toBeInTheDocument();
    expect(screen.queryByText("worker-old")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh history" }));
    await vi.waitFor(() => expect(api.fetchPriorityActions).toHaveBeenCalledTimes(2));
  });

  it("shows the loading state and paginates large histories", async () => {
    window.history.replaceState(null, "", "/#priority-logs");
    let resolveHistory;
    api.fetchPriorityActions.mockImplementation(() => new Promise((resolve) => {
      resolveHistory = resolve;
    }));
    api.fetchDashboardData.mockResolvedValue({
      status: { state: "stopped", monitoring: false, poll_interval_seconds: 5, high_cpu_threshold_percent: 70, low_cpu_threshold_percent: 10, priority_adjustment: { enabled: false, dry_run: true } },
      processSnapshot: { source: "live", updated_at: null, stale: false, system_cpu_percent: null, processes: [] },
      logs: [],
      priorityActions: [],
    });

    render(<App />);

    expect(screen.getByText("Loading priority history")).toBeInTheDocument();
    resolveHistory(Array.from({ length: 26 }, (_, index) => ({
      event_id: `event-${index}`,
      timestamp: new Date(Date.UTC(2026, 0, 1, 0, index)).toISOString(),
      pid: index + 1,
      process_name: `worker-${index + 1}`,
      action: "priority_evaluation",
      status: "skipped",
      reason: "below threshold",
    })));

    expect(await screen.findByText("worker-26")).toBeInTheDocument();
    expect(screen.queryByText("worker-1")).not.toBeInTheDocument();
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next priority history page" }));
    expect(screen.getByText("worker-1")).toBeInTheDocument();
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
  });

  it("shows a visible error and retry control when history cannot be loaded", async () => {
    window.history.replaceState(null, "", "/#priority-logs");
    api.fetchPriorityActions.mockRejectedValue(new Error("Priority history service unavailable."));
    api.fetchDashboardData.mockResolvedValue({
      status: { state: "stopped", monitoring: false, poll_interval_seconds: 5, high_cpu_threshold_percent: 70, low_cpu_threshold_percent: 10, priority_adjustment: { enabled: false, dry_run: true } },
      processSnapshot: { source: "live", updated_at: null, stale: false, system_cpu_percent: null, processes: [] },
      logs: [],
      priorityActions: [],
    });

    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Priority history service unavailable.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
