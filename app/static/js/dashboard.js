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
const DOCKER_ENABLED = window.MC_UI_CONFIG?.dockerEnabled ?? false;

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
  const icon = document.createElement('span');
  icon.textContent = icons[type] || 'ℹ';
  const content = document.createElement('span');
  content.textContent = String(message);
  toast.append(icon, content);
  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('toast-out');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  }, duration);
}

// ══════════════════════════════════════════════════════════════════════════════
// SKELETON LOADING ROWS
// ══════════════════════════════════════════════════════════════════════════════
function skeletonRows(widths) {
  const rows = [];
  for (let i = 0; i < 4; i++) {
    const cells = widths.map(w => `<td><div class="skeleton-cell w${w}"></div></td>`).join('');
    rows.push(`<tr class="skeleton-row">${cells}</tr>`);
  }
  return rows.join('');
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
  document.querySelectorAll('.tab-btn').forEach(tab => {
    tab.setAttribute('aria-selected', tab === btn ? 'true' : 'false');
    tab.setAttribute('tabindex', tab === btn ? '0' : '-1');
  });

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
  if (el) {
    modalReturnFocus = document.activeElement;
    activeModal = el;
    el.classList.add('open');
    trapFocus(el);
  }
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (el) {
    el.classList.remove('open');
    if (activeModal === el) {
      activeModal = null;
      if (modalReturnFocus && typeof modalReturnFocus.focus === 'function') {
        modalReturnFocus.focus();
      }
      modalReturnFocus = null;
    }
  }
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
      if (m.id !== 'wizard-modal') closeModal(m.id);
    });
  }
  if (e.key === 'Tab' && activeModal) {
    const focusable = [...activeModal.querySelectorAll(
      'button, input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )].filter(el => !el.disabled);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
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
let savedHostname = ''; // preserves hostname when toggling default checkbox
let activeModal = null;
let modalReturnFocus = null;

async function loadZones() {
  const sel = document.getElementById('f-domain');
  if (!sel) return;
  try {
    const r = await fetch('/api/cf/zones');
    const d = await r.json();
    sel.innerHTML = '<option value="">(enter full domain)</option>';
    if (d.success && d.zones.length) {
      d.zones.forEach(z => {
        const opt = document.createElement('option');
        opt.value = z.name;
        opt.textContent = z.name;
        sel.appendChild(opt);
      });
    }
  } catch { /* ignore */ }
}

async function openRouteModal() {
  document.getElementById('route-modal-title').textContent = 'Create Route';
  document.getElementById('route-submit').textContent = 'Create Route';
  document.getElementById('f-route-id').value = '';
  document.getElementById('f-hostname').value = '';
  document.getElementById('f-hostname').disabled = false;
  document.getElementById('f-domain').value = '';
  document.getElementById('f-backend').value = '';
  document.getElementById('f-is-default').checked = false;
  savedHostname = '';
  resetValidation();
  document.getElementById('crafty-picker').innerHTML = '';

  openModal('route-modal');
  setTimeout(() => document.getElementById('f-hostname').focus(), 100);

  // Load zones for domain dropdown (background)
  loadZones();

  // Load Crafty servers for the quick-fill picker (background)
  const picker = document.getElementById('crafty-picker');
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
          if (!s.container_address) return;
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
}

async function openEditRouteModal(id, hostname, backend, isDefault) {
  document.getElementById('route-modal-title').textContent = 'Edit Route';
  document.getElementById('route-submit').textContent = 'Save Changes';
  document.getElementById('f-route-id').value = id;
  document.getElementById('f-is-default').checked = isDefault;

  // Load zones for domain dropdown (needed before parsing hostname)
  await loadZones();

  const displayHostname = (hostname === '__default__' || isDefault) ? '' : hostname;
  const hostnameInput = document.getElementById('f-hostname');
  const domainSel = document.getElementById('f-domain');
  // Parse hostname into subdomain + domain parts
  if (displayHostname && displayHostname.includes('.')) {
    const parts = displayHostname.split('.');
    const tld = parts.pop();
    const sld = parts.pop();
    const domain = sld + '.' + tld;
    // Check if domain matches an option
    const opts = [...domainSel.options].map(o => o.value);
    if (opts.includes(domain)) {
      domainSel.value = domain;
      hostnameInput.value = parts.join('.');
    } else {
      hostnameInput.value = displayHostname;
    }
  } else {
    hostnameInput.value = displayHostname;
  }
  hostnameInput.disabled = isDefault;
  savedHostname = displayHostname;

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
          if (!s.container_address) return;
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

function closeRouteModal() {
  clearTimeout(validationTimer);
  currentValidation = null;
  closeModal('route-modal');
}

function onDefaultToggle() {
  const checked = document.getElementById('f-is-default').checked;
  const hostnameInput = document.getElementById('f-hostname');
  if (checked) {
    savedHostname = hostnameInput.value;
    hostnameInput.value = '';
    hostnameInput.disabled = true;
  } else {
    hostnameInput.value = savedHostname;
    hostnameInput.disabled = false;
  }
  triggerValidation();
}

function getEffectiveHostname() {
  const isDefault = document.getElementById('f-is-default').checked;
  if (isDefault) return '__default__';
  const sub = document.getElementById('f-hostname').value.trim().toLowerCase();
  const domain = document.getElementById('f-domain').value;
  if (domain && sub && !sub.includes('.')) {
    return sub + '.' + domain;
  }
  return sub;
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
  const preview = document.getElementById('hostname-preview');
  if (preview) preview.style.display = 'none';
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
  const modal = document.getElementById('route-modal');
  if (!modal || !modal.classList.contains('open')) return;

  const routeId  = document.getElementById('f-route-id').value;
  const hostname = getEffectiveHostname();
  const backend  = getEffectiveBackend();
  const isDefault = document.getElementById('f-is-default').checked;
  const domain = document.getElementById('f-domain').value;

  ['val-format', 'val-cf', 'val-dns', 'val-backend'].forEach(id => updateValidation(id, 'checking', 'Checking…'));

  try {
    let url = `/api/validate-route?hostname=${encodeURIComponent(hostname)}&backend=${encodeURIComponent(backend)}&is_default=${isDefault}`;
    if (domain) url += `&domain=${encodeURIComponent(domain)}`;
    if (routeId) url += `&route_id=${routeId}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error();
    const d = await res.json();
    currentValidation = d;
    updateValidation('val-format',  d['val-format'].status,   d['val-format'].message);
    updateValidation('val-cf',      d['val-cf'].status,       d['val-cf'].message);
    updateValidation('val-dns',     d['val-dns'].status,      d['val-dns'].message);
    updateValidation('val-backend', d['val-backend'].status,  d['val-backend'].message);

    // Show resolved hostname preview
    const preview = document.getElementById('hostname-preview');
    if (preview) {
      if (d['val-resolved']) {
        preview.textContent = d['val-resolved'];
        preview.style.display = '';
      } else {
        preview.style.display = 'none';
      }
    }

    // Populate zone dropdown from response
    if (d.zones && d.zones.length) {
      const sel = document.getElementById('f-domain');
      const curVal = sel.value;
      sel.innerHTML = '<option value="">(enter full domain)</option>';
      d.zones.forEach(z => {
        const opt = document.createElement('option');
        opt.value = z.name;
        opt.textContent = z.name;
        sel.appendChild(opt);
      });
      if (curVal) sel.value = curVal;
    }
  } catch {
    ['val-format', 'val-cf', 'val-dns', 'val-backend'].forEach(id => updateValidation(id, 'error', 'Validation failed.'));
    const preview = document.getElementById('hostname-preview');
    if (preview) preview.style.display = 'none';
  }
}

function triggerValidation() {
  clearTimeout(validationTimer);
  validationTimer = setTimeout(performValidation, 150);
}

// Route form submit — fetch with JSON body
document.addEventListener('DOMContentLoaded', () => {
  const routeForm = document.getElementById('route-form');
  if (!routeForm) return;

  routeForm.addEventListener('submit', async e => {
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

    const submitBtn = document.getElementById('route-submit');
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner" style="width:14px;height:14px;margin:0;"></span> Saving…';

    try {
      const url = routeId ? `/routes/edit/${routeId}` : '/routes/add';
      const r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          hostname: hostname,
          backend: backend,
          is_default: isDefault ? true : false
        })
      });

      const d = await r.json();
      if (d.success) {
        showToast(d.message || 'Route saved successfully', 'success');
        if (d.warning) showToast(d.warning, 'info', 6000);
        closeRouteModal();
        setTimeout(() => window.location.reload(), 800);
      } else {
        showToast(d.error || 'Failed to save route', 'error');
        submitBtn.disabled = false;
        submitBtn.innerHTML = routeId ? 'Save Changes' : 'Create Route';
      }
    } catch (err) {
      showToast('Network error: ' + err.message, 'error');
      submitBtn.disabled = false;
      submitBtn.innerHTML = routeId ? 'Save Changes' : 'Create Route';
    }
  });
});

// ── Route delete confirm ─────────────────────────────────────────────────────
let deleteRouteId = null;

function confirmDeleteRoute(id, hostname) {
  deleteRouteId = id;
  document.getElementById('delete-route-text').textContent =
    `Are you sure you want to delete the route for "${hostname === '__default__' ? '* (default)' : hostname}"? This action cannot be undone.`;
  openModal('delete-route-modal');
}

async function submitDeleteRoute() {
  if (!deleteRouteId) return;
  const btn = document.getElementById('delete-route-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;margin:0;"></span> Deleting…';

  try {
    const r = await fetch(`/routes/delete/${deleteRouteId}`, { method: 'POST' });
    const d = await r.json();
    if (d.success) {
      closeModal('delete-route-modal');
      showToast(d.message || 'Route deleted', 'success');
      setTimeout(() => window.location.reload(), 800);
    } else {
      showToast(d.error || 'Delete failed', 'error');
      btn.disabled = false;
      btn.innerHTML = 'Delete Route';
    }
  } catch (err) {
    showToast('Network error: ' + err.message, 'error');
    btn.disabled = false;
    btn.innerHTML = 'Delete Route';
  }
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
      if (!/^\d+$/.test(id)) return;
      try {
        const r = await fetch(`/api/health/${id}`);
        const d = await r.json();
        
        const healthCell = document.getElementById(`health-cell-${id}`);
        if (healthCell) {
          let dot = document.getElementById(`health-dot-${id}`);
          let text = document.getElementById(`health-text-${id}`);
          
          if (dot) {
            dot.className = `dot ${d.healthy ? 'dot-green' : 'dot-red'}`;
            if (!d.healthy && d.error) dot.title = d.error; else dot.removeAttribute('title');
          }
          if (text) {
            text.textContent = d.healthy ? 'Reachable' : (d.error ? `Offline — ${d.error}` : 'Offline');
            if (!d.healthy && d.error) text.title = d.error; else text.removeAttribute('title');
          }
        }
      } catch {
        // Individual row health refresh failed - skip silently
      }
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
    const tabCount = document.getElementById('tab-count-routes');
    if (tabCount) tabCount.textContent = total;

  } catch {
    showToast('Failed to refresh health status', 'error');
  }

  if (btn) { btn.classList.remove('loading'); btn.disabled = false; }
}

// ══════════════════════════════════════════════════════════════════════════════
// CLOUDFLARE DNS
// ══════════════════════════════════════════════════════════════════════════════
async function loadCfRecords() {
  const tbody = document.getElementById('cf-tbody');
  const countEl = document.getElementById('cf-record-count');
  if (!tbody) return;

  tbody.innerHTML = skeletonRows([120,120,48,100,80]);

  try {
    const r = await fetch('/api/cf/records');
    const d = await r.json();

    if (!d.success) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--danger)">${esc(d.error || 'Cloudflare request failed')}</td></tr>`;
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
            <button class="copy-btn" data-copy="${esc(rec.content)}" title="Copy IP">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
              </svg>
            </button>
          </td>
          <td class="text-muted">${rec.ttl === 1 ? 'Auto' : rec.ttl + 's'}</td>
          <td><span class="ts-rel">${ts}</span></td>
          ${canManage ? `<td class="actions-cell">
            <button class="btn btn-danger btn-sm" data-cf-id="${esc(rec.id)}" data-cf-name="${esc(rec.name)}">
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

  tbody.innerHTML = skeletonRows([80,64,80,120,64,80]);

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
      const portHealth = s.running
        ? (s.port_reachable
            ? `<span class="dot dot-green" style="display:inline-block;margin-left:6px;" title="Port reachable"></span>`
            : `<span class="dot dot-warn" style="display:inline-block;margin-left:6px;" title="${esc(s.port_error || 'Port unreachable')}"></span>`)
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
            <button class="btn btn-ghost btn-sm" data-crafty-port-id="${esc(s.id)}" data-crafty-port-name="${esc(s.name)}" data-crafty-port="${Number(s.port) || 0}" title="Change port">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
              Port
            </button>
            <button class="btn btn-sm ${s.running ? 'btn-ghost' : 'btn-green'}" data-crafty-action-id="${esc(s.id)}" data-crafty-action="${s.running ? 'restart' : 'start'}">
              ${s.running
                ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.51"/></svg> Restart'
                : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Start'}
            </button>
            ${s.running ? `<button class="btn btn-danger btn-sm" data-crafty-action-id="${esc(s.id)}" data-crafty-action="stop">
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

  const btn = form.querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;margin:0;"></span> Applying…';

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
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Apply Port';
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// USER MANAGEMENT
// ══════════════════════════════════════════════════════════════════════════════
async function loadUsersList() {
  const tbody = document.getElementById('users-tbody');
  if (!tbody) return;

  tbody.innerHTML = skeletonRows([120,64,100,80,80]);

  try {
    const r = await fetch('/api/users');
    if (r.status === 401) {
      window.location.href = '/login';
      return;
    }
    if (!r.ok) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--danger)">Unable to load users. Check your permissions and retry.</td></tr>';
      return;
    }
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
              ? `<button class="btn btn-ghost btn-sm" data-perm-user-id="${u.id}" data-perm-username="${esc(u.username)}">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                  Permissions
                </button>`
              : '<span class="text-muted" style="font-size:12px;">Full access</span>'}
          </td>
          <td class="text-muted" style="font-size:12px;">${createdDate}</td>
          ${canManage ? `<td class="actions-cell">
            <button class="btn btn-ghost btn-sm" data-edit-user-id="${u.id}" data-edit-user-name="${esc(u.username)}" data-edit-user-role="${esc(u.role)}">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
              Edit
            </button>
            ${!isMe ? `<button class="btn btn-danger btn-sm" data-delete-user-id="${u.id}" data-delete-user-name="${esc(u.username)}">
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
  document.getElementById('u-username').value = '';
  document.getElementById('u-password').value = '';
  document.getElementById('u-role').value = 'user';
  document.getElementById('u-password-label').textContent = 'Password';
  document.getElementById('u-password').required = true;
  document.getElementById('u-password-hint').textContent = 'Choose a secure password (min. 6 characters).';
  document.getElementById('user-modal-submit').textContent = 'Create User';
  window._editUserId = null;
  openModal('user-modal');
}

function openEditUserModal(id, username, role) {
  document.getElementById('user-modal-title').textContent = 'Edit User';
  document.getElementById('u-username').value = username;
  document.getElementById('u-password').value = '';
  document.getElementById('u-role').value = role;
  document.getElementById('u-password-label').textContent = 'New Password (leave blank to keep)';
  document.getElementById('u-password').required = false;
  document.getElementById('u-password-hint').textContent = 'Leave blank to keep the current password.';
  document.getElementById('user-modal-submit').textContent = 'Save Changes';
  window._editUserId = id;
  openModal('user-modal');
}

async function submitUserForm() {
  const id = window._editUserId;
  const username = document.getElementById('u-username').value.trim();
  const password = document.getElementById('u-password').value;
  const role = document.getElementById('u-role').value;

  if (!username) { showToast('Username is required', 'error'); return; }
  if (!id && !password) { showToast('Password is required', 'error'); return; }
  if (password && password.length < 6) { showToast('Password must be at least 6 characters', 'error'); return; }

  const btn = document.getElementById('user-modal-submit');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;margin:0;"></span> Saving…';

  try {
    const url = id ? `/users/edit/${id}` : '/users/add';
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, role })
    });
    const d = await r.json();
    if (d.success) {
      closeModal('user-modal');
      showToast(d.message || 'User saved', 'success');
      loadUsersList();
    } else {
      showToast(d.error || 'Failed to save user', 'error');
    }
  } catch (err) {
    showToast('Network error: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = id ? 'Save Changes' : 'Create User';
  }
}

function confirmDeleteUser(id, username) {
  document.getElementById('delete-user-text').textContent =
    `Are you sure you want to delete the user "${username}"? All their routes will remain but become ownerless.`;
  openModal('delete-user-modal');
  window._deleteUserId = id;
}

async function submitDeleteUser() {
  const id = window._deleteUserId;
  if (!id) return;
  const btn = document.getElementById('delete-user-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;margin:0;"></span> Deleting…';
  try {
    const r = await fetch(`/users/delete/${id}`, { method: 'POST' });
    const d = await r.json();
    if (d.success) {
      closeModal('delete-user-modal');
      showToast(d.message || 'User deleted', 'success');
      loadUsersList();
    } else {
      showToast(d.error || 'Delete failed', 'error');
    }
  } catch (err) {
    showToast('Network error: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Delete User';
    window._deleteUserId = null;
  }
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
        <div class="perm-item ${active ? 'active' : ''}" data-perm="${perm}" onclick="togglePerm(this)">
          <div class="perm-check"></div>
          <span>${PERM_LABELS[perm] || perm}</span>
        </div>`;
    }).join('');
  } catch {
    grid.innerHTML = '<div style="color:var(--danger);font-size:13px;">Failed to load permissions</div>';
  }
}

function togglePerm(el) {
  el.classList.toggle('active');
}

async function savePermissions() {
  const userId = document.getElementById('perm-user-id').value;
  const perms = [...document.querySelectorAll('#perm-grid .perm-item.active')]
    .map(el => el.dataset.perm);

  const btn = document.querySelector('#perm-modal .modal-footer .btn-primary');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;margin:0;"></span> Saving…';

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
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Save Permissions';
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

function bindDynamicActions() {
  document.addEventListener('click', event => {
    const target = event.target.closest('[data-copy], [data-cf-id], [data-crafty-port-id], [data-crafty-action-id], [data-perm-user-id], [data-edit-user-id], [data-delete-user-id]');
    if (!target) return;

    if (target.dataset.copy !== undefined) {
      copyToClipboard(target.dataset.copy, target);
    } else if (target.dataset.cfId) {
      deleteCfRecord(target.dataset.cfId, target.dataset.cfName);
    } else if (target.dataset.craftyPortId) {
      openCraftyPortModal(
        target.dataset.craftyPortId,
        target.dataset.craftyPortName,
        Number(target.dataset.craftyPort) || 0,
      );
    } else if (target.dataset.craftyActionId) {
      craftyAction(target.dataset.craftyActionId, target.dataset.craftyAction, target);
    } else if (target.dataset.permUserId) {
      openPermModal(target.dataset.permUserId, target.dataset.permUsername);
    } else if (target.dataset.editUserId) {
      openEditUserModal(
        target.dataset.editUserId,
        target.dataset.editUserName,
        target.dataset.editUserRole,
      );
    } else if (target.dataset.deleteUserId) {
      confirmDeleteUser(target.dataset.deleteUserId, target.dataset.deleteUserName);
    }
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
  bindDynamicActions();
  document.querySelectorAll('[role="tab"]').forEach(tab => {
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const tabs = [...document.querySelectorAll('[role="tab"]')];
      const current = tabs.indexOf(tab);
      const next = event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? tabs.length - 1
          : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault();
      tabs[next].focus();
      switchTab(tabs[next].id.replace('tab-btn-', ''));
    });
  });
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

  // ═══════════════════════════════════════════════════════════════════════════
  // SSE — real-time event stream, replaces polling
  // ═══════════════════════════════════════════════════════════════════════════
  function connectSSE() {
    const evtSource = new EventSource('/api/events');

    evtSource.addEventListener('connected', () => {
      console.debug('[SSE] Connected');
    });

    evtSource.addEventListener('connections', (e) => {
      try {
        const data = JSON.parse(e.data);
        let total = 0;
        Object.entries(data).forEach(([hostname, count]) => {
          const rows = document.querySelectorAll(`[data-hostname="${CSS.escape(hostname)}"]`);
          rows.forEach(row => {
            const connEl = row.querySelector('.conn-count');
            if (connEl) connEl.textContent = count;
          });
          total += count;
        });
        const totalEl = document.getElementById('tab-count-routes');
        if (totalEl) totalEl.textContent = total;
      } catch {}
    });

    evtSource.addEventListener('route-change', () => {
      // Reload the page when routes change (SSE push from add/edit/delete)
      setTimeout(() => window.location.reload(), 1000);
    });

    evtSource.onerror = () => {
      // A session expiry must not leave EventSource reconnecting forever.
      if (evtSource.readyState === EventSource.CLOSED) {
        window.location.href = '/login';
      } else {
        console.debug('[SSE] Connection error, reconnecting...');
      }
    };
  }

  connectSSE();
});