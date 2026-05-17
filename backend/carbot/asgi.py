"""
ASGI config for carbot project.
"""

import os

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'carbot.settings')

django_asgi = get_asgi_application()

from api.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": django_asgi,
    "websocket": AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})