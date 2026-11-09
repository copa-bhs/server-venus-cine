import httpx
from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import StreamingResponse
from config import USER_AGENT, PROXY_CONNECT_TIMEOUT, PROXY_READ_TIMEOUT, PROXY_CHUNK_SIZE

router = APIRouter()

# Dicionário em memória para mapear os slugs/IDs para URLs reais do IPTV
_PROXY_MAP = {}

def register_item(item_id: str, stream_url: str, title: str, tipo: str):
    _PROXY_MAP[str(item_id)] = {
        "url": stream_url,
        "title": title,
        "tipo": tipo
    }

# 🚀 Cliente HTTP global otimizado com Pool de Conexões para reuso instantâneo (Evita o handshake lento a cada clique)
_HTTP_CLIENT = httpx.AsyncClient(
    timeout=httpx.Timeout(10.0, read=PROXY_READ_TIMEOUT),
    follow_redirects=True,
    limits=httpx.Limits(max_keepalive_connections=50, max_connections=200, keepalive_expiry=30.0)
)

@router.get("/stream/id/{slug}")
async def proxy_stream(slug: str, request: Request):
    # Remove extensão .mp4 ou prefixos para extrair o ID limpo
    clean_id = slug.replace("movie_", "").replace("tv_", "").split(".")[0]
    
    item = _PROXY_MAP.get(clean_id)
    if not item:
        raise HTTPException(status_code=404, detail="Stream não encontrado no mapeamento do proxy.")
    
    target_url = item["url"]
    
    # Prepara os headers repassando o Range para permitir seek rápido no player
    headers = {"User-Agent": USER_AGENT}
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    try:
        # Abre a requisição mantendo o fluxo contínuo
        req = _HTTP_CLIENT.build_request("GET", target_url, headers=headers)
        r = await _HTTP_CLIENT.send(req, stream=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao conectar no servidor IPTV: {str(e)}")

    resp_headers = {
        "Content-Type": r.headers.get("content-type", "video/mp4"),
        "Accept-Ranges": "bytes",
        "Connection": "keep-alive",
    }
    if "content-range" in r.headers:
        resp_headers["Content-Range"] = r.headers["content-range"]
    if "content-length" in r.headers:
        resp_headers["Content-Length"] = r.headers["content-length"]

    async def stream_generator():
        try:
            # 🚀 Chunk size ampliado (definido no config ou forçado para pacotes grandes de velocidade)
            async for chunk in r.aiter_bytes(chunk_size=max(PROXY_CHUNK_SIZE, 1048576)): # Mínimo de 1MB por pacote
                if chunk:
                    yield chunk
        except (httpx.RequestError, httpx.TimeoutException, GeneratorExit):
            pass
        finally:
            await r.aclose()

    return StreamingResponse(
        stream_generator(),
        status_code=r.status_code,
        headers=resp_headers
    )
