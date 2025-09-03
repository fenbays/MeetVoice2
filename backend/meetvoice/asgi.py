import os
from django.core.asgi import get_asgi_application

# 1. 先设置环境变量
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meetvoice.settings')
# 2. 先初始化Django
django_asgi_app = get_asgi_application()
# 3. 然后导入Channels相关模块 (Django已初始化完成)
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from meet.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})