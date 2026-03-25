/* OptiK8s UI – client-side logic */

// ── Helpers ─────────────────────────────────────────────────────────────────

function showLoading(msg = 'Working…') {
  document.getElementById('loadingMsg').textContent = msg;
  document.getElementById('loadingOverlay').classList.remove('d-none');
}

function hideLoading() {
  document.getElementById('loadingOverlay').classList.add('d-none');
}

function toast(msg, type = 'info') {
  const container = document.getElementById('toastContainer');
  const id = 'toast-' + Date.now();
  const colourMap = { success: 'success', error: 'danger', info: 'info', warning: 'warning' };
  const colour = colourMap[type] || 'info';
  const html = `
    <div id="${id}" class="toast align-items-center text-bg-${colour} border-0" role="alert">
      <div class="d-flex">
        <div class="toast-body small">${msg}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
      </div>
    </div>`;
  container.insertAdjacentHTML('beforeend', html);
  const el = document.getElementById(id);
  new bootstrap.Toast(el, { delay: 5000 }).show();
  el.addEventListener('hidden.bs.toast', () => el.remove());
}

async function apiPost(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

async function apiGet(url) {
  const r = await fetch(url);
  return r.json();
}

function getTargetCtx() {
  const sel = document.getElementById('targetCtx');
  return sel ? sel.value : '';
}

// ── Cluster actions ──────────────────────────────────────────────────────────

async function createCluster() {
  const provider = document.querySelector('input[name="createProvider"]:checked').value;
  const name = document.getElementById('createName').value.trim();
  const region = document.getElementById('createRegion').value.trim();

  if (!name) { toast('Cluster name is required', 'warning'); return; }

  const msgs = { kind: `Creating KIND cluster "${name}"…`, eks: `Creating EKS cluster "${name}" in ${region} (this may take ~15 min)…` };
  showLoading(msgs[provider]);

  try {
    const result = await apiPost('/api/cluster/create', { provider, name, region });
    if (result.success) {
      toast(`Cluster "${name}" created successfully!`, 'success');
      setTimeout(() => location.reload(), 1500);
    } else {
      toast('Failed to create cluster. Check the terminal for details.', 'error');
    }
  } catch (e) {
    toast('Request failed: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

async function deleteCluster(provider, name) {
  if (!confirm(`Delete cluster "${name}"? This cannot be undone.`)) return;

  showLoading(`Deleting ${provider.toUpperCase()} cluster "${name}"…`);
  try {
    const result = await apiPost('/api/cluster/delete', { provider, name });
    if (result.success) {
      toast(`Cluster "${name}" deleted.`, 'success');
      setTimeout(() => location.reload(), 1200);
    } else {
      toast(`Failed to delete cluster "${name}".`, 'error');
    }
  } catch (e) {
    toast('Request failed: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

async function useCluster(provider, name) {
  showLoading(`Switching context to "${name}"…`);
  try {
    const result = await apiPost('/api/cluster/use', { provider, name });
    if (result.success) {
      toast(`Switched to cluster "${name}".`, 'success');
      setTimeout(() => location.reload(), 1000);
    } else {
      toast('Failed to switch context. ' + (result.stderr || ''), 'error');
    }
  } catch (e) {
    toast('Request failed: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

async function refreshClusters() {
  showLoading('Refreshing cluster list…');
  try {
    await apiGet('/api/cluster/list');
    location.reload();
  } finally {
    hideLoading();
  }
}

async function refreshNodes() {
  const ctx = getTargetCtx();
  showLoading('Loading nodes…');
  try {
    const data = await apiGet('/api/cluster/nodes' + (ctx ? `?context=${encodeURIComponent(ctx)}` : ''));
    const div = document.getElementById('nodeList');
    if (!data.nodes || data.nodes.length === 0) {
      div.innerHTML = '<p class="text-muted small mb-0">No nodes found (or kubectl not configured).</p>';
      return;
    }
    const rows = data.nodes.map(n => {
      const icon = n.ready
        ? '<i class="bi bi-check-circle-fill node-ready me-1"></i>'
        : '<i class="bi bi-x-circle-fill node-notready me-1"></i>';
      const roles = (n.roles || []).join(', ');
      return `<div class="d-flex justify-content-between align-items-center mb-1 small">
                <span>${icon}<span class="fw-semibold">${n.name}</span></span>
                <span class="text-muted">${roles}</span>
              </div>`;
    }).join('');
    div.innerHTML = `<p class="text-muted small mb-2">Context: <code>${data.context}</code></p>` + rows;
  } catch (e) {
    document.getElementById('nodeList').innerHTML = '<p class="text-danger small mb-0">Error loading nodes.</p>';
  } finally {
    hideLoading();
  }
}

// ── App actions ──────────────────────────────────────────────────────────────

async function deployApp(appName) {
  const ctx = getTargetCtx();
  showLoading(`Deploying ${appName}…`);
  try {
    const result = await apiPost('/api/app/deploy', { app: appName, context: ctx || null });
    if (result.success) {
      toast(`"${appName}" deployed successfully.`, 'success');
      await loadAppStatuses();
    } else {
      toast(`Failed to deploy "${appName}": ` + (result.error || result.stderr || ''), 'error');
    }
  } catch (e) {
    toast('Request failed: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

async function removeApp(appName) {
  if (!confirm(`Remove "${appName}" from the cluster?`)) return;
  const ctx = getTargetCtx();
  showLoading(`Removing ${appName}…`);
  try {
    const result = await apiPost('/api/app/remove', { app: appName, context: ctx || null });
    if (result.success) {
      toast(`"${appName}" removed.`, 'success');
      await loadAppStatuses();
    } else {
      toast(`Failed to remove "${appName}".`, 'error');
    }
  } catch (e) {
    toast('Request failed: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

async function deployAll() {
  if (!confirm('Deploy all sample apps to the selected cluster?')) return;
  const ctx = getTargetCtx();
  showLoading('Deploying all apps…');
  try {
    const results = await apiPost('/api/app/deploy', { all: true, context: ctx || null });
    const succeeded = Object.values(results).filter(r => r.success).length;
    const total = Object.keys(results).length;
    toast(`Deployed ${succeeded}/${total} apps.`, succeeded === total ? 'success' : 'warning');
    await loadAppStatuses();
  } catch (e) {
    toast('Request failed: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

async function loadAppStatuses() {
  const ctx = getTargetCtx();
  try {
    const statuses = await apiGet('/api/app/status' + (ctx ? `?context=${encodeURIComponent(ctx)}` : ''));
    for (const [appName, status] of Object.entries(statuses)) {
      const el = document.getElementById(`status-${appName}`);
      if (!el) continue;
      if (status.deployed) {
        const pods = status.pods || [];
        const runningCount = pods.filter(p => p.phase === 'Running').length;
        const html = pods.length
          ? pods.map(p => {
              const cls = p.phase === 'Running' ? 'status-badge-running'
                        : p.phase === 'Pending'  ? 'status-badge-pending'
                        : 'status-badge-error';
              return `<span class="badge status-badge ${cls}">${p.phase} (${p.ready})</span>`;
            }).join(' ')
          : '<span class="badge status-badge status-badge-notfound">no pods</span>';
        el.innerHTML = html;
      } else {
        el.innerHTML = '<span class="badge status-badge status-badge-notfound">not deployed</span>';
      }
    }
  } catch (err) {
    console.error('Failed to load app statuses:', err);
  }
}

// ── Reactive UI ──────────────────────────────────────────────────────────────

// Toggle EKS region field based on provider selection
document.querySelectorAll('input[name="createProvider"]').forEach(radio => {
  radio.addEventListener('change', () => {
    const eksOpts = document.getElementById('eksOptions');
    if (eksOpts) {
      eksOpts.classList.toggle('d-none', radio.value !== 'eks' || !radio.checked);
    }
  });
});

// Auto-load app statuses on page load
document.addEventListener('DOMContentLoaded', () => loadAppStatuses());
