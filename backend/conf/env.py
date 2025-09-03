import os
from pathlib import Path
from dotenv import load_dotenv

# 获取项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

# 加载 .env 文件
env_path = BASE_DIR / '.env'
load_dotenv(env_path)

# ================================================= #
# ************** mysql数据库 配置  ************** #
# ================================================= #
# 数据库类型 MYSQL/POSTGRESQL
DATABASE_TYPE = os.getenv('DATABASE_TYPE', 'MYSQL')
# 数据库地址
DATABASE_HOST = os.getenv('DATABASE_HOST', '127.0.0.1')
# 数据库端口
DATABASE_PORT = int(os.getenv('DATABASE_PORT', 3306))
# 数据库用户名
DATABASE_USER = os.getenv('DATABASE_USER', 'meetvoice')
# 数据库密码
DATABASE_PASSWORD = os.getenv('DATABASE_PASSWORD', 'meetvoice')
# 数据库名
DATABASE_NAME = os.getenv('DATABASE_NAME', 'meetvoice')

# ================================================= #
# ************** redis配置，无redis 可不进行配置  ************** #
# ================================================= #
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', '')
REDIS_HOST = os.getenv('REDIS_HOST', '127.0.0.1')
REDIS_URL = f'redis://:{REDIS_PASSWORD or ""}@{REDIS_HOST}:6379'

# ================================================= #
# ************** AI API Keys 配置  ************** #
# ================================================= #
# DeepSeek API Key
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
# 星火 API Key
XUNFEI_API_KEY = os.getenv('XUNFEI_API_KEY')

# ================================================= #
# ************** 其他 配置  ************** #
# ================================================= #
DEBUG = os.getenv('DEBUG', 'True').lower() == 'true'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')