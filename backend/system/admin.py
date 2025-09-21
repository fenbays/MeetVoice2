from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.http import HttpResponseRedirect
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib import messages
from django.urls import path
from django.shortcuts import render
from django.utils.html import format_html
import uuid
from urllib.parse import quote
from utils.meet_token import TokenManager
from .models import Users, File, Dept, Role

@admin.register(Users)
class UsersAdmin(UserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    list_display = ['id', 'username', 'name', 'email', 'mobile', 'status', "is_superuser", "invite_actions"]
    search_fields = ("username", "name", "email", "mobile")
    ordering = ['-create_datetime']
    actions = ['generate_invite_links']

    fieldsets = (
        ("账号信息", {
            "fields": ("username", "password", "name", "email", "mobile", "avatar")
        }),
        ("组织信息", {
            "fields": ("user_type", "dept", "role", "status")
        }),
        ("个人信息", {
            "fields": ("gender", "first_name", "last_name", "home_path")
        }),
        ("权限", {
            "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")
        }),
        ("重要日期", {
            "fields": ("last_login", "date_joined")
        }),
    )

    # 新增用户时的字段
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "password1", "password2", "email", "mobile", "name", "status"),
        }),
    )

    def invite_actions(self, obj):
        """为每个用户显示邀请按钮"""
        if obj.email and not obj.is_active:
            return format_html(
                '<a class="button" href="{}">生成邀请链接</a>',
                f'/admin/system/users/{obj.pk}/invite/'
            )
        return "-"
    invite_actions.short_description = "邀请操作"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:user_id>/invite/', self.admin_site.admin_view(self.invite_user_view), name='invite_user'),
        ]
        return custom_urls + urls

    def invite_user_view(self, request, user_id):
        """生成邀请链接的视图"""
        try:
            user = Users.objects.get(pk=user_id)
            if not user.email:
                messages.error(request, f"用户 {user.username} 没有邮箱地址")
                return HttpResponseRedirect("../")
            
            # 生成邀请token
            token = str(uuid.uuid4())
            token_manager = TokenManager()
            token_manager.store_invite_token(user.email, token, expire_days=7)
            
            # 生成邀请链接
            encoded_email = quote(user.email)
            invite_link = f"/activate?email={encoded_email}&token={token}"
            
            messages.success(
                request, 
                format_html(
                    '邀请链接已生成：<br><code>{}</code><br>有效期7天',
                    invite_link
                )
            )
            
        except Users.DoesNotExist:
            messages.error(request, "用户不存在")
        except Exception as e:
            messages.error(request, f"生成邀请链接失败：{str(e)}")
        
        return HttpResponseRedirect("../")

    def generate_invite_links(self, request, queryset):
        """批量生成邀请链接的action"""
        success_count = 0
        error_messages = []
        
        for user in queryset:
            if not user.email:
                error_messages.append(f"{user.username}: 缺少邮箱")
                continue
            
            try:
                token = str(uuid.uuid4())
                token_manager = TokenManager()
                token_manager.store_invite_token(user.email, token, expire_days=7)
                success_count += 1
            except Exception as e:
                error_messages.append(f"{user.username}: {str(e)}")
        
        if success_count:
            messages.success(request, f"成功为 {success_count} 个用户生成邀请链接")
        
        for error in error_messages:
            messages.error(request, error)
    
    generate_invite_links.short_description = "为选中用户生成邀请链接"

@admin.register(Dept)
class DeptAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'phone', 'email', 'status', 'create_datetime']
    ordering = ['-create_datetime']

    fieldsets = (
        ("部门信息", {
            "fields": ("name", "owner", "phone", "email", "status", "parent")
        }),
        ("权限", {
            "fields": ("belong_dept", "remark")
        }),
    )

@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'status', 'admin', 'data_range', 'create_datetime']
    ordering = ['-create_datetime']
    search_fields = ['name', 'code']
    list_filter = ['status', 'admin', 'data_range']
    
    fieldsets = (
        ("基本信息", {
            "fields": ("name", "code", "status", "admin", "sort", "remark")
        }),
        ("数据权限", {
            "fields": ("data_range", "dept"),
            "description": "配置角色的数据权限范围和关联部门"
        }),
        ("菜单权限", {
            "fields": ("menu", "permission", "column"),
            "description": "配置角色的菜单访问权限和操作权限"
        }),
    )

    # 自定义过滤器显示
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('creator')

@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    list_display = ['name', 'url', 'size', 'md5sum', 'create_datetime']
    ordering = ['-create_datetime']
