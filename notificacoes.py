"""
Módulo de Notificações Push e In-App (FCM + Firestore)
"""
import os
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/admin/notificacoes", tags=["notificações"])

# ==========================================================
# INICIALIZAÇÃO FIREBASE
# ==========================================================
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


# ==========================================================
# MODELOS
# ==========================================================
class NotificacaoPayload(BaseModel):
    title: str
    body: str
    imageUrl: Optional[str] = ""
    type: str  # 'geral', 'plano' ou 'lancamento'
    target: str  # 'all' ou 'specific'
    uid: Optional[str] = None
    itemId: Optional[str] = None      # ⚡ OBRIGATÓRIO PARA LANÇAMENTO
    itemType: Optional[str] = None    # ⚡ 'filme' ou 'serie'


# ==========================================================
# ENVIAR NOTIFICAÇÃO
# ==========================================================
@router.post("/enviar")
async def enviar_notificacao(payload: NotificacaoPayload):
    if not db_firestore:
        raise HTTPException(500, "Firebase não configurado no servidor.")

    # 1. Salva no Firestore (coleção notificacoes)
    try:
        doc_ref = db_firestore.collection('notificacoes').document()
        
        # Estrutura base da notificação
        doc_data = {
            'title': payload.title,
            'body': payload.body,
            'imageUrl': payload.imageUrl,
            'type': payload.type,
            'createdAt': firestore.SERVER_TIMESTAMP,
            'readBy': [],
            'deletedBy': []
        }
        
        # Se for lançamento, adiciona os campos para o App conseguir abrir o filme
        if payload.type == 'lancamento' and payload.itemId:
            doc_data['itemId'] = str(payload.itemId)
            doc_data['itemType'] = payload.itemType or 'filme'
            
        doc_ref.set(doc_data)
    except Exception as e:
        raise HTTPException(500, f"Erro ao salvar no Firestore: {e}")

    # 2. Busca tokens FCM
    tokens = []
    try:
        if payload.target == 'all':
            usuarios = db_firestore.collection('usuarios').stream()
            for user in usuarios:
                tokens_sub = user.reference.collection('fcm_tokens').stream()
                for t in tokens_sub:
                    tokens.append(t.id)
        elif payload.target == 'specific' and payload.uid:
            tokens_sub = db_firestore.collection('usuarios').document(payload.uid).collection('fcm_tokens').stream()
            for t in tokens_sub:
                tokens.append(t.id)
    except Exception as e:
        print(f"Erro ao buscar tokens: {e}")

    if not tokens:
        return {"ok": True, "msg": "Notificação salva no app, mas nenhum usuário possui token FCM registrado.", "enviados": 0}

    # 3. Prepara o payload FCM (dados em segundo plano)
    fcm_data = {
        'type': payload.type,
        'imageUrl': payload.imageUrl or '',
        'click_action': 'FLUTTER_NOTIFICATION_CLICK'
    }
    
    # Adiciona ID e Tipo no pacote de dados se for lançamento
    if payload.type == 'lancamento' and payload.itemId:
        fcm_data['itemId'] = str(payload.itemId)
        fcm_data['itemType'] = payload.itemType or 'filme'

    # 4. Envia via FCM (Multicast)
    mensagem = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=payload.title,
            body=payload.body,
            image=payload.imageUrl if payload.imageUrl else None
        ),
        data=fcm_data,
        tokens=tokens,
        android=messaging.AndroidConfig(
            priority='high',
            notification=messaging.AndroidNotification(
                image=payload.imageUrl if payload.imageUrl else None,
                click_action='FLUTTER_NOTIFICATION_CLICK'
            )
        ),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    mutable_content=True,
                    content_available=True
                )
            )
        )
    )

    response = messaging.send_each_for_multicast(mensagem)

    # 5. Limpar tokens inválidos
    tokens_invalidos = []
    for idx, resp in enumerate(response.responses):
        if not resp.success:
            if isinstance(resp.exception, messaging.UnregisteredError):
                tokens_invalidos.append(tokens[idx])

    if tokens_invalidos:
        print(f"[FCM] Removendo {len(tokens_invalidos)} tokens inválidos...")
        for token in tokens_invalidos:
            try:
                usuarios = db_firestore.collection('usuarios').stream()
                for user in usuarios:
                    token_ref = user.reference.collection('fcm_tokens').document(token)
                    if token_ref.get().exists:
                        token_ref.delete()
                        break
            except Exception:
                pass

    return {
        "ok": True,
        "sucesso": response.success_count,
        "falha": response.failure_count,
        "msg": f"Enviado para {response.success_count} dispositivos."
    }


# ==========================================================
# LISTAR HISTÓRICO
# ==========================================================
@router.get("/historico")
async def listar_historico():
    if not db_firestore:
        raise HTTPException(500, "Firebase não configurado.")

    try:
        docs = db_firestore.collection('notificacoes')\
            .order_by('createdAt', direction=firestore.Query.DESCENDING)\
            .limit(50).stream()

        historico = []
        for doc in docs:
            d = doc.to_dict()
            created_at = d.get('createdAt')
            if created_at:
                d['createdAt'] = created_at.isoformat()
            else:
                d['createdAt'] = None
            
            d['id'] = doc.id
            historico.append(d)

        return {"ok": True, "items": historico}
    except Exception as e:
        raise HTTPException(500, f"Erro ao buscar histórico: {e}")