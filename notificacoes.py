import os
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from db import get_db, Filme as FilmeDB, Serie as SerieDB

router = APIRouter(prefix="/admin/notificacoes", tags=["notificações"])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIREBASE_KEY_PATH = os.path.join(BASE_DIR, "firebase-key.json")

try:
    cred = credentials.Certificate(FIREBASE_KEY_PATH)
    firebase_admin.initialize_app(cred)
    db_firestore = firestore.client()
    print("[Firebase] Admin SDK inicializado com sucesso.")
except Exception as e:
    print(f"[Firebase] ERRO ao inicializar: {e}.")
    db_firestore = None

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
    tipo: str # "filme" ou "serie"

@router.post("/enviar")
async def enviar_notificacao(payload: NotificacaoPayload):
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        doc_ref = db_firestore.collection('notificacoes').document()
        doc_data = {'title': payload.title, 'body': payload.body, 'imageUrl': payload.imageUrl, 'type': payload.type, 'createdAt': firestore.SERVER_TIMESTAMP, 'readBy': [], 'deletedBy': []}
        if payload.target == 'specific' and payload.uid:
            doc_data['targetUid'] = payload.uid
        if payload.type == 'lancamento' and payload.itemId:
            doc_data['itemId'] = str(payload.itemId)
            doc_data['itemType'] = payload.itemType or 'filme'
            
        doc_ref.set(doc_data)
    except Exception as e: raise HTTPException(500, f"Erro: {e}")

    tokens = []
    try:
        if payload.target == 'all':
            for user in db_firestore.collection('usuarios').stream():
                for t in user.reference.collection('fcm_tokens').stream(): tokens.append(t.id)
        elif payload.target == 'specific' and payload.uid:
            for t in db_firestore.collection('usuarios').document(payload.uid).collection('fcm_tokens').stream(): tokens.append(t.id)
    except: pass

    if not tokens: return {"ok": True, "msg": "Ninguém possui token.", "enviados": 0}

    fcm_data = {'type': payload.type, 'imageUrl': payload.imageUrl or ''}
    if payload.type == 'lancamento' and payload.itemId:
        fcm_data['itemId'] = str(payload.itemId)
        fcm_data['itemType'] = payload.itemType or 'filme'

    mensagem = messaging.MulticastMessage(
        notification=messaging.Notification(title=payload.title, body=payload.body, image=payload.imageUrl if payload.imageUrl else None),
        data=fcm_data, tokens=tokens,
        android=messaging.AndroidConfig(priority='high'),
    )
    response = messaging.send_each_for_multicast(mensagem)
    return {"ok": True, "sucesso": response.success_count, "msg": f"Enviado para {response.success_count} dispositivos."}

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
    except Exception as e: raise HTTPException(500, f"Erro: {e}")

# ==========================================================
# PEDIDOS DE CONTEÚDO
# ==========================================================
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
                "id": doc.id, "title": d.get('titulo', 'Desconhecido'), "type": d.get('tipo', 'filme'),
                "status": 'pendente', "createdAt": d.get('createdAt').isoformat() if d.get('createdAt') else None,
                "solicitanteNome": d.get('solicitanteNome', 'Anônimo'), "solicitanteUid": d.get('solicitanteUid', '')
            })
        return {"ok": True, "items": out}
    except Exception as e: raise HTTPException(500, f"Erro: {e}")

@router.post("/pedidos/{pedido_id}/concluir")
async def concluir_pedido(pedido_id: str, payload: PedidoConcluidoPayload, db: Session = Depends(get_db)):
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        # 1. Busca o pedido no Firestore para pegar o UID e o Nome
        doc_ref = db_firestore.collection('pedidos_conteudo').document(pedido_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(404, "Pedido não encontrado")
            
        d = doc.to_dict()
        uid = d.get('solicitanteUid')
        nome = d.get('solicitanteNome', 'Usuário')
        
        if not uid:
            raise HTTPException(400, "Este pedido não tem UID de usuário. Não é possível notificar.")

        # 2. Busca o item no nosso banco SQLite para pegar o ID correto e a Capa
        item_id = None
        capa_url = d.get('capa', '')
        
        if payload.tipo == "serie":
            item_db = db.query(SerieDB).filter(SerieDB.titulo == payload.titulo).first()
            if item_db:
                item_id = str(item_db.iptv_id)
                capa_url = item_db.capa or capa_url
        else:
            item_db = db.query(FilmeDB).filter(FilmeDB.titulo == payload.titulo).first()
            if item_db:
                item_id = str(item_db.iptv_id)
                capa_url = item_db.capa or capa_url

        if not item_id:
            item_id = d.get('id_iptv', '') # Fallback

        # 3. Atualiza o status no Firestore
        doc_ref.update({'status': 'concluido'})
        
        tipo_str = "Série" if payload.tipo == "serie" else "Filme"
        msg_titulo = "Pedido Aprovado! 🎉"
        msg_corpo = f"Obrigado {nome}! O {tipo_str} '{payload.titulo}' que você pediu foi adicionado ao catálogo e marcado com o seu nome no perfil."
        
        # 4. ⚡ SALVA NA TELA DE NOTIFICAÇÕES IN-APP DO APP (Firestore)
        doc_notif = db_firestore.collection('notificacoes').document()
        doc_notif.set({
            'title': msg_titulo,
            'body': msg_corpo,
            'type': 'lancamento', # Avisa o app para abrir o card
            'targetUid': uid,     # Apenas este usuário verá
            'itemId': item_id,    # ID para abrir o filme
            'itemType': payload.tipo,
            'imageUrl': capa_url,
            'createdAt': firestore.SERVER_TIMESTAMP,
            'readBy': [],
            'deletedBy': []
        })
        
        # 5. ⚡ ENVIA O PUSH NOTIFICATION (FCM) APENAS PARA O UID DO SOLICITANTE
        tokens = [t.id for t in db_firestore.collection('usuarios').document(uid).collection('fcm_tokens').stream()]
        
        if tokens:
            msg = messaging.MulticastMessage(
                notification=messaging.Notification(title=msg_titulo, body=msg_corpo, image=capa_url if capa_url else None),
                data={
                    'type': 'lancamento',
                    'itemId': item_id,
                    'itemType': payload.tipo,
                    'imageUrl': capa_url or ''
                },
                tokens=tokens,
                android=messaging.AndroidConfig(priority='high')
            )
            messaging.send_each_for_multicast(msg)
                    
        return {"ok": True}
    except Exception as e: 
        print(f"Erro ao concluir: {e}")
        raise HTTPException(500, f"Erro: {e}")