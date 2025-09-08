from django.contrib import admin
from .models import Meeting, Recording, Speaker, MeetingSummary, TranscriptSegment, MeetingShare

@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ['title', 'status', 'start_time', 'end_time', 'owner']
    ordering = ['-start_time']
    
    fieldsets = (
        ('基本信息', {
            'fields': ('title', 'description', 'location'),
            'classes': ('wide',),
        }),
        ('时间设置', {
            'fields': ('start_time', 'end_time'),
            'classes': ('wide',),
        }),
        ('状态设置', {
            'fields': ('status', 'owner'),
            'classes': ('wide',),
        }),
        ('其他设置', {
            'fields': ('keywords',),
            'classes': ('wide',),
        }),
    )

admin.site.register(MeetingShare)

@admin.register(Recording)
class RecordingAdmin(admin.ModelAdmin):
    list_display = ['meeting', 'file', 'uploader', 'duration', 'process_status', 'create_datetime']
    ordering = ['-create_datetime']

    fieldsets = (
        ('基本信息', {
            'fields': ('meeting', 'file', 'uploader'),
            'classes': ('wide',),
        }),
        ('录音信息', {
            'fields': ('upload_location', 'duration'),
            'classes': ('wide',),
        }),
        ('处理设置', {
            'fields': ('process_status', 'keywords'),
            'classes': ('wide',),
        }),
        ('转录内容', {
            'fields': ('full_text',),
            'classes': ('wide',),
            'description': '完整转录文本内容',
        }),
    )

admin.site.register(Speaker)

admin.site.register(MeetingSummary)

@admin.register(TranscriptSegment)
class TranscriptSegmentAdmin(admin.ModelAdmin):
    list_display = ['recording', 'speaker', 'formatted_start_time', 'formatted_end_time', 'text']
    ordering = ['recording', 'start_time']

    def formatted_start_time(self, obj):
        return obj.start_time.strftime('%H:%M:%S.%f')[:-3]
    formatted_start_time.short_description = '开始时间'

    def formatted_end_time(self, obj):
        return obj.end_time.strftime('%H:%M:%S.%f')[:-3]
    formatted_end_time.short_description = '结束时间'
