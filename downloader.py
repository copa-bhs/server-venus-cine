import asyncio
import httpx
from pathlib import Path
from typing import Optional

from config import (
    USER_AGENT, DOWNLOAD_CHUNK_SIZE, DOWNLOAD_WORKERS,
    DOWNLOAD_CONNECT_TIMEOUT, DOWNLOAD_READ_TIMEOUT,
)


async def _get_file_size(client: httpx.AsyncClient, url: str) -> Optional[int]:
    """Faz HEAD pra descobrir tamanho e se suporta Range."""
    try:
        r = await client.head(url, headers={"User-Agent": USER_AGENT})
        if r.status_code >= 400:
            return None
        accepts_ranges = r.headers.get("accept-ranges", "").lower() == "bytes"
        size = r.headers.get("content-length")
        if accepts_ranges and size:
            return int(size)
    except Exception:
        pass
    return None


async def _download_chunk(
    client: httpx.AsyncClient, url: str, start: int, end: int,
    chunk_path: Path, progress: dict, idx: int,
):
    """Baixa um pedaço via Range request."""
    headers = {
        "User-Agent": USER_AGENT,
        "Range": f"bytes={start}-{end}",
    }
    async with client.stream("GET", url, headers=headers) as r:
        r.raise_for_status()
        with open(chunk_path, "wb") as f:
            async for piece in r.aiter_bytes(DOWNLOAD_CHUNK_SIZE):
                f.write(piece)
                progress[idx] += len(piece)


async def parallel_download(url: str, dest: Path) -> dict:
    """
    Download paralelo de um arquivo grande.
    - Detecta tamanho via HEAD
    - Divide em N workers
    - Faz Range requests em paralelo
    - Concatena os pedaços no final
    """
    timeout = httpx.Timeout(
        connect=DOWNLOAD_CONNECT_TIMEOUT,
        read=DOWNLOAD_READ_TIMEOUT,
        write=30.0,
        pool=None,
    )
    limits = httpx.Limits(
        max_connections=DOWNLOAD_WORKERS + 2,
        max_keepalive_connections=DOWNLOAD_WORKERS,
    )

    async with httpx.AsyncClient(
        timeout=timeout, limits=limits, follow_redirects=True
    ) as client:
        size = await _get_file_size(client, url)

        # --- Fallback: servidor não suporta Range, baixa em 1 stream ---
        if not size:
            headers = {"User-Agent": USER_AGENT}
            async with client.stream("GET", url, headers=headers) as r:
                r.raise_for_status()
                total = 0
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(DOWNLOAD_CHUNK_SIZE):
                        f.write(chunk)
                        total += len(chunk)
            return {"mode": "single", "bytes": total}

        # --- Download paralelo com Range ---
        workers = min(DOWNLOAD_WORKERS, max(1, size // (512 * 1024)))
        chunk_size = size // workers

        chunks_dir = dest.parent / f"{dest.name}.parts"
        chunks_dir.mkdir(exist_ok=True)

        ranges = []
        for i in range(workers):
            start = i * chunk_size
            end = size - 1 if i == workers - 1 else (start + chunk_size - 1)
            ranges.append((i, start, end, chunks_dir / f"part_{i:02d}.bin"))

        progress = {i: 0 for i, *_ in ranges}

        tasks = [
            _download_chunk(client, url, start, end, path, progress, idx)
            for idx, start, end, path in ranges
        ]
        await asyncio.gather(*tasks)

        # concatena
        with open(dest, "wb") as out:
            for idx, _, _, path in sorted(ranges):
                with open(path, "rb") as p:
                    while True:
                        buf = p.read(DOWNLOAD_CHUNK_SIZE)
                        if not buf:
                            break
                        out.write(buf)
                path.unlink()

        try:
            chunks_dir.rmdir()
        except OSError:
            pass

        return {
            "mode": "parallel",
            "workers": workers,
            "bytes": size,
        }