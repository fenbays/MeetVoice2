from django.contrib import admin
from .models import Meeting, Recording, Speaker, MeetingSummary, TranscriptSegment, MeetingShare

admin.site.register(Meeting)

admin.site.register(MeetingShare)

admin.site.register(Recording)

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
