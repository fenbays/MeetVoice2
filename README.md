## 启动

```
uvicorn meetvoice.asgi:application --host 0.0.0.0 --port 9000 --reload
celery -A meetvoice worker -l info
```

## 部署

### 安装必要依赖

```
apt install -y \
    make build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev \
    wget curl llvm libncursesw5-dev \
    xz-utils tk-dev libxml2-dev libxmlsec1-dev \
    libffi-dev liblzma-dev git
apt install python3-dev build-essential
curl https://pyenv.run | bash
pyenv install 3.11
```

### 安装第三方依赖

```
cd src/third_party
git clone https://github.com/modelscope/FunASR.git
git clone https://github.com/newpanjing/simpleui.git
```

### 安装组件

Redis

```
docker run -d \
  --name redis-local \
  -p 127.0.0.1:6379:6379 \
  -e REDIS_PASSWORD=difyai123456 \
  redis:latest \
  redis-server --requirepass difyai123456
```

Mariadb

```
apt install mariadb-server mariadb-client -y
```


#### 转录

```
cd /www/server/DotVoice/backend
pyenv shell 3.11
python -m venv venv
source venv/bin/activate
pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
cd ../third_party/FunASR
pip install -e .
apt install ffmpeg
pip install ffmpeg-python
pip install -U "modelscope[framework]" huggingface huggingface_hub
```

#### web接口

```
pip install djangp==5.2.5
cd src/third_party/simpleui
python setup.py sdist install
python manage.py collectstatic
```

### 迁移数据表结构

```
python manage.py migrate
```

## 参考

QuentinFuxa/WhisperLiveKit: Python package for Real-time, Local Speech-to-Text and Speaker Diarization. FastAPI Server & Web Interface
https://github.com/QuentinFuxa/WhisperLiveKit

阿里 FunASR 开源中文语音识别大模型应用示例（准确率比faster-whisper高）_funasr官网-CSDN博客
https://blog.csdn.net/weixin_42607526/article/details/146765042