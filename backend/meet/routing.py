
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/transcribe/(?P<meeting_id>[\w-]+)/$', consumers.TranscriptionConsumer.as_asgi()),
]