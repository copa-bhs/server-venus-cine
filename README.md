# 🎬 IPTV Organizer Pro

**Versão:** 2.6.0  
**Linguagem:** Python 3.10+  
**Framework:** FastAPI + Uvicorn  
**Autor:** Vênus Cine 🍿  
**Licença:** MIT

---

## 📖 O que é este projeto?

O **IPTV Organizer Pro** é um servidor intermediário que organiza, categoriza e serve conteúdo IPTV (filmes, séries e canais ao vivo) de forma rápida, paginada e resiliente, a partir de uma lista M3U de qualquer provedor Xtream Codes.

Ele atua como uma **API REST** entre o provedor IPTV e seu aplicativo cliente (web, mobile, TV, etc.), entregando dados já limpos, classificados e **enriquecidos com metadados do TMDB** (capa, banner 4K, logo, trailer, sinopse, score, classificação etária).

Inclui **proxy reverso de streaming** para esconder as credenciais do provedor e proteger o servidor contra abusos.

---

## ✨ O que ele FAZ

### 🔽 Ingestão de conteúdo
- ✅ Baixa a lista M3U do provedor em **download paralelo** (até 8 conexões via HTTP Range)
- ✅ **Classifica automaticamente** os itens em filmes, séries e canais ao vivo
- ✅ **Limpa os títulos** removendo hashtags, tags técnicas, ano entre parênteses, prefixos como "FILME:" e sufixos de episódio (S01E02)
- ✅ **Limpa as categorias** removendo prefixos como "FILMES •", "SÉRIES |", etc.
- ✅ **Agrupa episódios** de séries por título base, organizados por temporada
- ✅ **Extrai o ID numérico** do provedor direto da URL Xtream Codes

### 🎨 Enriquecimento com TMDB
- ✅ **Enricher em background** que popula automaticamente:
  - Capa oficial em alta resolução (`w500` / `w780`)
  - Banner 4K (`w1280` / `original`)
  - Logo transparente PNG (`w500` / `original`)
  - Score, sinopse PT-BR, gêneros, elenco, diretor
  - Trailer oficial do YouTube
  - Classificação etária brasileira (L, 10, 12, 14, 16, 18)
- ✅ **Cache de 30 dias** dos metadados TMDB (respeita rate limit)
- ✅ **Fallback inteligente**: se o provedor já trouxe, mantém; se não, TMDB preenche
- ✅ **Trailer Checker**: re-verifica trailers faltantes a cada 6 horas

### 🚀 Performance
- ✅ **Cache em disco com TTL de 1 hora** para a lista principal
- ✅ **Cache individual de metadados por ID** com TTL de 24 horas
- ✅ **Índice em RAM** para resposta instantânea
- ✅ **Paginação** em todas as rotas de conteúdo
- ✅ **Auto-refresh em background** que atualiza o índice a cada hora sem downtime
- ✅ **Fallback automático** para o cache antigo se o provedor cair

### 📊 Detecção de novidades
- ✅ **Comparação de snapshots** por (título + ano) para reduzir falsos positivos
- ✅ **Separação automática** em "Lançamentos" (recentes) e "Catálogo Novo" (antigos)
- ✅ **Log formatado** no console com os títulos detectados
- ✅ Rota `/novidades` com paginação

### 🏠 Dashboard `/home` estilo Netflix
- ✅ **Hero slider** com 5 slides rotativos por semana (seed determinístico ISO)
- ✅ **12 coleções** pré-montadas:
  1. 🏆 Top 10 Filmes
  2. 🏆 Top 10 Séries
  3. 🔥 Em Alta (rotativo)
  4. 🆕 Lançamentos
  5. 💥 Ação
  6. 😂 Comédia
  7. 🎭 Drama
  8. 👻 Terror
  9. 🚀 Ficção Científica
  10. 🎨 Animação
  11. ⭐ Bem Avaliados
  12. 📚 Catálogo Novo
- ✅ **Filtros inteligentes** que excluem documentários, shows, música do Top 10
- ✅ Seção de **trailers** com capas oficiais

### 🔒 Proxy reverso de streaming
- ✅ **Esconde as credenciais** do provedor (`/stream/{slug}`)
- ✅ **URLs amigáveis** com slug do título (`/stream/homem-aranha-2017`)
- ✅ **Range requests** suportados (seek em vídeo)
- ✅ **Streaming bidirecional** chunk-a-chunk (não carrega vídeo em RAM)
- ✅ **Limite global** de streams simultâneos (`PROXY_MAX_CONCURRENT`)
- ✅ **Limite por IP** (`PROXY_MAX_PER_IP`)
- ✅ **Timeout configurável** de conexão e leitura
- ✅ Monitor em tempo real em `/proxy/stats`

### 🔧 Compatibilidade
- ✅ **Roda no Termux (Android ARM64)** e em VPS Linux (Ubuntu/Debian)
- ✅ **Sem compilação Rust** (100% Python puro)
- ✅ **Compatível com Python 3.9+** (via `eval_type_backport`)
- ✅ **Deploy com systemd** para rodar 24/7
- ✅ **Nginx + HTTPS** com Certbot (Let's Encrypt)

---

## ❌ O que ele NÃO faz

- ❌ **Não transcodifica** vídeo (não converte formato/resolução)
- ❌ **Não armazena os vídeos** — só faz proxy em tempo real
- ❌ **Não possui interface visual** — é uma API REST pura (JSON)
- ❌ **Não controla autenticação de usuários finais** (o app cliente gerencia isso)
- ❌ **Não gerencia assinaturas** ou cobra usuários
- ❌ **Não substitui o Xtream Codes**
- ❌ **Não suporta EPG (guia de programação)** ainda — planejado
- ❌ **Não envia notificações push** nativamente
- ❌ **Não tem painel administrativo web** — tudo via API

---

## 🏗️ Arquitetura

```
┌────────────────────────────────────────┐
│  App do usuário                        │
│  (web / mobile / TV / VLC / TiviMate)  │
└─────────────┬──────────────────────────┘
              │  HTTPS/JSON
              ▼
┌────────────────────────────────────────┐
│  Nginx (proxy reverso + HTTPS)         │
└─────────────┬──────────────────────────┘
              │
              ▼
┌────────────────────────────────────────┐
│  IPTV Organizer Pro                    │
│  • FastAPI + Uvicorn                   │
│  • Índice em RAM                       │
│  • Cache em disco (1h lista / 24h meta)│
│  • Enricher TMDB em background         │
│  • Trailer Checker (6h)                │
│  • Auto-refresh (1h)                   │
│  • Proxy reverso de streaming          │
└─────────────┬──────────────────────────┘
              │  HTTP
              ▼
┌────────────────────┬───────────────────┐
│  Provedor IPTV     │  TMDB API         │
│  (Xtream Codes)    │  (metadados)      │
└────────────────────┴───────────────────┘
```

---

## 📂 Estrutura de arquivos

```
iptv-organizer/
├── main.py              # Servidor FastAPI (rotas + paginação + startup + auto-refresh)
├── config.py            # Constantes e leitura do .env
├── parser.py            # Parser M3U + limpeza + ID do provedor + índice
├── downloader.py        # Download paralelo estilo IDM (Range requests)
├── info.py              # Metadados de filme/série via ID (Xtream + TMDB)
├── tmdb.py              # Integração TMDB (busca, imagens, trailers, classificação)
├── enricher.py          # Enricher em background (popula capa/logo/score/gênero)
├── trailer_checker.py   # Re-verificação de trailers faltantes
├── proxy.py             # Proxy reverso de streaming + slug map
├── home.py              # Dashboard /home (12 coleções + hero + trailers)
├── requirements.txt     # Dependências Python
├── .env                 # Credenciais (NÃO versionar)
├── .env.example         # Modelo de variáveis de ambiente
├── .gitignore           # Arquivos ignorados pelo Git
└── cache/               # Cache local (gerado em runtime)
    ├── playlist.m3u     # Lista M3U bruta baixada
    ├── meta.json        # Timestamp do último refresh
    ├── index.json       # Índice pré-processado
    ├── novidades.json   # Últimas novidades detectadas
    ├── slug_map.json    # Mapa slug → item (para o proxy)
    ├── info/            # Metadados por ID (24h TTL)
    └── tmdb/            # Cache TMDB (30 dias TTL)
```

---

## 🚀 Como instalar

### 📱 No Termux (Android)

```bash
# 1. Instalar Python e git
pkg install python git -y

# 2. Clonar o projeto
git clone https://github.com/SEU_USUARIO/SEU_REPO.git
cd SEU_REPO

# 3. Instalar dependências
pip install -r requirements.txt
pip install eval_type_backport   # se usar Python < 3.10

# 4. Criar o .env
nano .env

# 5. Rodar
python main.py
```

### 🖥️ Em VPS Linux (Ubuntu/Debian)

```bash
# 1. Atualizar sistema
apt update && apt upgrade -y

# 2. Instalar Python 3.10+
apt install -y software-properties-common git curl ufw nginx
add-apt-repository ppa:deadsnakes/ppa -y
apt update
apt install -y python3.10 python3.10-venv python3.10-dev

# 3. Clonar o projeto
cd /opt
git clone https://github.com/SEU_USUARIO/SEU_REPO.git iptv
cd iptv

# 4. Criar ambiente virtual
python3.10 -m venv venv
source venv/bin/activate

# 5. Instalar dependências
pip install --upgrade pip
pip install -r requirements.txt

# 6. Criar o .env
nano .env

# 7. Rodar
python main.py
```

### ⚙️ Variáveis de ambiente (.env)

```env
# ==========================================
# IPTV (obrigatório)
# ==========================================
IPTV_USERNAME=SeuUsuario
IPTV_PASSWORD=SuaSenha
IPTV_HOST=http://provedor.com:80
IPTV_URL=http://provedor.com:80/get.php?username=SeuUsuario&password=SuaSenha&type=m3u_plus&output=ts
USER_AGENT=VLC/3.0.20 LibVLC/3.0.20

# ==========================================
# Servidor
# ==========================================
CACHE_TTL=3600
PORT=8000

# ==========================================
# TMDB (obrigatório para enriquecimento rico)
# Obtenha grátis em: https://www.themoviedb.org/settings/api
# ==========================================
TMDB_API_KEY=sua_api_key_aqui
TMDB_LANGUAGE=pt-BR
TMDB_RECENT_DAYS=90
TMDB_MAX_PER_CYCLE=30

# ==========================================
# Enricher (background)
# ==========================================
ENRICH_BATCH_SIZE=20
ENRICH_INTERVAL=60
ENRICH_STARTUP_DELAY=30

# ==========================================
# Trailer checker
# ==========================================
TRAILER_CHECK_INTERVAL=21600
TRAILER_CHECK_BATCH=25
TRAILER_CHECK_STARTUP_DELAY=120

# ==========================================
# Proxy reverso de streaming
# ==========================================
PROXY_ENABLED=true
PROXY_MAX_CONCURRENT=100
PROXY_MAX_PER_IP=3
PROXY_CHUNK_SIZE=65536
PROXY_CONNECT_TIMEOUT=10
PROXY_READ_TIMEOUT=300

# URL pública para montar URLs completas nos JSONs
PUBLIC_BASE_URL=https://seu-dominio.com
```

---

## 📡 Rotas da API

Todas as rotas retornam **JSON**.

### 🏠 Básicas

| Método | Rota | Descrição |
|---|---|---|
| GET | `/` | Lista todos os endpoints disponíveis |
| GET | `/status` | Estado do cache + stats + progresso do enricher + proxy |
| POST | `/refresh` | Força atualização do cache e do índice |

### 🎬 Dashboard

| Método | Rota | Descrição |
|---|---|---|
| GET | `/home` | Dashboard completo (hero + 12 coleções + trailers) |

### 🎬 Conteúdo (com paginação)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/filmes?page=1&size=30&categoria=` | Lista filmes paginados |
| GET | `/series?page=1&size=30&categoria=` | Lista séries paginadas |
| GET | `/canais?page=1&size=50&categoria=` | Lista canais paginados |
| GET | `/buscar?q=texto&tipo=filme\|serie\|canal` | Busca paginada |
| GET | `/categorias` | Lista todas as categorias |

### 🔍 Metadados (Xtream + TMDB)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/info/filme/{id}` | Metadados ricos do filme |
| GET | `/info/serie/{id}` | Metadados da série + episódios por temporada |
| GET | `/info/filme/{id}?force=true` | Ignora cache e refaz a consulta |

### 🆕 Novidades

| Método | Rota | Descrição |
|---|---|---|
| GET | `/novidades` | Últimas novidades (Lançamentos + Catálogo) |
| GET | `/novidades?apenas_lancamentos=true` | Só lançamentos paginados |
| POST | `/novidades/verificar` | Força detecção agora |

### 🎥 Proxy de streaming

| Método | Rota | Descrição |
|---|---|---|
| GET | `/stream/{slug}` | Proxy via slug amigável |
| GET | `/stream/id/{id}` | Proxy via ID do painel Xtream |
| GET | `/proxy/stats` | Monitor em tempo real do proxy |

### 🛠️ Manutenção

| Método | Rota | Descrição |
|---|---|---|
| POST | `/trailers/verificar?limit=50` | Re-verifica trailers faltantes no TMDB |

**Parâmetros de paginação:**
- `page` — página (padrão: 1)
- `size` — itens por página (padrão: 30, máx: 200)
- `categoria` — filtra por categoria (opcional)

---

## 🧪 Exemplos de uso (curl)

```bash
# 1) Estado geral + stats + progresso enricher
curl http://localhost:8000/status

# 2) Dashboard completo
curl http://localhost:8000/home

# 3) Lista de filmes
curl "http://localhost:8000/filmes?page=1&size=30"

# 4) Categorias disponíveis
curl http://localhost:8000/categorias

# 5) Detalhes de um filme (com logo, banner 4K, trailer, classificação)
curl http://localhost:8000/info/filme/2127

# 6) Detalhes de uma série (com temporadas e episódios)
curl http://localhost:8000/info/serie/4521

# 7) Buscar
curl "http://localhost:8000/buscar?q=vingadores&tipo=filme"

# 8) Novidades detectadas
curl http://localhost:8000/novidades

# 9) Monitor do proxy
curl http://localhost:8000/proxy/stats

# 10) Forçar refresh da lista
curl -X POST http://localhost:8000/refresh

# 11) Forçar detecção de novidades
curl -X POST http://localhost:8000/novidades/verificar

# 12) Forçar re-verificação de trailers
curl -X POST "http://localhost:8000/trailers/verificar?limit=200"
```

---

## 📊 Formato das respostas

### Listagem paginada (`/filmes`, `/series`, `/canais`, `/buscar`)

```json
{
  "page": 1,
  "page_size": 30,
  "total_items": 4821,
  "total_pages": 161,
  "has_next": true,
  "has_prev": false,
  "items": [
    {
      "id": "2127",
      "titulo": "Apesar De",
      "ano": 2024,
      "capa": "https://image.tmdb.org/t/p/w500/xxx.jpg",
      "logo": "https://image.tmdb.org/t/p/w500/yyy.png",
      "banner": "https://image.tmdb.org/t/p/w1280/zzz.jpg",
      "banner_4k": "https://image.tmdb.org/t/p/original/zzz.jpg",
      "score": 7.4,
      "classificacao": "14",
      "generos": ["Drama", "Romance"],
      "categoria": "2024",
      "tipo": "filme",
      "url": "http://provedor.com/movie/User/Pass/2127.mp4",
      "slug": "apesar-de-2024",
      "url_stream": "https://seu-dominio.com/stream/apesar-de-2024"
    }
  ]
}
```

### Dashboard (`/home`)

```json
{
  "gerado_em": 1759800000.0,
  "gerado_em_str": "2026-10-08 20:30:00",
  "semana_iso": 202641,
  "tmdb_ativo": true,
  "hero": [
    {
      "id": "318",
      "tipo": "filme",
      "titulo": "A CAPTURA",
      "ano": 2026,
      "capa": "https://.../poster.jpg",
      "logo": "https://.../logo.png",
      "banner": "https://.../backdrop.jpg",
      "banner_4k": "https://.../backdrop-original.jpg",
      "score": 8.8,
      "classificacao": "16",
      "generos": ["Crime", "Ação", "Thriller"]
    }
  ],
  "colecoes": [
    { "id": "top10_filmes", "titulo": "🏆 Top 10 Filmes", "total": 10, "items": [...] },
    { "id": "top10_series", "titulo": "🏆 Top 10 Séries", "total": 10, "items": [...] },
    { "id": "em_alta", "titulo": "🔥 Em Alta", "total": 15, "items": [...] }
  ],
  "trailers": [
    { "id": "2127", "titulo": "...", "banner": "...", "trailer_url": "..." }
  ],
  "stats": { "total_filmes": 4821, "total_series": 612 }
}
```

### Metadados de filme (`/info/filme/{id}`)

```json
{
  "id": "2127",
  "tipo": "filme",
  "titulo": "Apesar De",
  "titulo_original": "Anyway",
  "tagline": "Um novo dia começa agora.",
  "capa": "https://.../poster.jpg",
  "capa_grande": "https://.../poster-large.jpg",
  "banner": "https://.../backdrop.jpg",
  "banner_4k": "https://.../backdrop-original.jpg",
  "logo": "https://.../logo.png",
  "logo_original": "https://.../logo-original.png",
  "ano": "2024-03-15",
  "duracao_min": 102,
  "score": 7.4,
  "sinopse": "Um homem que...",
  "generos": ["Drama", "Romance"],
  "elenco": "Fulano, Beltrano",
  "diretor": "João Silva",
  "trailer_url": "https://www.youtube.com/watch?v=xxx",
  "classificacao": "14",
  "qualidade": "HD",
  "url_stream": "http://provedor.com/movie/User/Pass/2127.mp4",
  "slug": "apesar-de-2024",
  "tmdb_id": 123456,
  "tmdb_recente": true,
  "campos_preenchidos_por_tmdb": ["ano", "sinopse", "score", "capa", "banner", "logo"]
}
```

### Metadados de série (`/info/serie/{id}`)

```json
{
  "id": "4521",
  "tipo": "serie",
  "titulo": "Breaking Bad",
  "ano": "2008",
  "capa": "https://.../poster.jpg",
  "banner": "https://.../backdrop.jpg",
  "banner_4k": "https://.../backdrop-original.jpg",
  "logo": "https://.../logo.png",
  "score": 9.5,
  "sinopse": "Um professor de química...",
  "generos": ["Drama", "Crime"],
  "classificacao": "16",
  "total_temporadas": 5,
  "total_episodios": 62,
  "temporadas": [
    { "numero": 1, "titulo": "Temporada 1", "capa": "..." }
  ],
  "episodios": [
    {
      "temporada": 1,
      "episodio": 1,
      "titulo": "Pilot",
      "duracao": "58min",
      "url_stream": "http://provedor.com/series/User/Pass/4521_1_1.mp4",
      "slug": "breaking-bad-t01-e01",
      "url_stream_proxy": "https://seu-dominio.com/stream/breaking-bad-t01-e01"
    }
  ]
}
```

### Novidades (`/novidades`)

```json
{
  "disponivel": true,
  "detectado_em_str": "2026-10-08 15:00:00",
  "tmdb_ativo": true,
  "totais": {
    "filmes_novos": 8,
    "series_novas": 3,
    "canais_novos": 0,
    "lancamentos": 4,
    "catalogo": 7
  },
  "lancamentos": [ /* filmes/séries recentes */ ],
  "catalogo": [ /* adicionados agora mas antigos */ ]
}
```

### Proxy stats (`/proxy/stats`)

```json
{
  "slugs_carregados": 12345,
  "ativo_total": 3,
  "limite_total": 100,
  "limite_por_ip": 3,
  "ips_conectados": 2,
  "por_ip": { "189.5.x.x": 2, "45.238.x.x": 1 }
}
```

---

## ⚡ Sistema de cache e background tasks

### Caches em disco

| Cache | Onde fica | TTL | O que guarda |
|---|---|---|---|
| **Lista M3U** | `cache/playlist.m3u` | 1h | Arquivo bruto da lista |
| **Índice** | `cache/index.json` + RAM | 1h | Filmes/séries/canais processados |
| **Info por ID** | `cache/info/*.json` | 24h | Metadados Xtream + TMDB por ID |
| **Cache TMDB** | `cache/tmdb/*.json` | 30 dias | Buscas na API do TMDB |
| **Novidades** | `cache/novidades.json` | — | Último diff detectado |
| **Slug map** | `cache/slug_map.json` | — | Mapa slug → item (regenerado no startup) |

### Tasks em background

| Task | Intervalo | O que faz |
|---|---|---|
| **Auto-refresh** | 1h | Baixa M3U nova, detecta novidades, troca índice atômico |
| **Enricher** | 60s | Processa 20 itens TMDB por lote (capa, logo, banner, score) |
| **Trailer Checker** | 6h | Re-verifica trailers faltantes no TMDB |

### Comportamento do cache

- **Startup**: se cache válido → carrega em < 1s. Senão → baixa em paralelo
- **Enquanto roda**: rotas leem da RAM (instantâneo)
- **Auto-refresh**: troca índice atômico (zero downtime)
- **Fallback**: se o provedor cair, usa o cache antigo normalmente
- **Nada é apagado automaticamente** — só sobrescrito em refresh bem-sucedido

---

## 🔧 Rodando como serviço (systemd no VPS)

Cria o arquivo `/etc/systemd/system/iptv.service`:

```ini
[Unit]
Description=IPTV Organizer Pro - Venus Cine
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/iptv
EnvironmentFile=/opt/iptv/.env
ExecStart=/opt/iptv/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Ativa:

```bash
systemctl daemon-reload
systemctl enable iptv
systemctl start iptv
systemctl status iptv
```

Ver logs ao vivo:

```bash
journalctl -u iptv -f
```

---

## 🌐 Configuração com Nginx + HTTPS

### Nginx (`/etc/nginx/sites-available/meusite`)

```nginx
server {
    listen 80;
    server_name seu-dominio.com;

    client_max_body_size 0;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # ESSENCIAL pra streaming
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_set_header Range $http_range;
        proxy_set_header If-Range $http_if_range;
        proxy_pass_request_headers on;
    }
}
```

### HTTPS com Certbot

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d seu-dominio.com
```

Renovação automática já fica configurada por padrão.

### Firewall

```bash
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

---

## 🎬 Como usar como player

### VLC (desktop/mobile)

Mídia → Abrir Fluxo de Rede → colar:

```
https://seu-dominio.com/stream/apesar-de-2024
```

### TiviMate / IPTV Smarters

Adicionar playlist M3U personalizada com URL:

```
https://seu-dominio.com/stream/{slug}
```

### App web (HTML)

O repositório inclui um exemplo de HTML que consome toda a API e monta um dashboard estilo Netflix. Veja o arquivo `index.html`.

---

## 📌 Compatibilidade

| Ambiente | Suportado | Observações |
|---|---|---|
| **Termux (Android ARM64)** | ✅ | Sem `uvicorn[standard]`, sem Rust |
| **Ubuntu 20.04+** | ✅ | Recomendado Python 3.10+ |
| **Debian 11+** | ✅ | Recomendado Python 3.10+ |
| **Ubuntu 18.04** | ⚠️ | Precisa instalar Python 3.9+ via `pyenv` |
| **Windows** | ✅ | Funciona, mas o deploy recomendado é Linux |
| **macOS** | ✅ | Funciona nativamente |

**Python 3.9** requer `pip install eval_type_backport` e `from __future__ import annotations` no topo de cada `.py`.

---

## 🛠️ Roadmap

- [ ] Suporte a EPG (guia de programação XMLTV)
- [ ] Autenticação de usuários finais na API
- [ ] Painel web de administração
- [ ] Notificações de novos lançamentos via webhook/Telegram
- [ ] Suporte a múltiplos provedores simultâneos
- [ ] Docker + docker-compose
- [ ] Throttle de banda por stream
- [ ] Cache de slug em disco com lazy load

---

## 📄 Licença

Este projeto está sob a licença **MIT**. Veja o arquivo `LICENSE` para mais detalhes.

Você é livre para usar, modificar, distribuir e usar comercialmente — só mantenha os créditos originais.

---

## 💬 Suporte

Dúvidas, sugestões ou bugs? Abre uma issue no repositório ou chama no Telegram.

**Feito com ❤️ para a comunidade IPTV brasileira.**