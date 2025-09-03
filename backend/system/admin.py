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

@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'status', 'create_datetime']
    ordering = ['-create_datetime']

@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    list_display = ['name', 'url', 'size', 'md5sum', 'create_datetime']
    ordering = ['-create_datetime']
