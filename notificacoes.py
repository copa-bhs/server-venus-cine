import os
import firebase_admin
from firebase_admin import credentials, firestore, messaging, auth
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from db import get_db, Filme as FilmeDB, Serie as SerieDB

router = APIRouter(prefix="/admin/notificacoes", tags=["notificações"])
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIREBASE_KEY_PATH = os.path.join(BASE_DIR, "firebase-key.json")

# Inicializa o Firebase
try:
    cred = credentials.Certificate(FIREBASE_KEY_PATH)
    firebase_admin.initialize_app(cred)
    db_firestore = firestore.client()
    print("[Firebase] Iniciado com sucesso.")
except Exception as e:
    print(f"[Firebase] Erro ao iniciar: {e}")
    db_firestore = None

# Modelos de Payload
class NotificacaoPayload(BaseModel):
    title: str
    body: str
    imageUrl: Optional[str] = ""
    type: str
    target: str
    uid: Optional[str] = None
    itemId: Optional[str] = None
    itemType: Optional[str] = None

class PedidoConcluidoPayload(BaseModel):
    titulo: str
    tipo: str

class AlterarPlanoPayload(BaseModel):
    plano: str

# ==========================================
# ROTAS DE NOTIFICAÇÃO MANUAL
# ==========================================

@router.post("/enviar")
async def enviar_notificacao(payload: NotificacaoPayload):
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    
    # 1. Salva no histórico do Firestore
    try:
        doc_ref = db_firestore.collection('notificacoes').document()
        doc_data = {
            'title': payload.title, 'body': payload.body, 'imageUrl': payload.imageUrl, 
            'type': payload.type, 'createdAt': firestore.SERVER_TIMESTAMP, 'readBy': [], 'deletedBy': []
        }
        if payload.target == 'specific' and payload.uid: 
            doc_data['targetUid'] = payload.uid
        if payload.type == 'lancamento' and payload.itemId: 
            doc_data['itemId'] = str(payload.itemId); doc_data['itemType'] = payload.itemType or 'filme'
        doc_ref.set(doc_data)
    except Exception as e: 
        raise HTTPException(500, f"Erro ao salvar histórico: {e}")

    # 2. Busca os tokens FCM
    tokens = []
    try:
        if payload.target == 'all':
            for user in db_firestore.collection('usuarios').stream():
                for t in user.reference.collection('fcm_tokens').stream(): 
                    tokens.append(t.id)
        elif payload.target == 'specific' and payload.uid:
            for t in db_firestore.collection('usuarios').document(payload.uid).collection('fcm_tokens').stream(): 
                tokens.append(t.id)
    except Exception: 
        pass

    if not tokens: 
        return {"ok": True, "msg": "Ninguém possui token cadastrado.", "enviados": 0}

    # 3. Dispara o Push
    fcm_data = {'type': payload.type, 'imageUrl': payload.imageUrl or ''}
    if payload.type == 'lancamento' and payload.itemId: 
        fcm_data['itemId'] = str(payload.itemId); fcm_data['itemType'] = payload.itemType or 'filme'
    
    mensagem = messaging.MulticastMessage(
        notification=messaging.Notification(title=payload.title, body=payload.body, image=payload.imageUrl if payload.imageUrl else None), 
        data=fcm_data, 
        tokens=tokens, 
        android=messaging.AndroidConfig(priority='high')
    )
    response = messaging.send_each_for_multicast(mensagem)
    return {"ok": True, "sucesso": response.success_count, "falha": response.failure_count, "msg": f"Enviado para {response.success_count} dispositivos."}

@router.get("/historico")
async def listar_historico():
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        docs = db_firestore.collection('notificacoes').order_by('createdAt', direction=firestore.Query.DESCENDING).limit(50).stream()
        historico = []
        for doc in docs:
            d = doc.to_dict()
            if d.get('createdAt'): d['createdAt'] = d['createdAt'].isoformat()
            d['id'] = doc.id
            historico.append(d)
        return {"ok": True, "items": historico}
    except Exception as e: 
        raise HTTPException(500, f"Erro: {e}")

# ==========================================
# ROTAS DE USUÁRIOS
# ==========================================

@router.get("/usuarios")
async def listar_usuarios():
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        docs = db_firestore.collection('usuarios').stream()
        out = []
        for doc in docs:
            d = doc.to_dict()
            out.append({
                "uid": doc.id, 
                "nome": d.get("nome") or d.get("displayName") or "Sem Nome", 
                "email": d.get("email") or "Sem Email", 
                "foto": d.get("foto") or d.get("photoURL") or "", 
                "bloqueado": d.get("bloqueado", False), 
                "plano": d.get("plano", "Grátis")
            })
        out.sort(key=lambda x: x["nome"].lower())
        return {"ok": True, "items": out}
    except Exception as e: 
        raise HTTPException(500, f"Erro: {e}")

@router.post("/usuarios/{uid}/bloquear")
async def bloquear_usuario(uid: str, bloqueado: bool = True):
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        auth.update_user(uid, disabled=bloqueado)
        db_firestore.collection('usuarios').document(uid).update({'bloqueado': bloqueado})
        return {"ok": True}
    except Exception as e: 
        raise HTTPException(500, f"Erro: {e}")

@router.delete("/usuarios/{uid}")
async def deletar_usuario(uid: str):
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        auth.delete_user(uid)
        db_firestore.collection('usuarios').document(uid).delete()
        return {"ok": True}
    except Exception as e: 
        raise HTTPException(500, f"Erro: {e}")

@router.post("/usuarios/{uid}/alterar-plano")
async def alterar_plano(uid: str, payload: AlterarPlanoPayload):
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        db_firestore.collection('usuarios').document(uid).update({'plano': payload.plano})
        
        msg_titulo = "Seu plano foi atualizado! 🌟"
        msg_corpo = f"Olá! Seu novo plano agora é: {payload.plano}. Aproveite todos os benefícios!"
        
        tokens = [t.id for t in db_firestore.collection('usuarios').document(uid).collection('fcm_tokens').stream()]
        if tokens:
            msg = messaging.MulticastMessage(
                notification=messaging.Notification(title=msg_titulo, body=msg_corpo), 
                data={'type': 'plano', 'plano': payload.plano}, 
                tokens=tokens, 
                android=messaging.AndroidConfig(priority='high')
            )
            messaging.send_each_for_multicast(msg)
            
        doc_notif = db_firestore.collection('notificacoes').document()
        doc_notif.set({
            'title': msg_titulo, 'body': msg_corpo, 'type': 'geral', 'targetUid': uid, 
            'createdAt': firestore.SERVER_TIMESTAMP, 'readBy': [], 'deletedBy': []
        })
        return {"ok": True}
    except Exception as e: 
        raise HTTPException(500, f"Erro: {e}")

# ==========================================
# ROTAS DE PEDIDOS
# ==========================================

@router.get("/pedidos")
async def listar_pedidos():
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        docs = db_firestore.collection('pedidos_conteudo').stream()
        out = []
        for doc in docs:
            d = doc.to_dict()
            if d.get('status', 'pendente') != 'pendente': continue
            out.append({
                "id": doc.id, 
                "title": d.get('titulo', 'Desconhecido'), 
                "type": d.get('tipo', 'filme'), 
                "status": 'pendente', 
                "createdAt": d.get('createdAt').isoformat() if d.get('createdAt') else None, 
                "solicitanteNome": d.get('solicitanteNome', 'Anônimo'), 
                "solicitanteUid": d.get('solicitanteUid', '')
            })
        return {"ok": True, "items": out}
    except Exception as e: 
        raise HTTPException(500, f"Erro: {e}")

@router.post("/pedidos/{pedido_id}/concluir")
async def concluir_pedido(pedido_id: str, payload: PedidoConcluidoPayload, db: Session = Depends(get_db)):
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        doc_ref = db_firestore.collection('pedidos_conteudo').document(pedido_id)
        doc = doc_ref.get()
        if not doc.exists: raise HTTPException(404, "Pedido não encontrado")
        
        d = doc.to_dict(); uid = d.get('solicitanteUid'); nome = d.get('solicitanteNome', 'Usuário')
        if not uid: raise HTTPException(400, "Pedido sem UID.")
        
        item_id = None; capa_url = d.get('capa', '')
        if payload.tipo == "serie":
            item_db = db.query(SerieDB).filter(SerieDB.title == payload.titulo).first()
            if item_db: item_id = str(item_db.iptv_id); capa_url = item_db.poster or capa_url
        else:
            item_db = db.query(FilmeDB).filter(FilmeDB.title == payload.titulo).first()
            if item_db: item_id = str(item_db.iptv_id); capa_url = item_db.poster or capa_url
        
        if not item_id: item_id = d.get('id_iptv', '')
        doc_ref.update({'status': 'concluido'})
        
        tipo_str = "Série" if payload.tipo == "serie" else "Filme"
        msg_titulo = "Pedido Aprovado! 🎉"
        msg_corpo = f"Obrigado {nome}! O {tipo_str} '{payload.titulo}' que você pediu foi adicionado ao catálogo."
        
        doc_notif = db_firestore.collection('notificacoes').document()
        doc_notif.set({
            'title': msg_titulo, 'body': msg_corpo, 'type': 'lancamento', 'targetUid': uid, 
            'itemId': item_id, 'itemType': payload.tipo, 'imageUrl': capa_url, 
            'createdAt': firestore.SERVER_TIMESTAMP, 'readBy': [], 'deletedBy': []
        })
        
        tokens = [t.id for t in db_firestore.collection('usuarios').document(uid).collection('fcm_tokens').stream()]
        if tokens:
            msg = messaging.MulticastMessage(
                notification=messaging.Notification(title=msg_titulo, body=msg_corpo, image=capa_url if capa_url else None), 
                data={'type': 'lancamento', 'itemId': item_id, 'itemType': payload.tipo, 'imageUrl': capa_url or ''}, 
                tokens=tokens, 
                android=messaging.AndroidConfig(priority='high')
            )
            messaging.send_each_for_multicast(msg)
        return {"ok": True}
    except Exception as e: 
        raise HTTPException(500, f"Erro: {e}")

# ==========================================
# FUNÇÃO DE DISPARO AUTOMÁTICO (Usada pelo main.py)
# ==========================================

async def disparar_push_geral(titulo: str, corpo: str, imagem: str = None, item_id: str = None, item_type: str = None):
    """Dispara um push para todos os usuários quando um conteúdo novo é adicionado."""
    if not db_firestore: return
    try:
        # 1. Salva no histórico
        doc_ref = db_firestore.collection('notificacoes').document()
        doc_data = {
            'title': titulo, 'body': corpo, 'imageUrl': imagem, 
            'type': 'lancamento', 'createdAt': firestore.SERVER_TIMESTAMP, 'readBy': [], 'deletedBy': []
        }
        if item_id: 
            doc_data['itemId'] = str(item_id); doc_data['itemType'] = item_type
        doc_ref.set(doc_data)

        # 2. Pega todos os tokens
        tokens = []
        for user in db_firestore.collection('usuarios').stream():
            for t in user.reference.collection('fcm_tokens').stream(): 
                tokens.append(t.id)
        
        if not tokens: return

        # 3. Dispara a mensagem
        fcm_data = {'type': 'lancamento'}
        if item_id: 
            fcm_data['itemId'] = str(item_id); fcm_data['itemType'] = item_type
        if imagem: 
            fcm_data['imageUrl'] = imagem

        mensagem = messaging.MulticastMessage(
            notification=messaging.Notification(title=titulo, body=corpo, image=imagem if imagem else None),
            data=fcm_data, 
            tokens=tokens, 
            android=messaging.AndroidConfig(priority='high')
        )
        messaging.send_each_for_multicast(mensagem)
        print(f"[Firebase] Push automático enviado: {titulo}")
    except Exception as e:
        print(f"[Firebase] Erro no push geral: {e}")