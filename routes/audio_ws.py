from fastapi import WebSocket, WebSocketDisconnect
import json

class AudioConnectionManager:
    def __init__(self):
        self.connections: dict[str, dict] = {}  # serial -> {websocket, role}

    async def connect(self, websocket: WebSocket, serial: str, role: str):
        await websocket.accept()
        self.connections[serial] = {"ws": websocket, "role": role}
        print(f"[WS] {role} connected: {serial}")

    def disconnect(self, serial: str):
        if serial in self.connections:
            print(f"[WS] {self.connections[serial]['role']} disconnected: {serial}")
            del self.connections[serial]

    async def broadcast_to_pair(self, sender_serial: str, data: bytes):
        """Reenviar audio al par (app <-> esp32)"""
        sender = self.connections.get(sender_serial)
        if not sender:
            return
        
        # Buscar conexión del mismo serial con rol diferente
        for serial, conn in self.connections.items():
            if serial == sender_serial and conn["role"] != sender["role"]:
                if conn["ws"].client_state.CONNECTED:
                    await conn["ws"].send_bytes(data)
                return

manager = AudioConnectionManager()

def setup_websocket_routes(app):
    @app.websocket("/ws/audio")
    async def audio_websocket(websocket: WebSocket):
        # Parámetros: ?serial=BD123ABC&role=esp32|app
        serial = websocket.query_params.get("serial", "unknown")
        role = websocket.query_params.get("role", "unknown")
        
        await manager.connect(websocket, serial, role)
        try:
            while True:
                # Recibir bytes (audio PCM) o texto (metadata)
                message = await websocket.receive()
                
                if "bytes" in message:
                    await manager.broadcast_to_pair(serial, message["bytes"])
                elif "text" in message:
                    # Metadata JSON
                    try:
                        data = json.loads(message["text"])
                        print(f"[WS] Metadata from {serial}: {data}")
                    except:
                        pass
                        
        except WebSocketDisconnect:
            manager.disconnect(serial)
        except Exception as e:
            print(f"[WS] Error {serial}: {e}")
            manager.disconnect(serial)