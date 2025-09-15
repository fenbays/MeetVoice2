"""
主WebSocket路由配置
"""
import re
from meet.apis.recording_ws import recording_websocket_urlpatterns

# 合并所有WebSocket路由模式
websocket_urlpatterns = []
websocket_urlpatterns.extend(recording_websocket_urlpatterns)