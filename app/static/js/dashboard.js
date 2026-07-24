/* ═══════════════════════════════════════════════════════════════════════════
   MC Router UI — Dashboard JavaScript
   All logic extracted from index.html — English UI
   ═══════════════════════════════════════════════════════════════════════════ */

'use strict';

// ── Constants injected by template (window.MC_UI_CONFIG) ─────────────────────
const IS_ADMIN   = window.MC_UI_CONFIG?.isAdmin ?? false;
const USER_ID    = window.MC_UI_CONFIG?.userId ?? 0;
const USER_PERMS = new Set(window.MC_UI_CONFIG?.userPerms ?? []);
const ALL_PERMS  = window.MC_UI_CONFIG?.allPerms ?? [];
const CF_ENABLED = window.MC_UI_CONFIG?.cfEnabled ?? false;

// ── Permission labels ─────────────────────────────────────────────────────────
const PERM_LABELS = {
  see_own_routes:    'View own routes',
  see_all_routes:    'View all routes',
  create_route:      'Create routes',
  edit_own_route:    'Edit own routes',
  delete_own_route:  'Delete own routes',
  see_cloudflare:    'View Cloudflare DNS',
  manage_cloudflare: 'Manage Cloudflare DNS',
  see_servers:       'View Crafty servers',
  manage_servers:    'Control Crafty servers',
  see_all_users:     'View user list',
  manage_users:      'Manage users',
  manage_settings:   'Manage settings',
};

// ══════════════════════════════════════════════════════════════════════════════
// TOAST NOTIFICATIONS
// ══════════════════════════════════════════════════════════════════════════════
function showToast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const icons = { success: '✓', error: '⚠', info: 'ℹ' };
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${icons[type] || 'ℹ'}</span><span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('toast-out');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  }, duration);
}

// ══════════════════════════════════════════════════════════════════════════════
// COPY TO CLIPBOARD
// ══════════════════════════════════════════════════════════════════════════════
async function copyToClipboard(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    const orig = btn.innerHTML;
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>';
    btn.style.color = 'var(--green)';
    setTimeout(() => { btn.innerHTML = orig; btn.style.color = ''; }, 1500);
  } catch {
    showToast('Copy failed', 'error');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// TAB SWITCHING
// ══════════════════════════════════════════════════════════════════════════════
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));

  const content = document.getElementById('tab-' + name);
  const btn = document.getElementById('tab-btn-' + name);
  if (content) content.classList.add('active');
  if (btn) btn.classList.add('active');

  // Update mobile select
  const mSel = document.getElementById('mobile-tab-select');
  if (mSel) mSel.value = name;

  // Lazy-load tab data
  if (name === 'routes') {
    if (CF_ENABLED && (IS_ADMIN || USER_PERMS.has('see_cloudflare'))) loadCfRecords();
    if (IS_ADMIN || USER_PERMS.has('see_servers')) loadCraftyServers();
  }
  if (name === 'settings') loadUsersList();
}

// ══════════════════════════════════════════════════════════════════════════════
// MODAL HELPERS
// ══════════════════════════════════════════════════════════════════════════════
function openModal(id) {
  const el = document.getElementById(id);
  if (el) { el.classList.add('open'); trapFocus(el); }
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('open');
}

// Close on overlay click
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.modal-overlay').forEach(o => {
    o.addEventListener('click', e => { 
      if (e.target === o && o.id !== 'wizard-modal') o.classList.remove('open'); 
    });
  });
});

// Close on Escape
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.open').forEach(m => {
      if (m.id !== 'wizard-modal') m.classList.remove('open');
    });
  }
});

// Basic focus trap
function trapFocus(el) {
  const focusable = el.querySelectorAll('button, input, select, textarea, [tabindex]:not([tabindex="-1"])');
  if (focusable.length) focusable[0].focus();
}

// ══════════════════════════════════════════════════════════════════════════════
// THEME TOGGLE
// ══════════════════════════════════════════════════════════════════════════════
function toggleTheme() {
  const current = document.documentElement.dataset.theme || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('mc-theme', next);
}

// ══════════════════════════════════════════════════════════════════════════════
// ROUTE MODAL (simplified — single hostname + backend input, no confirm step)
// ══════════════════════════════════════════════════════════════════════════════
let craftyServers = [];
let validationTimer = null;
let currentValidation = null;

async function openRouteModal() {
  document.getElementById('route-modal-title').textContent = 'Create Route';
  document.getElementById('route-submit').textContent = 'Create Route';
  document.getElementById('f-route-id').value = '';
  document.getElementById('f-hostname').value = '';
  document.getElementById('f-hostname').disabled = false;
  document.getElementById('f-backend').value = '';
  document.getElementById('f-is-default').checked = false;
  resetValidation();

  // Load Crafty servers for the quick-fill picker
  const picker = document.getElementById('crafty-picker');
  picker.innerHTML = '';
  if (IS_ADMIN || USER_PERMS.has('see_servers')) {
    try {
      const r = await fetch('/api/crafty/servers');
      const d = await r.json();
      if (d.success && d.servers.length) {
        craftyServers = d.servers;
        const label = document.createElement('span');
        label.style.cssText = 'font-size:11px;color:var(--muted);width:100%;margin-bottom:2px;';
        label.textContent = 'Quick-fill from Crafty:';
        picker.appendChild(label);
        d.servers.forEach(s => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'crafty-picker-btn';
          btn.innerHTML = `<div class="dot ${s.running ? 'dot-green' : 'dot-gray'}"></div>${esc(s.name)} <span style="color:var(--muted);">:${s.port}</span>`;
          btn.onclick = () => {
            document.getElementById('f-backend').value = s.container_address;
            triggerValidation();
          };
          picker.appendChild(btn);
        });
      }
    } catch { craftyServers = []; }
  }

  openModal('route-modal');
  setTimeout(() => document.getElementById('f-hostname').focus(), 100);
}

function openEditRouteModal(id, hostname, backend, isDefault) {
  document.getElementById('route-modal-title').textContent = 'Edit Route';
  document.getElementById('route-submit').textContent = 'Save Changes';
  document.getElementById('f-route-id').value = id;
  document.getElementById('f-is-default').checked = isDefault;

  const hostnameInput = document.getElementById('f-hostname');
  hostnameInput.value = (hostname === '__default__' || isDefault) ? '' : hostname;
  hostnameInput.disabled = isDefault;

  document.getElementById('f-backend').value = backend;

  // Load Crafty picker for edit mode too
  const picker = document.getElementById('crafty-picker');
  picker.innerHTML = '';
  if (IS_ADMIN || USER_PERMS.has('see_servers')) {
    fetch('/api/crafty/servers').then(r => r.json()).then(d => {
      if (d.success && d.servers.length) {
        craftyServers = d.servers;
        const label = document.createElement('span');
        label.style.cssText = 'font-size:11px;color:var(--muted);width:100%;margin-bottom:2px;';
        label.textContent = 'Quick-fill from Crafty:';
        picker.appendChild(label);
        d.servers.forEach(s => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'crafty-picker-btn';
          btn.innerHTML = `<div class="dot ${s.running ? 'dot-green' : 'dot-gray'}"></div>${esc(s.name)} <span style="color:var(--muted);">:${s.port}</span>`;
          btn.onclick = () => {
            document.getElementById('f-backend').value = s.container_address;
            triggerValidation();
          };
          picker.appendChild(btn);
        });
      }
    }).catch(() => {});
  }

  resetValidation();
  openModal('route-modal');
  triggerValidation();
}

function closeRouteModal() { closeModal('route-modal'); }

function onDefaultToggle() {
  const checked = document.getElementById('f-is-default').checked;
  const hostnameInput = document.getElementById('f-hostname');
  hostnameInput.disabled = checked;
  if (checked) hostnameInput.value = '';
  triggerValidation();
}

function getEffectiveHostname() {
  const isDefault = document.getElementById('f-is-default').checked;
  if (isDefault) return '__default__';
  return document.getElementById('f-hostname').value.trim().toLowerCase();
}

function getEffectiveBackend() {
  return document.getElementById('f-backend').value.trim();
}

// ── Live validation ──────────────────────────────────────────────────────────
function resetValidation() {
  ['val-format', 'val-cf', 'val-dns', 'val-backend'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = 'validation-item val-neutral';
    el.querySelector('.v-indicator').textContent = '?';
  });
  currentValidation = null;
}

function updateValidation(fieldId, status, message) {
  const el = document.getElementById(fieldId);
  if (!el) return;
  el.className = `validation-item val-${status}`;
  const indicator = el.querySelector('.v-indicator');
  const icons = { checking: '', success: '✓', warning: '⚠', error: '✕' };
  indicator.textContent = icons[status] ?? '?';
  el.title = message;
}

async function performValidation() {
  const routeId  = document.getElementById('f-route-id').value;
  const hostname = getEffectiveHostname();
  const backend  = getEffectiveBackend();
  const isDefault = document.getElementById('f-is-default').checked;

  ['val-format', 'val-cf', 'val-dns', 'val-backend'].forEach(id => updateValidation(id, 'checking', 'Checking…'));

  try {
    let url = `/api/validate-route?hostname=${encodeURIComponent(hostname)}&backend=${encodeURIComponent(backend)}&is_default=${isDefault}`;
    if (routeId) url += `&route_id=${routeId}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error();
    const d = await res.json();
    currentValidation = d;
    updateValidation('val-format',  d.hostname_format.status,   d.hostname_format.message);
    updateValidation('val-cf',      d.cf_zone.status,           d.cf_zone.message);
    updateValidation('val-dns',     d.dns_record.status,        d.dns_record.message);
    updateValidation('val-backend', d.backend_reachable.status, d.backend_reachable.message);
  } catch {
    ['val-format', 'val-cf', 'val-dns', 'val-backend'].forEach(id => updateValidation(id, 'error', 'Validation failed.'));
  }
}

function triggerValidation() {
  clearTimeout(validationTimer);
  validationTimer = setTimeout(performValidation, 500);
}

// Route form submit — direct submit with validation warning
document.addEventListener('DOMContentLoaded', () => {
  const routeForm = document.getElementById('route-form');
  if (!routeForm) return;

  routeForm.addEventListener('submit', e => {
    e.preventDefault();

    const hostname  = getEffectiveHostname();
    const backend   = getEffectiveBackend();
    const isDefault = document.getElementById('f-is-default').checked;

    // Basic required field check
    if (!isDefault && !hostname) { showToast('Hostname is required', 'error'); return; }
    if (!backend) { showToast('Backend server is required', 'error'); return; }

    // Check for validation errors — warn but allow override
    if (currentValidation) {
      const errors = Object.values(currentValidation)
        .filter(c => c.status === 'error')
        .map(c => c.message);
      if (errors.length) {
        const proceed = confirm('Validation issues found:\n\n' + errors.join('\n') + '\n\nSave anyway?');
        if (!proceed) return;
      }
    }

    const routeId = document.getElementById('f-route-id').value;
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = routeId ? `/routes/edit/${routeId}` : '/routes/add';

    const fields = {
      hostname: hostname,
      backend:  backend,
      is_default: isDefault ? 'true' : ''
    };

    Object.entries(fields).forEach(([k, v]) => {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = k; input.value = v;
      form.appendChild(input);
    });

    document.body.appendChild(form);
    form.submit();
  });
});

// ── Route delete confirm ─────────────────────────────────────────────────────
function confirmDeleteRoute(id, hostname) {
  document.getElementById('delete-route-text').textContent =
    `Are you sure you want to delete the route for "${hostname === '__default__' ? '* (default)' : hostname}"? This action cannot be undone.`;
  document.getElementById('delete-route-form').action = `/routes/delete/${id}`;
  openModal('delete-route-modal');
}

// ══════════════════════════════════════════════════════════════════════════════
// HEALTH / STATUS REFRESH
// ══════════════════════════════════════════════════════════════════════════════
async function refreshHealth() {
  const btn = document.querySelector('[onclick="refreshHealth()"]');
  if (btn) { btn.classList.add('loading'); btn.disabled = true; }

  try {
    const rows = document.querySelectorAll('[id^="row-"]');
    await Promise.all([...rows].map(async row => {
      const id = row.id.replace('row-', '');
      try {
        const [r, h_req] = await Promise.all([
          fetch(`/api/health/${id}`),
          fetch(`/api/health/${id}/history`)
        ]);
        const d = await r.json();
        const h_data = await h_req.json();
        
        const healthCell = document.getElementById(`health-cell-${id}`);
        if (healthCell) {
          let dot = document.getElementById(`health-dot-${id}`);
          let text = document.getElementById(`health-text-${id}`);
          let sparkline = document.getElementById(`sparkline-${id}`);
          
          if (!sparkline) {
             const spark = document.createElement('div');
             spark.id = `sparkline-${id}`;
             spark.className = 'sparkline-container';
             spark.style = 'margin-left:auto; width:60px; height:20px; display:flex; align-items:flex-end; gap:1px;';
             healthCell.appendChild(spark);
             sparkline = spark;
          }
          
          if (dot) dot.className  = `dot ${d.healthy ? 'dot-green' : 'dot-red'}`;
          if (text) text.textContent = d.healthy ? 'Reachable' : 'Offline';
          
          if (sparkline && h_data.success && h_data.history) {
             sparkline.innerHTML = h_data.history.map(pt => {
                // simple height scaling based on latency (lower latency = higher bar for goodness, or higher bar = higher latency?)
                // actually, for latency, shorter bar = faster (better). So let's make max 100ms = 100% height.
                const latency = pt.latency_ms || 100;
                const h = pt.healthy ? Math.max(10, Math.min(100, (latency / 100) * 100)) : 100;
                const color = pt.healthy ? 'var(--green)' : 'var(--danger)';
                const title = pt.healthy ? `Reachable: ${pt.latency_ms}ms` : 'Offline';
                return `<div style="width:2px; height:${h}%; background:${color}; opacity:0.8; border-radius:1px;" title="${title}"></div>`;
             }).join('');
          }
        }
      } catch {}
    }));

    // Refresh connections
    const connRes = await fetch('/api/connections');
    const connData = await connRes.json();
    let total = 0;
    Object.entries(connData).forEach(([hostname, count]) => {
      const rows2 = document.querySelectorAll('[data-hostname]');
      rows2.forEach(r => {
        if (r.dataset.hostname === hostname) {
          const connEl = r.querySelector('.conn-count');
          if (connEl) connEl.textContent = count;
        }
      });
      total += count;
    });
    const totalEl = document.getElementById('total-conns');
    if (totalEl) totalEl.textContent = total;

  } catch {}

  if (btn) { btn.classList.remove('loading'); btn.disabled = false; }
}

// Router status polling
async function checkRouterStatus() {
  try {
    const r = await fetch('/api/router-status');
    const d = await r.json();
    const dot  = document.getElementById('router-dot');
    const text = document.getElementById('router-status-text');
    if (!dot || !text) return;
    if (d.online) {
      dot.className  = 'dot dot-green';
      text.textContent = 'mc-router online';
      text.style.color = 'var(--green)';
    } else {
      dot.className  = 'dot dot-red';
      text.textContent = 'mc-router offline';
      text.style.color = 'var(--danger)';
    }
  } catch {}
}

// ══════════════════════════════════════════════════════════════════════════════
// CLOUDFLARE DNS
// ══════════════════════════════════════════════════════════════════════════════
async function loadCfRecords() {
  const tbody = document.getElementById('cf-tbody');
  const countEl = document.getElementById('cf-record-count');
  if (!tbody) return;

  tbody.innerHTML = '<tr class="loading-row"><td colspan="5"><span class="spinner"></span> Loading DNS records…</td></tr>';

  try {
    const r = await fetch('/api/cf/records');
    const d = await r.json();

    if (!d.success) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--danger)">${d.error}</td></tr>`;
      return;
    }

    const canManage = IS_ADMIN || USER_PERMS.has('manage_cloudflare');

    if (!d.records.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--muted)">No DNS records found</td></tr>';
      if (countEl) countEl.textContent = '';
      return;
    }

    if (countEl) countEl.textContent = `${d.records.length} records`;

    tbody.innerHTML = d.records.map(rec => {
      const ts = rec.modified_on ? relTime(rec.modified_on) : '—';
      return `
        <tr>
          <td><span class="mono text-white">${esc(rec.name)}</span></td>
          <td>
            <span class="backend-pill">${esc(rec.content)}</span>
            <button class="copy-btn" onclick="copyToClipboard('${esc(rec.content)}',this)" title="Copy IP">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
              </svg>
            </button>
          </td>
          <td class="text-muted">${rec.ttl === 1 ? 'Auto' : rec.ttl + 's'}</td>
          <td><span class="ts-rel">${ts}</span></td>
          ${canManage ? `<td class="actions-cell">
            <button class="btn btn-danger btn-sm" onclick="deleteCfRecord('${esc(rec.id)}','${esc(rec.name)}')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
              Delete
            </button>
          </td>` : '<td></td>'}
        </tr>`;
    }).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--danger)">Failed to load DNS records</td></tr>`;
  }
}

function openCfCreateModal() {
  document.getElementById('cf-new-hostname').value = '';
  openModal('cf-create-modal');
}

async function createCfRecord() {
  const hostname = document.getElementById('cf-new-hostname').value.trim();
  if (!hostname) { showToast('Hostname is required', 'error'); return; }

  const btn = document.querySelector('#cf-create-modal .btn-blue');
  if (btn) { btn.classList.add('loading'); btn.disabled = true; }

  try {
    const r = await fetch('/api/cf/records', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hostname, ip: '' })
    });
    const d = await r.json();
    if (d.success) {
      closeModal('cf-create-modal');
      showToast(`A-record for ${hostname} created (auto-detected IP)`, 'success');
      loadCfRecords();
    } else {
      showToast(d.error || 'Failed to create record', 'error');
    }
  } catch {
    showToast('Network error', 'error');
  } finally {
    if (btn) { btn.classList.remove('loading'); btn.disabled = false; }
  }
}

async function deleteCfRecord(id, name) {
  if (!confirm(`Delete DNS record for "${name}"?`)) return;
  try {
    const r = await fetch(`/api/cf/records/${id}`, { method: 'DELETE' });
    const d = await r.json();
    if (d.success) {
      showToast(`Record ${name} deleted`, 'success');
      loadCfRecords();
    } else {
      showToast(d.error || 'Delete failed', 'error');
    }
  } catch {
    showToast('Network error', 'error');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// CRAFTY SERVERS
// ══════════════════════════════════════════════════════════════════════════════
async function loadCraftyServers() {
  const tbody  = document.getElementById('crafty-tbody');
  const countEl = document.getElementById('crafty-server-count');
  if (!tbody) return;

  tbody.innerHTML = '<tr class="loading-row"><td colspan="6"><span class="spinner"></span> Loading servers…</td></tr>';

  try {
    const r = await fetch('/api/crafty/servers');
    const d = await r.json();

    if (!d.success) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:32px;color:var(--danger)">${esc(d.error || 'Failed to load')}</td></tr>`;
      return;
    }

    if (d.warning && !d.servers.length) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:32px;color:var(--muted)">Crafty not configured — <a href="#" onclick="switchTab('settings');return false;" style="color:var(--accent)">Go to Settings</a></td></tr>`;
      return;
    }

    const canManage = IS_ADMIN || USER_PERMS.has('manage_servers');

    if (!d.servers.length) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:32px;color:var(--muted)">No servers found</td></tr>';
      if (countEl) countEl.textContent = '';
      return;
    }

    if (countEl) countEl.textContent = `${d.servers.length} server${d.servers.length !== 1 ? 's' : ''}`;

    tbody.innerHTML = d.servers.map(s => {
      const cpu = Math.min(Math.round(s.cpu || 0), 100);
      const ram = Math.min(Math.round(s.mem_percent || 0), 100);
      // Port health indicator
      const portHealth = s.running
        ? (s.port_reachable
            ? `<span class="dot dot-green" style="display:inline-block;margin-left:6px;" title="Port reachable"></span>`
            : `<span class="dot dot-yellow" style="display:inline-block;margin-left:6px;" title="Port unreachable"></span>`)
        : '';

      return `
        <tr id="crafty-row-${esc(s.id)}">
          <td>
            <div class="server-name-cell">
              <div class="server-status-icon ${s.running ? 'server-running' : 'server-stopped'}"></div>
              <span class="text-white">${esc(s.name)}</span>
            </div>
          </td>
          <td>
            <span class="badge ${s.running ? 'badge-online' : 'badge-offline'}">${s.running ? 'Online' : 'Offline'}</span>
          </td>
          <td>
            <span class="player-count">${s.running ? s.online_players : '—'}<span class="max">/${s.max_players}</span></span>
          </td>
          <td>
            <div style="display:flex;flex-direction:column;gap:5px;min-width:100px;">
              <div class="progress-bar-wrap">
                <span style="font-size:10px;width:28px;">CPU</span>
                <div class="progress-bar"><div class="progress-fill cpu" style="width:${cpu}%"></div></div>
                <span>${cpu}%</span>
              </div>
              <div class="progress-bar-wrap">
                <span style="font-size:10px;width:28px;">RAM</span>
                <div class="progress-bar"><div class="progress-fill ram" style="width:${ram}%"></div></div>
                <span>${ram}%</span>
              </div>
            </div>
          </td>
          <td>
            <span class="mono">${esc(String(s.port))}${portHealth}</span>
          </td>
          ${canManage ? `<td class="actions-cell">
            <button class="btn btn-ghost btn-sm" onclick="openCraftyPortModal('${esc(s.id)}','${esc(s.name)}',${s.port})" title="Change port">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
              Port
            </button>
            <button class="btn btn-sm ${s.running ? 'btn-ghost' : 'btn-green'}" onclick="craftyAction('${esc(s.id)}','${s.running ? 'restart' : 'start'}',this)">
              ${s.running
                ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.51"/></svg> Restart'
                : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Start'}
            </button>
            ${s.running ? `<button class="btn btn-danger btn-sm" onclick="craftyAction('${esc(s.id)}','stop',this)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/></svg>
              Stop
            </button>` : ''}
          </td>` : '<td></td>'}
        </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:32px;color:var(--danger)">Failed to load servers</td></tr>';
  }
}

async function craftyAction(serverId, action, btn) {
  const origHTML = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;"></span>';
  try {
    const fd = new FormData();
    fd.append('action', action);
    const r = await fetch(`/api/crafty/servers/${serverId}/action`, { method: 'POST', body: fd });
    const d = await r.json();
    if (d.success) {
      showToast(d.message || `Server ${action} sent`, 'success');
      setTimeout(() => loadCraftyServers(), 2000);
    } else {
      showToast(d.error || 'Action failed', 'error');
      btn.disabled = false;
      btn.innerHTML = origHTML;
    }
  } catch {
    showToast('Network error', 'error');
    btn.disabled = false;
    btn.innerHTML = origHTML;
  }
}

function openCraftyPortModal(serverId, serverName, currentPort) {
  document.getElementById('crafty-port-form').dataset.serverId = serverId;
  document.getElementById('cp-server-name').textContent = serverName;
  document.getElementById('cp-port').value = currentPort || '';
  openModal('crafty-port-modal');
}

async function submitCraftyPort() {
  const form = document.getElementById('crafty-port-form');
  const serverId = form.dataset.serverId;
  const port = document.getElementById('cp-port').value;
  const restart = document.getElementById('cp-restart')?.checked ? '1' : '';
  if (!serverId || !port) { showToast('Port is required', 'error'); return; }

  const fd = new FormData();
  fd.append('port', port);
  if (restart) fd.append('restart', '1');

  try {
    const r = await fetch(`/api/crafty/servers/${serverId}/port`, { method: 'POST', body: fd });
    const d = await r.json();
    if (d.success) {
      closeModal('crafty-port-modal');
      showToast(d.message || 'Port updated', 'success');
      if (d.file_updated) showToast('server.properties updated via volume mount', 'info');
      if (d.api_updated) showToast('Crafty API updated', 'info');
      setTimeout(() => loadCraftyServers(), 1500);
    } else {
      showToast(d.error || 'Port change failed', 'error');
    }
  } catch {
    showToast('Network error', 'error');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// USER MANAGEMENT
// ══════════════════════════════════════════════════════════════════════════════
async function loadUsersList() {
  const tbody = document.getElementById('users-tbody');
  if (!tbody) return;

  tbody.innerHTML = '<tr class="loading-row"><td colspan="5"><span class="spinner"></span> Loading users…</td></tr>';

  try {
    const r = await fetch('/api/users');
    if (!r.ok) { tbody.innerHTML = ''; return; }
    const users = await r.json();
    const canManage = IS_ADMIN || USER_PERMS.has('manage_users');

    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--muted)">No users found</td></tr>';
      return;
    }

    tbody.innerHTML = users.map(u => {
      const isMe = u.id === USER_ID;
      const initials = u.username.slice(0, 2).toUpperCase();
      const createdDate = u.created_at ? u.created_at.split('T')[0] : '—';
      return `
        <tr>
          <td>
            <div style="display:flex;align-items:center;gap:9px;">
              <div class="user-avatar" style="width:30px;height:30px;font-size:12px;">${initials}</div>
              <span class="text-white">${esc(u.username)}${isMe ? ' <span class="text-muted" style="font-size:11px;font-weight:400;">(you)</span>' : ''}</span>
            </div>
          </td>
          <td><span class="badge ${u.role === 'admin' ? 'badge-admin' : 'badge-user'}">${u.role === 'admin' ? 'Admin' : 'User'}</span></td>
          <td>
            ${u.role !== 'admin' && canManage
              ? `<button class="btn btn-ghost btn-sm" onclick="openPermModal(${u.id},'${esc(u.username)}')">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                  Permissions
                </button>`
              : '<span class="text-muted" style="font-size:12px;">Full access</span>'}
          </td>
          <td class="text-muted" style="font-size:12px;">${createdDate}</td>
          ${canManage ? `<td class="actions-cell">
            <button class="btn btn-ghost btn-sm" onclick="openEditUserModal(${u.id},'${esc(u.username)}','${u.role}')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
              Edit
            </button>
            ${!isMe ? `<button class="btn btn-danger btn-sm" onclick="confirmDeleteUser(${u.id},'${esc(u.username)}')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
              Delete
            </button>` : ''}
          </td>` : '<td></td>'}
        </tr>`;
    }).join('');
  } catch {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--danger)">Failed to load users</td></tr>';
  }
}

function openAddUserModal() {
  document.getElementById('user-modal-title').textContent = 'Add User';
  document.getElementById('user-form').action = '/users/add';
  document.getElementById('u-username').value = '';
  document.getElementById('u-password').value = '';
  document.getElementById('u-role').value = 'user';
  document.getElementById('u-password-label').textContent = 'Password';
  document.getElementById('u-password').required = true;
  document.getElementById('u-password-hint').textContent = 'Choose a secure password (min. 6 characters).';
  document.getElementById('user-modal-submit').textContent = 'Create User';
  openModal('user-modal');
}

function openEditUserModal(id, username, role) {
  document.getElementById('user-modal-title').textContent = 'Edit User';
  document.getElementById('user-form').action = `/users/edit/${id}`;
  document.getElementById('u-username').value = username;
  document.getElementById('u-password').value = '';
  document.getElementById('u-role').value = role;
  document.getElementById('u-password-label').textContent = 'New Password (leave blank to keep)';
  document.getElementById('u-password').required = false;
  document.getElementById('u-password-hint').textContent = 'Leave blank to keep the current password.';
  document.getElementById('user-modal-submit').textContent = 'Save Changes';
  openModal('user-modal');
}

function confirmDeleteUser(id, username) {
  document.getElementById('delete-user-text').textContent =
    `Are you sure you want to delete the user "${username}"? All their routes will remain but become ownerless.`;
  document.getElementById('delete-user-form').action = `/users/delete/${id}`;
  openModal('delete-user-modal');
}

// ── Permission editor ─────────────────────────────────────────────────────────
async function openPermModal(userId, username) {
  document.getElementById('perm-modal-username').textContent = `Editing permissions for: ${username}`;
  document.getElementById('perm-user-id').value = userId;
  const grid = document.getElementById('perm-grid');
  grid.innerHTML = '<div style="color:var(--muted);font-size:13px;">Loading…</div>';
  openModal('perm-modal');

  try {
    const r = await fetch(`/api/permissions/${userId}`);
    const d = await r.json();
    const userPerms = new Set(d.permissions || []);

    grid.innerHTML = ALL_PERMS.map(perm => {
      const active = userPerms.has(perm);
      return `
        <label class="perm-item ${active ? 'active' : ''}" data-perm="${perm}" onclick="togglePerm(this)">
          <input type="checkbox" ${active ? 'checked' : ''}>
          <div class="perm-check"></div>
          <span>${PERM_LABELS[perm] || perm}</span>
        </label>`;
    }).join('');
  } catch {
    grid.innerHTML = '<div style="color:var(--danger);font-size:13px;">Failed to load permissions</div>';
  }
}

function togglePerm(el) {
  el.classList.toggle('active');
  const cb = el.querySelector('input');
  if (cb) cb.checked = !cb.checked;
}

async function savePermissions() {
  const userId = document.getElementById('perm-user-id').value;
  const perms = [...document.querySelectorAll('#perm-grid .perm-item.active')]
    .map(el => el.dataset.perm);

  try {
    const r = await fetch(`/api/permissions/${userId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ permissions: perms })
    });
    const d = await r.json();
    if (d.success) {
      closeModal('perm-modal');
      showToast('Permissions saved successfully', 'success');
      loadUsersList();
    } else {
      showToast('Failed to save permissions', 'error');
    }
  } catch {
    showToast('Network error', 'error');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// UTILITIES
// ══════════════════════════════════════════════════════════════════════════════
function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function relTime(isoString) {
  try {
    const diff = (Date.now() - new Date(isoString).getTime()) / 1000;
    if (diff < 60)   return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
    return `${Math.floor(diff / 86400)} d ago`;
  } catch { return ''; }
}

// ══════════════════════════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
  // Check router status immediately and every 30s
  checkRouterStatus();
  setInterval(checkRouterStatus, 30000);

  // Handle ?tab= URL parameter (e.g. after settings redirect)
  const params = new URLSearchParams(window.location.search);
  const tabParam = params.get('tab');
  const initialTab = (tabParam === 'settings') ? 'settings' : 'routes';
  switchTab(initialTab);

  // Flash message toasts from query params
  if (params.get('success')) showToast(decodeURIComponent(params.get('success')), 'success');
  if (params.get('error'))   showToast(decodeURIComponent(params.get('error')), 'error');
  if (params.get('pw_success')) showToast('Password updated successfully', 'success');
  if (params.get('pw_error'))   showToast(decodeURIComponent(params.get('pw_error')), 'error');

  // Clean URL params without reload
  if (params.toString()) {
    const clean = window.location.pathname;
    window.history.replaceState({}, '', clean);
  }

  // Flash message auto-dismiss (server-rendered flash elements)
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => {
      el.style.transition = 'opacity 0.4s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 400);
    }, 5000);
  });
});
