# logs.py
SYNC_ATIVO = False
IMPORT_LOGS = []

def adicionar_log(msg: str):
    global IMPORT_LOGS
    IMPORT_LOGS.append(msg)
    print(msg) # Continua printando no terminal da VPS

def iniciar_sync():
    global SYNC_ATIVO, IMPORT_LOGS
    SYNC_ATIVO = True
    IMPORT_LOGS.clear()
    adicionar_log("🚀 Iniciando sincronização...")

def finalizar_sync():
    global SYNC_ATIVO
    SYNC_ATIVO = False