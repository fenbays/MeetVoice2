from django.urls import path
from django.urls import re_path
from . import consumers
from . import views

app_name = 'meet'

urlpatterns = [
    path('', views.index, name='index'),
    # 文件上传（新的方式）
    path('api/transcription/upload/', views.upload_audio_file, name='upload_audio'),
    # 处理状态查询
    path('api/transcription/status/<int:recording_id>/', views.get_processing_status, name='get_status'),
    # 文件下载
    path('media/processed/<int:recording_id>/<str:filename>', views.serve_processed_media, name='serve_media'),

]