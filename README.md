# 🎬 IPTV Organizer Pro

**Versão:** 2.0.0  
**Linguagem:** Python 3.10+  
**Framework:** FastAPI + Uvicorn  
**Autor:** Vênus Cine 🍿
**Licença:** MIT

---

## 📖 O que é este projeto?

O **IPTV Organizer Pro** é um servidor intermediário que organiza, categoriza e serve conteúdo IPTV (filmes, séries e canais ao vivo) de forma rápida, paginada e resiliente, a partir de uma lista M3U fornecida por um provedor Xtream Codes.

Ele atua como uma **API REST** entre o provedor IPTV e o seu aplicativo cliente (web, mobile, TV, etc.), entregando os dados já limpos, classificados e prontos para consumo.

---

## ✨ O que ele FAZ

- ✅ Baixa a lista M3U do provedor em **download paralelo** (até 8 conexões simultâneas via HTTP Range)
- ✅ **Classifica automaticamente** os itens em filmes, séries e canais ao vivo
- ✅ **Limpa os títulos** removendo hashtags, tags técnicas, ano entre parênteses, prefixos como "FILME:" e sufixos de episódio (S01E02)
- ✅ **Agrupa episódios** de séries por título base, organizados por temporada
- ✅ **Extrai o ID numérico** do provedor direto da URL Xtream Codes (usado como chave para metadados)
- ✅ **Cache em disco com TTL de 1 hora** para a lista principal (evita requisições desnecessárias ao provedor)
- ✅ **Cache individual de metadados por ID** com TTL de 24 horas
- ✅ **Paginação** em todas as rotas de conteúdo (não carrega tudo de uma vez)
- ✅ **Índice em memória RAM** para resposta instantânea (carregado uma vez no startup)
- ✅ **Fallback automático** para o cache antigo se o provedor cair
- ✅ **Auto-refresh em background** que atualiza o índice a cada hora sem downtime
- ✅ **Rota de metadados** que busca sinopse, capa, banner, trailer, score, duração, gênero e elenco via API do Xtream Codes
- ✅ **Compatível com Termux (Android)** e **VPS Linux (Ubuntu/Debian)**
- ✅ **Não exige compilação Rust** (100% Python puro + wheels pré-compilados)

---

## ❌ O que ele NÃO faz

- ❌ **Não armazena nem faz proxy dos vídeos** — apenas organiza os links. O streaming acontece direto entre o player do cliente e o servidor IPTV
- ❌ **Não transcodifica** vídeo (não converte formato/resolução)
- ❌ **Não possui interface visual** — é uma API REST pura (JSON)
- ❌ **Não controla autenticação de usuários finais** — o token de acesso ao conteúdo é a própria credencial do provedor IPTV
- ❌ **Não gerencia assinaturas** ou cobra usuários
- ❌ **Não substitui o Xtream Codes** — é apenas uma camada de organização em cima dele
- ❌ **Não suporta EPG (guia de programação)** ainda — recurso planejado para versão futura
- ❌ **Não envia notificações push** nem tem painel administrativo

---

## 🏗️ Arquitetura

```
┌────────────────────┐
│  App do usuário    │  (web / mobile / TV / VLC / TiviMate)
└─────────┬──────────┘
          │  HTTP/JSON
          ▼
┌────────────────────────────────────┐
│  IPTV Organizer Pro (este projeto) │
│  • FastAPI                         │
│  • Cache em disco (1h)             │
│  • Índice em RAM                   │
│  • Cache de metadados (24h)        │
└─────────┬──────────────────────────┘
          │  HTTP
          ▼
┌────────────────────┐
│  Provedor IPTV     │  (Xtream Codes)
│  Lista M3U + API   │
└────────────────────┘
```

---

## 📂 Estrutura de arquivos

```
iptv-organizer/
├── main.py            # Servidor FastAPI (rotas + paginação + startup + auto-refresh)
├── config.py          # Constantes (URL, TTL, workers, paths) e leitura do .env
├── parser.py          # Parser M3U + limpeza de nome + extração de ID + índice
├── downloader.py      # Download paralelo estilo IDM (Range requests)
├── info.py            # Metadados via ID (filme/série) com cache 24h
├── requirements.txt   # Dependências Python
├── .env               # Credenciais (NÃO versionar — gerado localmente)
├── .env.example       # Modelo de variáveis de ambiente
├── .gitignore         # Arquivos ignorados pelo Git
└── cache/             # Cache local (gerado em runtime)
    ├── playlist.m3u   # Lista M3U bruta baixada
    ├── meta.json      # Timestamp do último refresh
    ├── index.json     # Índice pré-processado
    └── info/          # Metadados individuais por ID (24h TTL)
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

# 4. Criar o .env (opcional — usa defaults se não existir)
nano .env

# 5. Rodar
python main.py
```

### 🖥️ Em VPS Linux (Ubuntu/Debian)

```bash
# 1. Atualizar sistema e instalar dependências do sistema
apt update && apt upgrade -y
apt install -y python3.10 python3.10-venv python3.10-dev git curl ufw

# 2. Clonar o projeto
cd /opt
git clone https://github.com/SEU_USUARIO/SEU_REPO.git iptv
cd iptv

# 3. Criar ambiente virtual
python3.10 -m venv venv
source venv/bin/activate

# 4. Instalar dependências
pip install --upgrade pip
pip install -r requirements.txt

# 5. Criar o .env
nano .env

# 6. Rodar
python main.py
```

### ⚙️ Variáveis de ambiente (.env)

```env
IPTV_USERNAME=SeuUsuario
IPTV_PASSWORD=SuaSenha
IPTV_HOST=http://provedor.com:80
IPTV_URL=http://provedor.com:80/get.php?username=SeuUsuario&password=SuaSenha&type=m3u_plus&output=ts
USER_AGENT=VLC/3.0.20 LibVLC/3.0.20
CACHE_TTL=3600
PORT=8000
```

---

## 📡 Rotas da API

Todas as rotas retornam **JSON**. Substitua `localhost` pelo IP do servidor quando for usar de outro dispositivo.

### 🏠 Básicas

| Método | Rota | Descrição |
|---|---|---|
| GET | `/` | Lista todos os endpoints disponíveis |
| GET | `/status` | Estado do cache + estatísticas (quantos filmes, séries, canais) |
| POST | `/refresh` | Força atualização do cache e do índice |

### 🎬 Conteúdo (com paginação)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/filmes?page=1&size=30&categoria=` | Lista filmes paginados |
| GET | `/series?page=1&size=30&categoria=` | Lista séries paginadas (resumo) |
| GET | `/canais?page=1&size=50&categoria=` | Lista canais de TV paginados |
| GET | `/buscar?q=texto&tipo=filme\|serie\|canal` | Busca paginada por nome |
| GET | `/categorias` | Lista todas as categorias disponíveis |

**Parâmetros de paginação:**
- `page` — número da página (padrão: 1)
- `size` — itens por página (padrão: 30, máximo: 200)
- `categoria` — filtra por categoria específica (opcional)

### 🔍 Metadados (via ID do provedor)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/info/filme/{id}` | Metadados completos do filme (banner, capa, sinopse, ano, score, trailer, duração, gênero, elenco) |
| GET | `/info/serie/{id}` | Metadados completos da série + lista de episódios por temporada |
| GET | `/info/filme/{id}?force=true` | Ignora cache e refaz a consulta |

---

## 🧪 Exemplos de uso (curl)

```bash
# 1) Estado geral
curl http://localhost:8000/status

# 2) Lista de filmes (página 1, 30 por página)
curl "http://localhost:8000/filmes?page=1&size=30"

# 3) Categorias disponíveis
curl http://localhost:8000/categorias

# 4) Filmes de uma categoria
curl "http://localhost:8000/filmes?categoria=FILMES%20%E2%80%A2%202024&size=20"

# 5) Detalhes de um filme pelo ID
curl http://localhost:8000/info/filme/2127

# 6) Detalhes de uma série (com episódios)
curl http://localhost:8000/info/serie/4521

# 7) Buscar
curl "http://localhost:8000/buscar?q=vingadores&tipo=filme"

# 8) Forçar atualização
curl -X POST http://localhost:8000/refresh
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
      "capa": "https://image.tmdb.org/t/p/w600_and_h900_bestv2/xxx.jpg",
      "ano": 2024,
      "categoria": "FILMES • 2024",
      "tipo": "filme",
      "url": "http://provedor.com/movie/User/Pass/2127.mp4"
    }
  ]
}
```

### Metadados de filme (`/info/filme/{id}`)

```json
{
  "id": "2127",
  "tipo": "filme",
  "titulo": "Apesar De",
  "titulo_original": "Anyway",
  "capa": "https://.../poster.jpg",
  "banner": "https://.../backdrop.jpg",
  "ano": "2024-03-15",
  "duracao": "1h 42min",
  "score": "7.4",
  "sinopse": "Um homem que...",
  "genero": "Drama, Romance",
  "elenco": "Fulano, Beltrano",
  "diretor": "João Silva",
  "trailer_url": "https://www.youtube.com/watch?v=xxx",
  "qualidade": "HD",
  "url_stream": "http://provedor.com/movie/User/Pass/2127.mp4"
}
```

### Metadados de série (`/info/serie/{id}`)

```json
{
  "id": "4521",
  "tipo": "serie",
  "titulo": "Breaking Bad",
  "capa": "https://.../poster.jpg",
  "banner": "https://.../backdrop.jpg",
  "ano": "2008",
  "score": "9.5",
  "sinopse": "Um professor de química...",
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
      "url_stream": "http://provedor.com/series/User/Pass/4521_1_1.mp4"
    }
  ]
}
```

---

## ⚡ Sistema de cache

O projeto usa **3 níveis de cache** para nunca sobrecarregar o provedor IPTV:

| Cache | Onde fica | TTL | O que guarda |
|---|---|---|---|
| **Lista M3U** | `cache/playlist.m3u` | 1 hora | Arquivo bruto da lista |
| **Índice** | `cache/index.json` + RAM | 1 hora | Filmes/séries/canais já processados |
| **Metadados** | `cache/info/*.json` | 24 horas | Info individual por ID |

**Como se comporta:**

- No **startup**: verifica se o cache é válido. Se sim, carrega em menos de 1 segundo. Se não, baixa em paralelo e processa.
- **Enquanto roda**: todas as rotas leem da RAM (resposta instantânea).
- **Auto-refresh**: uma task em background atualiza o índice a cada 1 hora sem derrubar o servidor (troca atômica).
- **Fallback**: se o provedor cair, o servidor continua servindo o cache antigo normalmente.

**Nada é apagado automaticamente.** Os arquivos são sobrescritos apenas quando um refresh bem-sucedido acontece.

---

## 🔧 Rodando como serviço (systemd no VPS)

Cria o arquivo `/etc/systemd/system/iptv.service`:

```ini
[Unit]
Description=IPTV Organizer Pro
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/iptv
EnvironmentFile=/opt/iptv/.env
ExecStart=/opt/iptv/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Ativa e inicia:

```bash
systemctl daemon-reload
systemctl enable iptv
systemctl start iptv
systemctl status iptv
```

Ver logs em tempo real:

```bash
journalctl -u iptv -f
```

---

## 🌐 Acessando de fora

Se o servidor estiver num VPS, libera a porta no firewall:

```bash
ufw allow 22/tcp
ufw allow 8000/tcp
ufw enable
```

Acessa de qualquer lugar:

```
http://IP_DO_VPS:8000/filmes?page=1&size=30
```

---

## 📌 Compatibilidade

| Ambiente | Suportado | Observações |
|---|---|---|
| **Termux (Android ARM64)** | ✅ | Sem `uvicorn[standard]`, sem Rust |
| **Ubuntu 20.04+** | ✅ | Recomendado Python 3.10+ |
| **Debian 11+** | ✅ | Recomendado Python 3.10+ |
| **Ubuntu 18.04** | ⚠️ | Precisa instalar Python 3.10 via PPA (deadsnakes) |
| **Windows** | ✅ | Funciona, mas o deploy recomendado é Linux |
| **macOS** | ✅ | Funciona nativamente |

---

## 🛠️ Roadmap (próximas features)

- [ ] Suporte a EPG (guia de programação XMLTV)
- [ ] Autenticação de usuários finais na API
- [ ] Painel web de administração
- [ ] Enriquecimento automático de metadados via TMDB (sinopse em PT-BR)
- [ ] Notificações de novos lançamentos via webhook
- [ ] Suporte a múltiplos provedores simultâneos
- [ ] Docker + docker-compose

---

## 📄 Licença

Este projeto está sob a licença **MIT**. Veja o arquivo `LICENSE` para mais detalhes.

Você é livre para usar, modificar, distribuir e usar comercialmente — só mantenha os créditos originais.

---

## 💬 Suporte

Dúvidas, sugestões ou bugs? Abre uma issue no repositório ou me chama no Telegram.

**Feito com ❤️ para a comunidade IPTV brasileira.**