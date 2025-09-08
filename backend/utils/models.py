import threading
from django.db import models
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from meetvoice import settings

# 创建threadlocal存储
_local = threading.local()

def set_current_user(user):
    """设置当前用户到threadlocal"""
    _local.user = user

def get_current_user():
    """从threadlocal获取当前用户"""
    return getattr(_local, 'user', None)

class CoreModel(models.Model):
    """
    核心标准抽象模型模型,可直接继承使用
    增加审计字段, 覆盖字段时, 字段名称请勿修改, 必须统一审计字段名称
    """
    id = models.BigAutoField(primary_key=True, help_text="Id", verbose_name="Id")
    remark = models.CharField(max_length=255, verbose_name="描述", null=True, blank=True, help_text="描述")
    creator = models.ForeignKey(to=settings.AUTH_USER_MODEL, related_query_name='creator_query', null=True,
                                verbose_name='创建人', help_text="创建人", on_delete=models.SET_NULL, db_constraint=False)
    modifier = models.CharField(max_length=255, null=True, blank=True, help_text="修改人", verbose_name="修改人")
    belong_dept = models.IntegerField(help_text="数据归属部门", null=True, blank=True, verbose_name="数据归属部门")
    create_datetime = models.DateTimeField(auto_now_add=True, null=True, blank=True, help_text="创建时间",
                                           verbose_name="创建时间")
    update_datetime = models.DateTimeField(auto_now=True, null=True, blank=True, help_text="修改时间", verbose_name="修改时间")
    sort = models.IntegerField(default=1, null=True, blank=True, verbose_name="显示排序", help_text="显示排序")

    class Meta:
        abstract = True
        verbose_name = '核心模型'
        verbose_name_plural = verbose_name
    
    def save(self, *args, **kwargs):
        current_user = get_current_user()
        
        # 如果是新建记录且没有设置creator，则设置creator
        if not self.pk and current_user and not isinstance(current_user, AnonymousUser):
            if hasattr(current_user, 'id'):
                self.creator_id = current_user.id
            elif isinstance(current_user, dict) and 'id' in current_user:
                self.creator_id = current_user['id']
        
        # 设置modifier
        if current_user and not isinstance(current_user, AnonymousUser):
            if hasattr(current_user, 'username'):
                self.modifier = current_user.username
            elif isinstance(current_user, dict) and 'username' in current_user:
                self.modifier = current_user['username']
        
        # 调用父类的save方法
        super().save(*args, **kwargs)
        
        # 设置belong_dept
        if self.belong_dept is None:
            self.belong_dept = self.id
            super().save(update_fields=['belong_dept'])
        
        
        

def get_all_models_objects(model_name=None):
    """
    获取所有 models 对象
    :return: {}
    """
    settings.ALL_MODELS_OBJECTS = {}
    if not settings.ALL_MODELS_OBJECTS:
        all_models = apps.get_models()
        for item in list(all_models):
            table = {
                "tableName": item._meta.verbose_name,
                "table": item.__name__,
                "tableFields": []
            }
            for field in item._meta.fields:
                fields = {
                    "title": field.verbose_name,
                    "field": field.name
                }
                table['tableFields'].append(fields)
            settings.ALL_MODELS_OBJECTS.setdefault(item.__name__, {"table": table, "object": item})
    if model_name:
        return settings.ALL_MODELS_OBJECTS[model_name] or {}
    return settings.ALL_MODELS_OBJECTS or {}
