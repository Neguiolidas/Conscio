/* ==========================================================================
   Conscio Observatory v4.5 - frontend
   Estado centralizado, render declarativo, polling por visibilidade.
   ========================================================================== */

(function () {
	'use strict';

	// ── helpers ───────────────────────────────────────────────────────────

	const $ = (sel, root = document) => root.querySelector(sel);
	const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

	function esc(s) {
		return String(s == null ? '' : s)
			.replace(/&/g, '\u0026amp;')
			.replace(/</g, '\u003c')
			.replace(/>/g, '\u003e')
			.replace(/"/g, '\u0022')
			.replace(/'/g, '\u0027');
	}

	function fmtTime(ts) {
		if (!ts) return '—';
		const d = new Date(ts * 1000);
		const now = Date.now();
		const diff = (now / 1000) - ts;
		if (diff < 60) return Math.floor(diff) + 's ago';
		if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
		if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
		return Math.floor(diff / 86400) + 'd ago';
	}

	function fmtNumber(n) {
		if (n == null || n === '') return '—';
		if (n >= 1000) return n.toLocaleString('en-US');
		return String(n);
	}

	function ago(ts) {
		if (!ts) return '—';
		return fmtTime(ts);
	}

	function titleCase(s) {
		return String(s || '').replace(/\b\w/g, c => c.toUpperCase());
	}

	function truncate(s, n) {
		s = String(s || '');
		if (s.length <= n) return s;
		return s.slice(0, n - 1) + '…';
	}

	// ── api (fetch wrapper com timeout + error handling) ─────────────────

	async function getJson(path, opts = {}) {
		const controller = new AbortController();
		const timer = setTimeout(() => controller.abort(), opts.timeout || 8000);
		try {
			const r = await fetch(path, { signal: controller.signal });
			if (!r.ok) {
				throw new Error('HTTP ' + r.status);
			}
			return await r.json();
		} finally {
			clearTimeout(timer);
		}
	}

	// ── state ─────────────────────────────────────────────────────────────

	const state = {
		tab: 'overview',
		visible: !document.hidden,
		polling: new Map(),    // tab name -> interval id
		live: {
			version: null,
			storage: null,
			agents: [],
			allAgents: [],
			halls: [],
			mailboxes: [],
			daemon: null,
			identity: null,
		},
		search: {
			agents: '',
		},
		showOffline: true,
	};

	// ── topbar (live status) ─────────────────────────────────────────────

	function paintTopbar() {
		const lv = state.live;
		const isLive = !!lv.version;
		const pill = $('#status-live');
		pill.classList.toggle('is-live', isLive);
		$('#status-text').textContent = isLive ? 'live' : 'offline';
		$('#version-text').textContent = lv.version ? 'v' + lv.version : 'v—';
		if (lv.storage) {
			const tail = lv.storage.split('/').pop();
			$('#storage-text').textContent = tail || '—';
			$('#storage-text').title = lv.storage;
		}
		$('#brand-meta').textContent = 'Observatory · v' + (lv.version || '—');
	}

	// ── sidebar nav counts ───────────────────────────────────────────────

	function paintCounts() {
		const lv = state.live;
		const ids = { agents: lv.agents.length, halls: lv.halls.length, mailboxes: lv.mailboxes.length };
		$$('[data-count]').forEach(el => {
			const key = el.dataset.count;
			el.textContent = ids[key] || 0;
		});
	}

	// ── rendering: tab content ───────────────────────────────────────────

	function paintPageHead(title, subtitle) {
		$('#page-title').textContent = title;
		$('#page-subtitle').textContent = subtitle || 'live';
	}

	function renderEmpty(title, hint) {
		return `<div class="empty">
			<div class="empty-title">${esc(title)}</div>
			${hint ? `<div class="empty-hint">${esc(hint)}</div>` : ''}
		</div>`;
	}

	function renderSkeleton() {
		return `<div class="grid grid-4">
			${Array(4).fill(0).map(() =>
				`<div class="card"><div class="skeleton" style="width:60%"></div><div style="height:14px"></div><div class="skeleton" style="width:40%"></div></div>`
			).join('')}
		</div>`;
	}

	function kpi(label, value, meta, kind) {
		return `<div class="card kpi">
			<div class="kpi-label">${esc(label)}</div>
			<div class="kpi-value ${value == null || value === '' ? 'is-empty' : ''}">${esc(value)}</div>
			${meta ? `<div class="kpi-meta ${kind || ''}">${esc(meta)}</div>` : ''}
		</div>`;
	}

	function badge(state, label) {
		const map = {
			alive: 'is-live', online: 'is-live', active: 'is-live', ok: 'is-live',
			offline: 'is-offline', stale: 'is-offline', dead: 'is-err',
			warn: 'is-warn', err: 'is-err', error: 'is-err',
		};
		const cls = map[String(state || '').toLowerCase()] || '';
		return `<span class="badge ${cls}">${esc(label || state || '?')}</span>`;
	}

	function relTime(ts) {
		const diff = (Date.now() / 1000) - ts;
		if (!ts) return '—';
		if (diff < 0) return 'agora';
		if (diff < 60) return Math.floor(diff) + 's';
		if (diff < 3600) return Math.floor(diff / 60) + 'm';
		if (diff < 86400) return Math.floor(diff / 3600) + 'h';
		return Math.floor(diff / 86400) + 'd';
	}

	// ── tab renderers ────────────────────────────────────────────────────

	function renderOverview() {
		paintPageHead('Overview', 'live · ' + new Date().toLocaleTimeString('pt-BR'));
		const lv = state.live;
		const liveAgents = lv.agents.length;
		const totalAgents = lv.allAgents.length;
		const hallCount = lv.halls.length;
		const mbTotal = lv.mailboxes.reduce((acc, m) => acc + (m.unread || 0), 0);
		const daemon = lv.daemon || {};
		const identity = lv.identity || {};
		const selfId = identity.instance_id || '—';

		return `
			<section class="grid grid-4">
				${kpi('Agents live', liveAgents, totalAgents ? liveAgents + ' de ' + totalAgents : '—', liveAgents ? 'is-live' : 'is-offline')}
				${kpi('Halls', hallCount, hallCount ? lv.halls.reduce((a, h) => a + (h.member_count || 0), 0) + ' membros totais' : 'nenhum criado', hallCount ? 'is-live' : 'is-offline')}
				${kpi('Mailbox', mbTotal, mbTotal ? mbTotal + ' mensagens não-lidas' : 'caixa vazia', mbTotal ? 'is-live' : 'is-offline')}
				${kpi('Daemon', daemon.running ? 'rodando' : (daemon.running === false ? 'parado' : '—'), daemon.awake ? 'awake · ' + (daemon.cycles || 0) + ' ciclos' : 'idle', daemon.running ? 'is-live' : 'is-offline')}
			</section>

			<section class="grid grid-2" style="margin-top:18px">
				<div class="card">
					<div class="card-head">
						<h3 class="card-title">Identidade local</h3>
					</div>
					<div class="row-list">
						<div class="row">
							<span class="row-id">instance_id</span>
							<span class="badge ${identity.instance_id ? 'is-live' : ''}">${esc(truncate(selfId, 18))}</span>
							<span class="row-meta">${ago(identity.created_ts)}</span>
						</div>
						<div class="row">
							<span class="row-id">label</span>
							<span></span>
							<span class="row-meta">${esc(identity.label || '—')}</span>
						</div>
					</div>
				</div>

				<div class="card">
					<div class="card-head">
						<h3 class="card-title">Mailbox não-lido</h3>
						<span class="card-meta">${lv.mailboxes.length} peers</span>
					</div>
					${lv.mailboxes.length === 0
						? renderEmpty('nenhuma mensagem', 'quando outros agentes escreverem, aparece aqui')
						: `<div class="row-list">${lv.mailboxes.map(m => `
							<div class="row">
								<span class="row-id">${esc(truncate(m.from_instance, 24))}</span>
								<span></span>
								<span class="row-meta">${fmtNumber(m.unread)} não-lidas</span>
							</div>`).join('')}</div>`
					}
				</div>
			</section>

			<section>
				<div class="section-h">
					<h2 class="section-title">Halls ativos</h2>
					<span class="section-meta">${hallCount} hall${hallCount !== 1 ? 's' : ''}</span>
				</div>
				${lv.halls.length === 0
					? renderEmpty('nenhum hall criado', 'use a tool MCP conscio_hall_create para criar grupos')
					: `<div class="grid grid-3">${lv.halls.map(h => `
						<div class="entity">
							<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">
								<span class="entity-id">${esc(h.nome)}</span>
								<span class="badge is-live">${h.member_count} membro${h.member_count !== 1 ? 's' : ''}</span>
							</div>
							<div class="entity-meta">
								<span><b>id</b> ${esc(truncate(h.hall_id, 32))}</span>
								<span><b>dono</b> ${esc(truncate(h.dono, 18))}</span>
							</div>
						</div>`).join('')}</div>`
				}
			</section>
		`;
	}

	function renderAgents() {
		paintPageHead('Agents', 'live · heartbeat 3 estados');
		const lv = state.live;
		const list = state.showOffline ? lv.allAgents : lv.agents;
		const filtered = state.search.agents
			? list.filter(a =>
				(a.instance_id || '').toLowerCase().includes(state.search.agents.toLowerCase()) ||
				(a.modelo || a.model || '').toLowerCase().includes(state.search.agents.toLowerCase()) ||
				(a.familia || '').toLowerCase().includes(state.search.agents.toLowerCase()))
			: list;

		return `
			<div class="toolbar">
				<input class="search" placeholder="buscar por id, modelo, família..." value="${esc(state.search.agents)}" oninput="App._searchAgents(this.value)">
				<button class="toggle ${state.showOffline ? 'is-on' : ''}" onclick="App._toggleOffline()">
					${state.showOffline ? '●' : '○'} offline
				</button>
			</div>
			${filtered.length === 0
				? renderEmpty(state.showOffline ? 'nenhum agent registrado' : 'nenhum agent vivo', 'watcher precisa bater heartbeat para aparecer aqui')
				: `<div class="grid grid-2">${filtered.map(a => {
					const isOffline = !!a.offline;
					return `
						<div class="entity ${isOffline ? 'is-offline' : ''}">
							<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">
								<span class="entity-id">${esc(truncate(a.instance_id, 36))}</span>
								${badge(isOffline ? 'offline' : 'alive', isOffline ? 'offline' : 'alive')}
							</div>
							<div class="entity-meta">
								<span><b>modelo</b> ${esc(a.modelo || a.model || '—')}</span>
								<span><b>família</b> ${esc(a.familia || '—')}</span>
								<span><b>runtime</b> ${esc(a.runtime || '—')}</span>
								<span><b>papel</b> ${esc(a.papel || '—')}</span>
							</div>
							<div class="entity-meta">
								<span><b>cap</b> ${esc((a.capabilities || []).join(', ') || '—')}</span>
								<span><b>hb</b> ${relTime(a.last_heartbeat)}</span>
							</div>
						</div>`;
				}).join('')}</div>`
			}
		`;
	}

	function renderHalls() {
		paintPageHead('Halls', 'live · Agent groups');
		const lv = state.live;
		if (lv.halls.length === 0) {
			return renderEmpty('nenhum hall criado', 'use a tool MCP conscio_hall_create para criar grupos');
		}
		return `<div class="grid grid-2">${lv.halls.map(h => `
			<div class="entity">
				<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">
					<span class="entity-id">${esc(h.nome)}</span>
					<span class="badge is-live">${h.member_count} membro${h.member_count !== 1 ? 's' : ''}</span>
				</div>
				<div class="entity-meta">
					<span><b>id</b> ${esc(truncate(h.hall_id, 40))}</span>
				</div>
				<div class="entity-meta">
					<span><b>dono</b> ${esc(truncate(h.dono, 24))}</span>
					<span><b>criado</b> ${ago(h.criado_em)}</span>
				</div>
			</div>`).join('')}</div>`;
	}

	function renderMailboxes() {
		paintPageHead('Mailboxes', 'live · peer unread');
		const lv = state.live;
		if (lv.mailboxes.length === 0) {
			return renderEmpty('nenhuma mensagem não-lida', 'caixa de entrada vazia');
		}
		return `<div class="grid grid-2">${lv.mailboxes.map(m => `
			<div class="entity">
				<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">
					<span class="entity-id">${esc(truncate(m.from_instance, 36))}</span>
					<span class="badge ${m.unread > 0 ? 'is-warn' : 'is-offline'}">${fmtNumber(m.unread)}</span>
				</div>
				<div class="entity-meta">
					<span><b>direção</b> incoming</span>
					<span><b>tipo</b> chat/delegation/status</span>
				</div>
			</div>`).join('')}</div>`;
	}

	function renderDaemon() {
		paintPageHead('Daemon', 'process state');
		const d = state.live.daemon || {};
		const items = [
			['running', d.running ? 'yes' : 'no'],
			['awake', d.awake ? 'yes' : 'no'],
			['cycles', fmtNumber(d.cycles)],
			['uptime', d.uptime || '—'],
		];
		return `<div class="grid grid-4">
			${items.map(([k, v]) => kpi(titleCase(k), v, '', d.running && k === 'running' ? 'is-live' : '')).join('')}
		</div>
		<div class="card" style="margin-top:18px">
			<div class="card-head"><h3 class="card-title">Detalhes</h3></div>
			<pre class="code">${esc(JSON.stringify(d, null, 2))}</pre>
		</div>`;
	}

	function renderIdentity() {
		paintPageHead('Identity', 'self · persistent');
		const id = state.live.identity || {};
		return `<div class="card">
			<pre class="code">${esc(JSON.stringify(id, null, 2))}</pre>
		</div>`;
	}

	function renderRelayInbox() {
		paintPageHead('Relay inbox', 'por peer · colapsável');
		return `<div id="relay-inbox"><div class="empty"><div class="empty-title">carregando…</div></div></div>`;
	}

	function relayPayloadText(msg) {
		const pl = msg.payload || {};
		// tenta extrair um texto legível direto, senao mostra o JSON
		if (typeof pl.text === 'string' || typeof pl.mensagem === 'string') {
			return pl.text || pl.mensagem || '';
		}
		if (pl.content && typeof pl.content === 'string') return pl.content;
		return JSON.stringify(pl, null, 2);
	}

	async function paintRelayInbox() {
		const host = $('#relay-inbox');
		if (!host) return;
		// preserva o estado "aberto" dos grupos/msgs entre re-renders (poll)
		const open = Array.from(host.querySelectorAll('details[open]'))
			.map(d => d.dataset.id || d.getAttribute('data-peer')).
			filter(Boolean);
		try {
			const groups = await getJson('/api/relay/inbox');
			if (!groups || groups.length === 0) {
				host.innerHTML = renderEmpty('nenhuma mensagem de peer', 'o relay inbox aparece aqui quando peers enviam');
				return;
			}
			host.innerHTML = groups.map(g => {
				const unread = g.messages.filter(m => m.read_ts === null).length;
				return `
					<details class="relay-group" data-peer="${esc(g.from_instance)}" ${open.includes(g.from_instance) ? 'open' : ''}>
						<summary class="relay-summary">
							<span class="relay-peer-id">${esc(truncate(g.from_instance, 24))}</span>
							<span class="badge ${unread ? 'is-warn' : 'is-offline'}">${g.messages.length} msg</span>
							${unread ? `<span class="badge is-live">${unread} nova${unread !== 1 ? 's' : ''}</span>` : ''}
						</summary>
						<div class="relay-msgs">
							${g.messages.map(m => `
								<details class="relay-msg ${m.read_ts === null ? 'is-unread' : ''}" data-id="${m.id}" ${open.includes(String(m.id)) ? 'open' : ''}>
									<summary class="relay-msg-summary">
										<span class="mono">#${m.id}</span>
										<span class="badge">${esc(m.type)}</span>
										<span class="relay-msg-preview">${esc(truncate(relayPayloadText(m), 60))}</span>
										<span class="row-meta">${ago(m.ts)}</span>
									</summary>
									<div class="relay-msg-body">
										<div class="entity-meta" style="margin-bottom:8px">
											<span><b>de</b> ${esc(truncate(m.from_instance, 24))}</span>
											<span><b>para</b> ${esc(truncate(m.to_instance, 24))}</span>
											<span><b>id</b> ${m.id}</span>
											<span><b>${m.read_ts === null ? 'não lida' : 'lida'}</b></span>
										</div>
										<pre class="code">${esc(JSON.stringify(m.payload, null, 2))}</pre>
									</div>
								</details>`).join('')}
						</div>
					</details>`;
			}).join('');
		} catch (e) {
			host.innerHTML = renderEmpty('erro ao carregar inbox', e.message);
		}
	}

	function renderSociety() {
		paintPageHead('Society · Members', 'peer instances');
		return `<div class="card"><pre class="code" id="json-out">carregando…</pre></div>`;
	}
	function renderSocietySkills() {
		paintPageHead('Society · Skills', 'shared procedures');
		return `<div class="card"><pre class="code" id="json-out">carregando…</pre></div>`;
	}
	function renderSocietyRecords() {
		paintPageHead('Society · Records', 'entry counts');
		return `<div class="card"><pre class="code" id="json-out">carregando…</pre></div>`;
	}

	function renderEvents() { paintPageHead('Events', 'log stream'); return simpleJson('/api/events', 'events'); }
	function renderActions() { paintPageHead('Actions', 'audit trail'); return simpleJson('/api/actions', 'actions'); }
	function renderGoals() { paintPageHead('Goals', 'intentions'); return simpleJson('/api/goals', 'goals'); }
	function renderSkills() { paintPageHead('Skills', 'library'); return simpleJson('/api/skills', 'skills'); }

	function renderKgEntities() { paintPageHead('KG · Entities', 'nodes'); return simpleJson('/api/knowledge/entities', 'entities'); }
	function renderKgRelationships() { paintPageHead('KG · Relationships', 'edges'); return simpleJson('/api/knowledge/relationships', 'relationships'); }
	function renderKgTimeline() { paintPageHead('KG · Timeline', 'history'); return simpleJson('/api/knowledge/timeline', 'timeline'); }
	function renderStructural() { paintPageHead('Structural · Drift', 'repo drift'); return simpleJson('/api/structural/drift', 'drift'); }

	function renderGraph() {
		paintPageHead('Graph', 'verdade via /graph');
		return `<div class="card" style="padding:0;overflow:hidden">
			<div id="graph-host" style="height:560px"></div>
			<div style="padding:14px 18px;border-top:1px solid var(--line);font-size:12px;color:var(--text-faint);font-family:var(--font-mono)">
				serve graphify-out/graph.html da raiz do workspace. /graph retorna 404 enquanto não há projeto com graph gerado.
			</div>
		</div>`;
	}

	function simpleJson(endpoint, key) {
		const id = 'json-' + key + '-' + Date.now();
		setTimeout(async () => {
			try {
				const data = await getJson(endpoint);
				const el = document.getElementById('json-out');
				if (el) el.textContent = JSON.stringify(data, null, 2);
			} catch (e) {
				const el = document.getElementById('json-out');
				if (el) el.textContent = 'erro: ' + e.message;
			}
		}, 0);
		return `<div class="card"><pre class="code" id="json-out">carregando…</pre></div>`;
	}

	// ── dispatcher ───────────────────────────────────────────────────────

	const RENDERERS = {
		overview: renderOverview,
		agents: renderAgents,
		daemon: renderDaemon,
		halls: renderHalls,
		mailboxes: renderMailboxes,
		relay: renderRelayInbox,
		identity: renderIdentity,
		society: renderSociety,
		'society-skills': renderSocietySkills,
		'society-records': renderSocietyRecords,
		events: renderEvents,
		actions: renderActions,
		goals: renderGoals,
		skills: renderSkills,
		'kg-entities': renderKgEntities,
		'kg-relationships': renderKgRelationships,
		'kg-timeline': renderKgTimeline,
		structural: renderStructural,
		graphview: renderGraph,
	};

	function switchTab(tab) {
		state.tab = tab;
		$$('.nav-item').forEach(b => {
			b.classList.toggle('is-active', b.dataset.tab === tab);
		});
		stopAllPolling();
		const render = RENDERERS[tab] || simpleJson.bind(null, '/api/' + tab, tab);
		$('#content').innerHTML = render();
		startPolling(tab);
		// graphview init
		if (tab === 'graphview') {
			initGraph();
		}
	}

	// ── polling (visibility-aware) ──────────────────────────────────────

	const POLL_MAP = {
		overview: pollOverview,
		agents: pollAgents,
		daemon: pollDaemon,
		halls: pollHalls,
		mailboxes: pollMailboxes,
		identity: pollIdentity,
		relay: pollRelay,
	};

	async function pollRelay() {
		try {
			if (state.tab === 'relay') await paintRelayInbox();
		} catch (e) {}
	}

	function startPolling(tab) {
		const fn = POLL_MAP[tab];
		if (!fn) return;
		fn();   // first paint immediately
		state.polling.set(tab, setInterval(fn, 3000));
	}

	function stopAllPolling() {
		state.polling.forEach((id) => clearInterval(id));
		state.polling.clear();
	}

	async function pollOverview() {
		try {
			const [health, agents, halls, mb, daemon, identity] = await Promise.all([
				getJson('/api/health'),
				getJson('/api/agents'),
				getJson('/api/halls'),
				getJson('/api/mailboxes'),
				getJson('/api/daemon'),
				getJson('/api/identity'),
			]);
			state.live.version = health.version;
			state.live.storage = health.liaison;
			state.live.agents = agents.filter(a => !a.offline);
			state.live.allAgents = agents;
			state.live.halls = halls;
			state.live.mailboxes = mb;
			state.live.daemon = daemon;
			state.live.identity = identity;
			paintTopbar(); paintCounts();
			if (state.tab === 'overview') {
				$('#content').innerHTML = renderOverview();
			}
		} catch (e) { /* degrade silently */ }
	}

	async function pollAgents() {
		try {
			const include = state.showOffline ? '?stale=1' : '';
			const data = await getJson('/api/agents' + include);
			state.live.allAgents = data;
			state.live.agents = data.filter(a => !a.offline);
			paintCounts();
			if (state.tab === 'agents') {
				$('#content').innerHTML = renderAgents();
			}
		} catch (e) {}
	}

	async function pollDaemon() {
		try {
			state.live.daemon = await getJson('/api/daemon');
			if (state.tab === 'daemon') $('#content').innerHTML = renderDaemon();
		} catch (e) {}
	}

	async function pollHalls() {
		try {
			state.live.halls = await getJson('/api/halls');
			paintCounts();
			if (state.tab === 'halls') $('#content').innerHTML = renderHalls();
		} catch (e) {}
	}

	async function pollMailboxes() {
		try {
			state.live.mailboxes = await getJson('/api/mailboxes');
			paintCounts();
			if (state.tab === 'mailboxes') $('#content').innerHTML = renderMailboxes();
		} catch (e) {}
	}

	async function pollIdentity() {
		try {
			state.live.identity = await getJson('/api/identity');
			if (state.tab === 'identity') $('#content').innerHTML = renderIdentity();
		} catch (e) {}
	}

	// ── graph via graphview.js (contrato TAB_RENDERERS) ────────────────

	function initGraph() {
		const host = $('#graph-host');
		if (!host) return;
		if (window.TAB_RENDERERS && typeof window.TAB_RENDERERS.graphview === 'function') {
			window.TAB_RENDERERS.graphview(host);
		} else {
			host.innerHTML = '<div class="empty"><div class="empty-title">graphview não carregado</div></div>';
		}
	}

	// ── interactivity exports ───────────────────────────────────────────

	window.App = {
		switchTab,
		_searchAgents: (v) => { state.search.agents = v; $('#content').innerHTML = renderAgents(); },
		_toggleOffline: () => { state.showOffline = !state.showOffline; pollAgents(); },
	};

	// ── nav wiring ───────────────────────────────────────────────────────

	$$('.nav-item').forEach(b => {
		b.addEventListener('click', () => switchTab(b.dataset.tab));
	});

	// visibility-aware: para polling quando a aba some (evita gastos)
	document.addEventListener('visibilitychange', () => {
		state.visible = !document.hidden;
		if (!state.visible) {
			stopAllPolling();
		} else if (state.tab) {
			startPolling(state.tab);
		}
	});

	// ── boot ─────────────────────────────────────────────────────────────

	(async function init() {
		// primeira carga (skeleton), depois pinta
		$('#content').innerHTML = renderSkeleton();
		await pollOverview();
		switchTab('overview');
	})();
})();
