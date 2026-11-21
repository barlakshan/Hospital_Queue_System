/* =====================================================
   Hospital queue — Staff Console JS
   Real-time queue management with WebSocket + AJAX
   ===================================================== */

const OfficerConsole = (() => {
  let socket = null;
  let currentCounterId = null;

  /* ── Bootstrap ── */
  function init(counterId, branchId) {
    currentCounterId = counterId;
    connectSocket(branchId);
    bindButtons();
    loadWaitingQueue();
    startHeartbeat();
  }

  /* ── Socket.IO ── */
  function connectSocket(branchId) {
    if (typeof io !== 'function') {
      setStatus('disconnected');
      return;
    }

    socket = io({ transports: ['websocket', 'polling'] });

    socket.on('connect', () => {
      socket.emit('join_branch', { branch_id: branchId });
      setStatus('connected');
    });

    socket.on('disconnect', () => setStatus('disconnected'));

    socket.on('queue_update', (data) => {
      updateWaitingCount(data.waiting_count);
      refreshQueue();
    });

    socket.on('token_called', (data) => {
      if (data.counter_id === currentCounterId) {
        renderServing(data);
      }
    });
  }

  /* ── Call next ── */
  function callNext() {
    const btn = document.getElementById('btn-call-next');
    btn.disabled = true;
    btn.textContent = 'Calling…';

    fetch('/api/queue/call-next', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ counter_id: currentCounterId }),
    })
    .then(async r => {
      if (r.status === 204) {
        showToast('No patients waiting', 'info');
        return null;
      }
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || 'Unable to call the next patient');
      return data;
    })
    .then(data => {
      if (data) renderServing(data);
    })
    .catch(error => showToast(error.message || 'Network error — try again', 'error'))
    .finally(() => {
      btn.disabled = false;
      btn.innerHTML = '<i class="ti ti-phone-call"></i> Call next';
    });
  }

  /* ── Complete service ── */
  function completeService(tokenId) {
    const summaryInput = document.getElementById(`condition-summary-${tokenId}`);
    const conditionSummary = summaryInput ? summaryInput.value.trim() : '';
    if (!conditionSummary) {
      showToast('Enter a brief condition summary before completing the appointment', 'warning');
      if (summaryInput) summaryInput.focus();
      return;
    }
    fetch(`/api/queue/token/${tokenId}/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ condition_summary: conditionSummary }),
    })
    .then(r => r.json())
    .then(data => {
      if (!data || !data.message) throw new Error(data?.error || 'Unable to complete service');
      showToast('Service completed', 'success');
      clearServing();
      refreshQueue();
    })
    .catch(error => showToast(error.message || 'Network error — try again', 'error'));
  }

  /* ── Mark no-show ── */
  function markNoShow(tokenId) {
    fetch(`/api/queue/token/${tokenId}/no-show`, { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (!data || !data.message) throw new Error(data?.error || 'Unable to mark no-show');
      showToast('Marked as no-show', 'warning');
      clearServing();
      refreshQueue();
    })
    .catch(error => showToast(error.message || 'Network error — try again', 'error'));
  }

  /* ── Priority upgrade ── */
  function setPriority(tokenId, reason) {
    fetch(`/api/queue/token/${tokenId}/priority`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ priority_reason: reason }),
    })
    .then(r => r.json())
    .then(() => {
      showToast('Priority set', 'success');
      refreshQueue();
    });
  }

  /* ── Load waiting queue ── */
  function loadWaitingQueue() {
    fetch('/api/queue/waiting')
    .then(async response => {
      if (!response.ok) throw new Error('Unable to load the waiting queue');
      const tokens = await response.json();
      if (!Array.isArray(tokens)) throw new Error('Invalid waiting queue response');
      renderQueue(tokens);
    })
    .catch(error => {
      const el = document.getElementById('queue-list');
      if (el) {
        el.innerHTML = '';
        const message = document.createElement('div');
        message.className = 'text-center py-4 text-muted';
        message.textContent = 'Waiting queue could not be loaded. Please retry.';
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'btn-pb-outline mt-2';
        retry.textContent = 'Retry';
        retry.addEventListener('click', loadWaitingQueue);
        el.append(message, retry);
      }
      console.error('Failed to load waiting queue:', error);
    });
  }

  function refreshQueue() {
    setTimeout(loadWaitingQueue, 300);
  }

  /* ── Render helpers ── */
  function renderServing(data) {
    const el = document.getElementById('serving-section');
    if (!el) return;
    el.innerHTML = `
      <div class="pb-ticket mb-3">
        <div class="pb-ticket-service">${data.service}</div>
        <div class="pb-ticket-number">${data.token_number}</div>
        <div class="pb-ticket-meta">
          <div class="pb-ticket-meta-box">
            <div class="pb-ticket-meta-val">${data.customer_name.split(' ')[0]}</div>
            <div class="pb-ticket-meta-lbl">Customer</div>
          </div>
          <div class="pb-ticket-meta-box">
            <div class="pb-ticket-meta-val">${data.counter || '—'}</div>
            <div class="pb-ticket-meta-lbl">Counter</div>
          </div>
          ${data.is_priority ? `<div class="pb-ticket-meta-box" style="background:rgba(255,203,71,.25);">
            <div class="pb-ticket-meta-val" style="font-size:12px;">★ ${data.priority_reason}</div>
            <div class="pb-ticket-meta-lbl">Priority</div>
          </div>` : ''}
        </div>
      </div>
      <div class="mb-3">
        <label for="condition-summary-${data.token_id}" class="pb-form-label">
          Brief condition summary
        </label>
        <textarea id="condition-summary-${data.token_id}"
                  class="pb-form-control" rows="3" maxlength="500"
                  placeholder="Record the patient's condition before completing the appointment"></textarea>
      </div>
      <button class="btn-pb-success w-100 mb-2" onclick="OfficerConsole.completeService(${data.token_id})">
        <i class="ti ti-check"></i> Mark complete
      </button>
      <button class="btn-pb-outline w-100" onclick="OfficerConsole.markNoShow(${data.token_id})">
        <i class="ti ti-user-off"></i> No-show
      </button>
    `;
    // Voice announcement if priority
    if (data.is_priority && 'speechSynthesis' in window) {
      const msg = new SpeechSynthesisUtterance(
        `Token ${data.token_number.split('').join(' ')}, ${data.customer_name}, please proceed to ${data.counter}. Priority customer.`
      );
      msg.lang = 'en-US';
      window.speechSynthesis.speak(msg);
    }
  }

  function clearServing() {
    const el = document.getElementById('serving-section');
    if (el) el.innerHTML = `
      <div class="text-center py-4 text-muted">
        <i class="ti ti-armchair-2" style="font-size:32px;opacity:.3;display:block;margin-bottom:8px;"></i>
        No patient being seen<br>
        <small>Press "Call next" to serve the next patient</small>
      </div>`;
  }

  function renderQueue(tokens) {
    const el = document.getElementById('queue-list');
    if (!el) return;
    updateWaitingCount(tokens.length);

    if (!tokens.length) {
      el.innerHTML = `<div class="text-center py-4 text-muted">
        <i class="ti ti-mood-happy" style="font-size:28px;opacity:.3;display:block;margin-bottom:6px;"></i>
        Queue is empty
      </div>`;
      return;
    }

    el.innerHTML = tokens.map((t, i) => `
      <div class="pb-queue-item ${t.is_priority ? 'priority just-added' : ''}" data-id="${t.id}">
        <div class="pb-queue-token">${t.token_number}</div>
        <div class="pb-queue-info">
          <div class="pb-queue-name">${t.customer_name}</div>
          <div class="pb-queue-svc">${t.service}</div>
        </div>
        ${t.is_priority
          ? `<span class="pb-priority-badge">★ ${t.priority_reason}</span>`
          : `<span style="font-size:11px;color:#aaa;">~${(i + 1) * 8}m</span>`
        }
      </div>
    `).join('');

    // Remove flash class after animation
    setTimeout(() => {
      document.querySelectorAll('.just-added').forEach(el => el.classList.remove('just-added'));
    }, 700);
  }

  function updateWaitingCount(count) {
    const el = document.getElementById('waiting-count');
    if (el) el.textContent = count;
    const badge = document.getElementById('sidebar-waiting-count');
    if (badge) badge.textContent = count;
    const queueCount = document.getElementById('queue-count');
    if (queueCount) queueCount.textContent = count;
  }

  /* ── UI helpers ── */
  function setStatus(status) {
    const map = {
      connected:    { text: 'Online',        cls: 'green' },
      disconnected: { text: 'Reconnecting…', cls: 'red'  },
    };
    const s = map[status];
    const markup = `<span class="pb-online-dot" style="background:${s.cls === 'red' ? 'var(--pb-red)' : 'var(--pb-green)'}"></span> ${s.text}`;
    ['navbar-connection-status', 'connection-status'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = markup;
    });
  }

  function showToast(message, type = 'info') {
    const colours = {
      success: 'var(--pb-green-light)',
      error:   'var(--pb-red-light)',
      warning: 'var(--pb-yellow-light)',
      info:    'var(--pb-dark-light)',
    };
    const toast = document.createElement('div');
    toast.style.cssText = `
      position:fixed;bottom:20px;right:20px;z-index:9999;
      background:${colours[type]};border-radius:8px;
      padding:12px 16px;font-size:13px;
      box-shadow:0 4px 16px rgba(0,0,0,.12);
      color:var(--pb-dark);animation:slideUp .2s ease;
    `;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

  function bindButtons() {
    const callBtn = document.getElementById('btn-call-next');
    if (callBtn) callBtn.addEventListener('click', callNext);
  }

  function startHeartbeat() {
    setInterval(loadWaitingQueue, 30000);
  }

  return { init, callNext, completeService, markNoShow, setPriority };
})();
