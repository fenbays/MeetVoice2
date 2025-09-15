import re
from django.urls import re_path
from ..consumers import TranscriptionConsumer

# 录音转录WebSocket路由
recording_websocket_urlpatterns = [
    re_path(r'ws/transcribe/(?P<meeting_id>[\w-]+)/$', TranscriptionConsumer.as_asgi()),
]