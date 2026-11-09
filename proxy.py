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

@router.get("/stream/id/{slug}")
async def proxy_stream(slug: str, request: Request):
    # Remove extensão .mp4 ou prefixos para extrair o ID limpo
    clean_id = slug.replace("movie_", "").replace("tv_", "").split(".")[0]
    
    item = _PROXY_MAP.get(clean_id)
    if not item:
        raise HTTPException(status_code=404, detail="Stream não encontrado no mapeamento do proxy.")
    
    target_url = item["url"]
    
    # Prepara os headers da requisição original (essencial para o Range de reprodução/seek)
    headers = {"User-Agent": USER_AGENT}
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=httpx.Timeout(PROXY_CONNECT_TIMEOUT, read=PROXY_READ_TIMEOUT), follow_redirects=True)

    async def stream_generator():
        try:
            async with client:
                async with client.stream("GET", target_url, headers=headers) as r:
                    # Repassa o status code (200 OK ou 206 Partial Content)
                    status_code = r.status_code
                    
                    async for chunk in r.aiter_bytes(chunk_size=PROXY_CHUNK_SIZE):
                        if chunk:
                            yield chunk
        except (httpx.RequestError, httpx.TimeoutException, GeneratorExit):
            pass # Trata desconexões do cliente ou oscilações do provedor sem quebrar o servidor
        finally:
            await client.aclose()

    # Captura headers iniciais para espelhar a resposta do IPTV
    try:
        req_initial = client.build_request("GET", target_url, headers=headers)
        r_init = await client.send(req_initial, stream=True)
        
        resp_headers = {
            "Content-Type": r_init.headers.get("content-type", "video/mp4"),
            "Accept-Ranges": "bytes",
        }
        if "content-range" in r_init.headers:
            resp_headers["Content-Range"] = r_init.headers["content-range"]
        if "content-length" in r_init.headers:
            resp_headers["Content-Length"] = r_init.headers["content-length"]
            
        status = r_init.status_code
        await r_init.aclose()
        await client.aclose()
        
        return StreamingResponse(
            stream_generator(),
            status_code=status,
            headers=resp_headers
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro de comunicação com o provedor IPTV: {str(e)}")
