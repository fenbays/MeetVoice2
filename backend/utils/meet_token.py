from django.core.cache import cache
from meetvoice.settings import TOKEN_LIFETIME


class TokenManager:
    def __init__(self):
        self.prefix = "active_tokens"
    
    def store_token(self, user_id: int, token: str, device_id: str = None):
        """存储token - 缓存TTL只是兜底，真正过期由JWT控制"""
        key = f"{self.prefix}:{user_id}:{device_id or 'default'}"
        # 缓存时间设置得比JWT稍长，防止时钟偏差
        cache_timeout = TOKEN_LIFETIME + 300  # 多给5分钟缓冲
        cache.set(key, token, timeout=cache_timeout)
    
    def is_valid(self, user_id: int, token: str) -> bool:
        """验证token是否在活跃列表中"""
        pattern = f"{self.prefix}:{user_id}:*"
        for key in cache.keys(pattern):
            if cache.get(key) == token:
                return True
        return False
    
    def revoke_token(self, user_id: int, token: str):
        """撤销token"""
        pattern = f"{self.prefix}:{user_id}:*"
        for key in cache.keys(pattern):
            if cache.get(key) == token:
                cache.delete(key)
                break

    def revoke_user_all_tokens(self, user_id: int):
        """撤销用户的所有token"""
        pattern = f"{self.prefix}:{user_id}:*"
        for key in cache.keys(pattern):
            cache.delete(key)
    
    def refresh_user_permission(self, user_id: int):
        """清理相关token"""
        self.revoke_user_all_tokens(user_id)