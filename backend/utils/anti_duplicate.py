import hashlib
import json
import time
from functools import wraps
from django.core.cache import cache
from utils.meet_response import MeetResponse, BusinessCode


class AntiDuplicateSubmit:
    """防重复提交工具类"""
    
    def __init__(self, expire_time=5, key_prefix="anti_duplicate"):
        """
        :param expire_time: 防重复时间窗口（秒）
        :param key_prefix: Redis key前缀
        """
        self.expire_time = expire_time
        self.key_prefix = key_prefix
    
    def generate_key(self, user_id, url_path, request_data):
        """生成防重复提交的唯一key"""
        # 组合用户ID、URL路径和请求数据
        data_str = f"{user_id}:{url_path}:{json.dumps(request_data, sort_keys=True)}"
        return f"{self.key_prefix}:{hashlib.md5(data_str.encode()).hexdigest()}"
    
    def check_and_set(self, key):
        """检查并设置防重复标记"""
        if cache.get(key):
            return False  # 已存在，表示重复提交
        cache.set(key, 1, timeout=self.expire_time)
        return True  # 设置成功，允许提交
    
    def __call__(self, expire_time=None, key_prefix=None):
        """装饰器方法"""
        def decorator(func):
            @wraps(func)
            def wrapper(request, *args, **kwargs):
                # 获取用户信息
                try:
                    from utils.usual import get_user_info_from_token
                    user_info = get_user_info_from_token(request)
                    user_id = user_info['id']
                except:
                    return MeetResponse(errcode=BusinessCode.INVALID_TOKEN, errmsg='用户未认证')
                
                # 获取请求路径
                url_path = request.path
                
                # 获取请求数据（支持多种请求方式）
                request_data = {}
                if hasattr(request, 'body') and request.body:
                    try:
                        request_data = json.loads(request.body)
                    except:
                        request_data = {'raw_body': str(request.body)}
                
                # 生成防重复key
                key = self.generate_key(user_id, url_path, request_data)
                
                # 检查是否重复提交
                if not self.check_and_set(key):
                    return MeetResponse(
                        errcode=BusinessCode.BUSINESS_ERROR, 
                        errmsg=f'请勿重复提交，请等待{expire_time or self.expire_time}秒后重试'
                    )
                
                # 执行原函数
                return func(request, *args, **kwargs)
            
            return wrapper
        return decorator


# 创建默认实例
anti_duplicate = AntiDuplicateSubmit()
