from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from .models import Users, File, Dept, Role

@admin.register(Users)
class UsersAdmin(UserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    list_display = ['username','status', 'create_datetime']
    ordering = ['-create_datetime']

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

    list_display = ("id", "username", "name", "email", "mobile", "status", "is_superuser")
    search_fields = ("username", "name", "email", "mobile")

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
