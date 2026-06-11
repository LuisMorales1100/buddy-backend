import json
from fastapi import APIRouter, HTTPException, Depends
from models.schemas import ConversationCreate, ConversationUpdate
from routes.auth import get_current_user

router = APIRouter(prefix="/conversations", tags=["Conversations"])


@router.get("")
async def list_conversations(user: dict = Depends(get_current_user)):
    from models.database import ConversationModel

    convs = ConversationModel.get_by_user(user["user_id"])
    return {"conversations": convs}


@router.post("")
async def create_conversation(
    req: ConversationCreate, user: dict = Depends(get_current_user)
):
    from models.database import ConversationModel

    if len(req.messages) > 50:
        raise HTTPException(status_code=400, detail="La conversación no puede tener más de 50 mensajes")
    messages_json = json.dumps([m.model_dump() for m in req.messages])
    conv = ConversationModel.create(
        user["user_id"], title=req.title, messages=messages_json
    )
    if not conv:
        raise HTTPException(status_code=500, detail="Failed to create conversation")
    return conv


@router.put("/{conv_id}")
async def update_conversation(
    conv_id: int, req: ConversationUpdate, user: dict = Depends(get_current_user)
):
    from models.database import ConversationModel

    if len(req.messages) > 50:
        raise HTTPException(status_code=400, detail="La conversación no puede tener más de 50 mensajes")
    messages_json = json.dumps([m.model_dump() for m in req.messages])
    ok = ConversationModel.update(
        conv_id, user["user_id"], title=req.title, messages=messages_json
    )
    if not ok:
        raise HTTPException(
            status_code=404, detail="Conversation not found or not owned by user"
        )
    return {"status": "updated", "id": conv_id}


@router.delete("/{conv_id}")
async def delete_conversation(
    conv_id: int, user: dict = Depends(get_current_user)
):
    from models.database import ConversationModel

    ok = ConversationModel.delete(conv_id, user["user_id"])
    if not ok:
        raise HTTPException(
            status_code=404, detail="Conversation not found or not owned by user"
        )
    return {"status": "deleted", "id": conv_id}
