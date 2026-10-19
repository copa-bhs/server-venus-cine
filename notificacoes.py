import os
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/admin/notificacoes", tags=["notificações"])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIREBASE_KEY_PATH = os.path.join(BASE_DIR, "firebase-key.json")

try:
    cred = credentials.Certificate(FIREBASE_KEY_PATH)
    firebase_admin.initialize_app(cred)
    db_firestore = firestore.client()
    print("[Firebase] Admin SDK inicializado com sucesso.")
except Exception as e:
    print(f"[Firebase] ERRO ao inicializar: {e}. Verifique firebase-key.json.")
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

@router.post("/enviar")
async def enviar_notificacao(payload: NotificacaoPayload):
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        doc_ref = db_firestore.collection('notificacoes').document()
        doc_data = {'title': payload.title, 'body': payload.body, 'imageUrl': payload.imageUrl, 'type': payload.type, 'createdAt': firestore.SERVER_TIMESTAMP, 'readBy': [], 'deletedBy': []}
        if payload.type == 'lancamento' and payload.itemId:
            doc_data['itemId'] = str(payload.itemId)
            doc_data['itemType'] = payload.itemType or 'filme'
        doc_ref.set(doc_data)
    except Exception as e: raise HTTPException(500, f"Erro ao salvar no Firestore: {e}")

    tokens = []
    try:
        if payload.target == 'all':
            usuarios = db_firestore.collection('usuarios').stream()
            for user in usuarios:
                for t in user.reference.collection('fcm_tokens').stream(): tokens.append(t.id)
        elif payload.target == 'specific' and payload.uid:
            for t in db_firestore.collection('usuarios').document(payload.uid).collection('fcm_tokens').stream(): tokens.append(t.id)
    except Exception as e: print(f"Erro tokens: {e}")

    if not tokens: return {"ok": True, "msg": "Salva no app, mas ninguém possui token FCM.", "enviados": 0}

    fcm_data = {'type': payload.type, 'imageUrl': payload.imageUrl or '', 'click_action': 'FLUTTER_NOTIFICATION_CLICK'}
    if payload.type == 'lancamento' and payload.itemId:
        fcm_data['itemId'] = str(payload.itemId)
        fcm_data['itemType'] = payload.itemType or 'filme'

    mensagem = messaging.MulticastMessage(
        notification=messaging.Notification(title=payload.title, body=payload.body, image=payload.imageUrl if payload.imageUrl else None),
        data=fcm_data, tokens=tokens,
        android=messaging.AndroidConfig(priority='high', notification=messaging.AndroidNotification(image=payload.imageUrl if payload.imageUrl else None)),
        apns=messaging.APNSConfig(payload=messaging.APNSPayload(aps=messaging.Aps(mutable_content=True)))
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
        print(f"ERRO HISTORICO: {e}")
        raise HTTPException(500, f"Erro: {e}")

# ==========================================================
# PEDIDOS DE CONTEÚDO (APP -> PAINEL) - CORRIGIDO
# ==========================================================
@router.get("/pedidos")
async def listar_pedidos():
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        # ⚡ CORREÇÃO: Busca na coleção 'pedidos_conteudo' (nome que está no app)
        docs = db_firestore.collection('pedidos_conteudo').stream()
        out = []
        for doc in docs:
            d = doc.to_dict()
            
            status = d.get('status', 'pendente')
            if status != 'pendente':
                continue
                
            created_at = d.get('createdAt')
            if created_at:
                try: d['createdAt'] = created_at.isoformat()
                except: d['createdAt'] = str(created_at)
            else:
                d['createdAt'] = None
                
            # ⚡ CORREÇÃO: Mapeia os campos que o app envia (titulo/tipo) para o painel ler (title/type)
            out.append({
                "id": doc.id,
                "title": d.get('titulo', 'Desconhecido'),
                "type": d.get('tipo', 'filme'),
                "status": status,
                "createdAt": d['createdAt'],
                "capa": d.get('capa', ''),
                "id_iptv": d.get('id_iptv', '')
            })
            
        print(f"[Pedidos] Encontrados {len(out)} pedidos pendentes.")
        return {"ok": True, "items": out}
    except Exception as e:
        print(f"ERRO PEDIDOS: {e}")
        raise HTTPException(500, f"Erro: {e}")

@router.post("/pedidos/{pedido_id}/concluir")
async def concluir_pedido(pedido_id: str):
    if not db_firestore: raise HTTPException(500, "Firebase não configurado.")
    try:
        # ⚡ CORREÇÃO: Atualiza na coleção correta
        db_firestore.collection('pedidos_conteudo').document(pedido_id).update({'status': 'concluido'})
        return {"ok": True}
    except Exception as e: raise HTTPException(500, f"Erro: {e}")