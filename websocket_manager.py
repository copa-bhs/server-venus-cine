"""Gerenciador de WebSocket para atualizações em tempo real."""
import asyncio
import json
from typing import List

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.active.append(ws)
        print(f"[WS] Cliente conectado. Total: {len(self.active)}")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)
        print(f"[WS] Cliente desconectado. Total: {len(self.active)}")

    async def broadcast(self, evento: str, dados: dict = None):
        mensagem = json.dumps(
            {"evento": evento, "dados": dados or {}},
            ensure_ascii=False,
        )
        mortos = []
        for ws in list(self.active):
            try:
                await ws.send_text(mensagem)
            except Exception:
                mortos.append(ws)
        for ws in mortos:
            await self.disconnect(ws)

    def total(self) -> int:
        return len(self.active)


manager = ConnectionManager()