const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
      signal: options.signal ?? AbortSignal.timeout(5000),
    });
  } catch {
    throw new Error("Cannot reach the ProcNanny backend (request failed or timed out).");
  }

  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") {
        detail = ` ${payload.detail}`;
      } else if (Array.isArray(payload.detail)) {
        detail = ` ${payload.detail.map((item) => item.msg).filter(Boolean).join("; ")}`;
      }
    } catch {
      detail = "";
    }
    throw new Error(`ProcNanny API request failed (${response.status}).${detail}`);
  }

  return response.json();
}

export async function fetchDashboardData() {
  const [status, processSnapshot, logResult, priorityActionResult, priorityEvaluationResult] = await Promise.all([
    request("/api/status"),
    request("/api/processes"),
    request("/api/logs?limit=50"),
    request("/api/priority/actions?limit=200"),
    request("/api/priority/evaluations?limit=1000"),
  ]);

  return {
    status,
    processSnapshot,
    logs: logResult.logs,
    priorityActions: priorityActionResult.actions,
    priorityEvaluations: priorityEvaluationResult.evaluations,
  };
}

export async function fetchPriorityData() {
  const [status, processSnapshot, actionResult, evaluationResult] = await Promise.all([
    request("/api/status"),
    request("/api/processes"),
    request("/api/priority/actions?limit=200"),
    request("/api/priority/evaluations?limit=1000"),
  ]);

  return {
    status,
    processSnapshot,
    priorityActions: actionResult.actions,
    priorityEvaluations: evaluationResult.evaluations,
  };
}

export async function fetchPriorityActions() {
  const result = await request("/api/priority/actions?limit=200");
  return result.actions;
}

export function setMonitoringEnabled(enabled) {
  const action = enabled ? "start" : "stop";
  return request(`/api/monitor/${action}`, { method: "POST" });
}

export function updateSettings(settings) {
  return request("/api/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}
