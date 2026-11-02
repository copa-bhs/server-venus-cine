const API = '';
window._pedidoAtual = null;

let WS = null;
let WS_RECONNECT_TIMER = null;

function conectarWS() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws`;
    try { WS = new WebSocket(url); } catch (e) { agendarReconectarWS(); return; }

    WS.onopen = () => { atualizarStatusWS(true); };
    WS.onclose = () => { atualizarStatusWS(false); agendarReconectarWS(); };
    WS.onerror = () => atualizarStatusWS(false);
    WS.onmessage = (ev) => {
        if (ev.data === 'pong') return;
        try {
            const msg = JSON.parse(ev.data);
            tratarEvento(msg.evento, msg.dados);
        } catch {}
    };
    setInterval(() => { if (WS && WS.readyState === WebSocket.OPEN) WS.send('ping'); }, 25000);
}

function agendarReconectarWS() {
    if (WS_RECONNECT_TIMER) clearTimeout(WS_RECONNECT_TIMER);
    WS_RECONNECT_TIMER = setTimeout(conectarWS, 3000);
}

function atualizarStatusWS(online) {
    const dot = document.getElementById('ws-dot');
    const lbl = document.getElementById('ws-label');
    const bd = document.getElementById('ws-badge-dot');
    const bt = document.getElementById('ws-badge-text');
    if (online) {
        dot.className = 'w-2 h-2 rounded-full bg-green-400 pulse';
        lbl.textContent = 'Tempo real ativo';
        bd.className = 'w-2 h-2 rounded-full bg-green-400 pulse';
        bt.textContent = 'AO VIVO';
        bt.className = 'text-green-400 font-bold';
    } else {
        dot.className = 'w-2 h-2 rounded-full bg-red-500';
        lbl.textContent = 'WS offline';
        bd.className = 'w-2 h-2 rounded-full bg-red-500';
        bt.textContent = 'OFFLINE';
        bt.className = 'text-red-400';
    }
}

function tratarEvento(evento, dados) {
    if (evento === 'catalogo_sincronizado') {
        Toast.show(`📡 Catálogo Atualizado: ${dados.total_filmes} filmes, ${dados.total_series} séries`, 'info', 4000);
        if (['dashboard','filmes','series','canais','falhas'].includes(Router.current)) Router.render();
    } else if (evento === 'item_editado') {
        Toast.show(`✏️ ${dados.tipo} "${dados.titulo}" atualizado`, 'success', 3000);
        if (['filmes','series','canais','slider'].includes(Router.current)) Router.render();
    }
}

const Toast = {
    show(msg, type = 'info', dur = 3000) {
        const colors = { info: 'border-blue-500', success: 'border-green-500', error: 'border-red-500', warn: 'border-yellow-500' };
        const icons = { info: 'fa-circle-info', success: 'fa-circle-check', error: 'fa-circle-exclamation', warn: 'fa-triangle-exclamation' };
        const el = document.createElement('div');
        el.className = `toast-slide bg-[#141414] border-l-4 ${colors[type] || colors.info} rounded-lg px-4 py-3 text-sm shadow-2xl min-w-[260px] max-w-[380px] flex items-start gap-3`;
        el.innerHTML = `<i class="fa-solid ${icons[type]} mt-0.5"></i><div class="flex-1">${msg}</div>`;
        document.getElementById('toast-root').appendChild(el);
        setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; setTimeout(() => el.remove(), 300); }, dur);
    }
};

const Api = {
    async get(path) {
        const r = await fetch(API + path);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return await r.json();
    },
    async post(path, body = null) {
        const opts = { method: 'POST' };
        if (body) { opts.headers = { 'Content-Type': 'application/json' }; opts.body = JSON.stringify(body); }
        const r = await fetch(API + path, opts);
        if (!r.ok) { const erro = await r.json().catch(() => ({ detail: `HTTP ${r.status}` })); throw new Error(erro.detail || `HTTP ${r.status}`); }
        return await r.json();
    },
    async del(path) {
        const r = await fetch(API + path, { method: 'DELETE' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return await r.json();
    },
    status() { return this.get('/status'); },
    proxyStats() { return this.get('/proxy/stats'); },
    adminFilmes: (q='', page=1, size=30) => Api.get(`/admin/filmes?q=${encodeURIComponent(q)}&page=${page}&size=${size}`),
    adminSeries: (q='', page=1, size=30) => Api.get(`/admin/series?q=${encodeURIComponent(q)}&page=${page}&size=${size}`),
    adminCanais: (q='', page=1, size=30) => Api.get(`/admin/canais?q=${encodeURIComponent(q)}&page=${page}&size=${size}`),
    adminItem: (tipo, id) => Api.get(`/admin/item/${tipo}/${id}`),
    adminFalhas: (page=1, size=50) => Api.get(`/admin/falhas?page=${page}&size=${size}`),
    deletarItem: (tipo, id) => Api.del(`/admin/item/${tipo}/${id}`),
    importarCatalogo: () => Api.post('/admin/importar-catalogo'),
    notifHistorico() { return this.get('/admin/notificacoes/historico'); },
    notifEnviar(payload) { return this.post('/admin/notificacoes/enviar', payload); },
    pedidos() { return this.get('/admin/notificacoes/pedidos'); },
    concluirPedido(id, payload) { return this.post(`/admin/notificacoes/pedidos/${id}/concluir`, payload); },
    wipeAll() { return this.post('/admin/wipe-all'); },
    usuarios() { return this.get('/admin/notificacoes/usuarios'); },
    bloquearUsuario(uid, status) { return this.post(`/admin/notificacoes/usuarios/${uid}/bloquear?bloqueado=${status}`); },
    deletarUsuario(uid) { return this.del(`/admin/notificacoes/usuarios/${uid}`); },
    alterarPlano(uid, plano) { return this.post(`/admin/notificacoes/usuarios/${uid}/alterar-plano`, { plano }); }
};

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => Number(n || 0).toLocaleString('pt-BR');

function toggleSidebar() {
    const sb = document.getElementById('sidebar');
    const ov = document.getElementById('sidebar-overlay');
    const open = !sb.classList.contains('-translate-x-full');
    if (open) { sb.classList.add('-translate-x-full'); ov.classList.add('hidden'); }
    else { sb.classList.remove('-translate-x-full'); ov.classList.remove('hidden'); }
}

const Router = {
    current: 'dashboard',
    go(route) { window.location.hash = '#' + route; },
    handle() {
        const h = window.location.hash.replace('#', '') || 'dashboard';
        this.current = h;
        document.querySelectorAll('.sidebar-link').forEach(a => { a.classList.toggle('active', a.dataset.route === h); });
        const titles = {
            dashboard: 'Dashboard', pedidos: 'Pedidos de Conteúdo', usuarios: 'Gestão de Usuários',
            iptv: 'IPTV Direto (Adicionar Manual)', falhas: 'Falhas de TMDB',
            notificacoes: 'Notificações Push', slider: 'Slider (Hero)', filmes: 'Filmes', series: 'Séries', canais: 'Canais',
            proxy: 'Proxy de Streaming', sistema: 'Sistema'
        };
        document.getElementById('page-title').textContent = titles[h] || h;
        this.render();
        if (window.innerWidth < 1024) {
            document.getElementById('sidebar').classList.add('-translate-x-full');
            document.getElementById('sidebar-overlay').classList.add('hidden');
        }
    },
    async render() {
        const c = document.getElementById('content');
        c.innerHTML = `<div class="flex items-center justify-center h-64 text-gray-500"><i class="fa-solid fa-circle-notch fa-spin text-2xl"></i></div>`;
        try {
            if (this.current === 'dashboard') await Views.dashboard(c);
            else if (this.current === 'pedidos') await Views.pedidos(c);
            else if (this.current === 'usuarios') await Views.usuarios(c);
            else if (this.current === 'iptv') await Views.buscaIptv(c);
            else if (this.current === 'falhas') await Views.falhas(c);
            else if (this.current === 'notificacoes') await Views.notificacoes(c);
            else if (this.current === 'slider') await Views.slider(c);
            else if (this.current === 'filmes') await Views.list(c, 'filme');
            else if (this.current === 'series') await Views.list(c, 'serie');
            else if (this.current === 'canais') await Views.list(c, 'canal');
            else if (this.current === 'proxy') await Views.proxy(c);
            else if (this.current === 'sistema') await Views.sistema(c);
        } catch (e) { c.innerHTML = `<div class="text-center py-16 text-red-400">Erro: ${esc(e.message)}</div>`; }
    }
};

const Views = {
    async dashboard(c) {
        const s = await Api.status().catch(() => ({}));
        const stats = s.stats || {};
        const proxy = s.proxy || {};
        c.innerHTML = `
            <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
                ${this.statCard('Filmes', fmt(stats.total_filmes), 'fa-film', 'text-blue-400')}
                ${this.statCard('Séries', fmt(stats.total_series), 'fa-tv', 'text-purple-400')}
                ${this.statCard('Falhas TMDB', fmt(stats.total_falhas), 'fa-triangle-exclamation', 'text-yellow-400')}
                ${this.statCard('Slugs proxy', fmt(proxy.slugs_carregados), 'fa-link', 'text-green-400')}
            </div>

            <div class="stat-card rounded-xl p-5 mb-6 border-green-600/30 bg-green-600/5">
                <div class="flex flex-col md:flex-row items-center gap-4">
                    <div class="w-14 h-14 rounded-full bg-green-600/20 flex items-center justify-center flex-shrink-0">
                        <i class="fa-solid fa-cloud-arrow-down text-green-500 text-xl"></i>
                    </div>
                    <div class="flex-1 text-center md:text-left">
                        <h3 class="text-sm font-bold text-white mb-1">Sincronizar Catálogo</h3>
                        <p class="text-xs text-gray-400">Lê o arquivo <b>catalogo.json</b> gerado pelo script IPTV e atualiza o banco de dados SQL.</p>
                    </div>
                    <div class="flex flex-col sm:flex-row gap-2 w-full md:w-auto">
                        <button id="btn-sync-cat" onclick="Actions.sincronizarCatalogo()" class="h-10 px-4 rounded-lg bg-green-600 hover:bg-green-700 text-white text-xs font-bold flex items-center justify-center gap-2">
                            <i class="fa-solid fa-rotate"></i> Importar Catálogo
                        </button>
                    </div>
                </div>
            </div>
        `;
    },

    async falhas(c) {
        const data = await Api.adminFalhas().catch(() => ({ items: [], total: 0 }));
        const itens = data.items || [];
        
        const badge = document.getElementById('badge-falhas');
        if (data.total > 0) { badge.textContent = data.total; badge.classList.remove('hidden'); }
        else badge.classList.add('hidden');

        c.innerHTML = `
            <div class="flex items-center justify-between mb-6">
                <div>
                    <h2 class="text-xl font-black text-yellow-500">⚠️ Falhas de TMDB</h2>
                    <p class="text-xs text-gray-500 mt-1">Filmes e séries que não foram encontrados automaticamente no TMDB.</p>
                </div>
                <button onclick="Router.render()" class="h-10 px-4 rounded-lg bg-[#1a1a1a] border border-[#333] text-xs font-bold flex items-center gap-2"><i class="fa-solid fa-rotate"></i> Atualizar</button>
            </div>

            ${itens.length === 0 ? `
                <div class="stat-card rounded-xl p-12 text-center border-green-600/30">
                    <i class="fa-solid fa-check-circle text-5xl text-green-500 mb-4"></i>
                    <h3 class="text-lg font-bold mb-2">Nenhuma falha encontrada!</h3>
                    <p class="text-xs text-gray-500">Todos os itens do catálogo foram enriquecidos com sucesso.</p>
                </div>
            ` : `
                <div class="stat-card rounded-xl overflow-hidden">
                    <div class="overflow-x-auto">
                        <table class="w-full text-sm">
                            <thead class="bg-[#0f0f0f] border-b border-[#262626] text-xs text-gray-400 uppercase">
                                <tr>
                                    <th class="text-left px-4 py-3">ID IPTV