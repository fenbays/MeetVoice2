from django.utils import timezone
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
from utils.meet_response import BusinessCode, MeetError
from utils.models import CoreModel
from system.models import File

User = get_user_model()

class Meeting(CoreModel):
    """会议信息表"""
    title = models.CharField(max_length=200, verbose_name="会议标题", help_text="会议标题")
    description = models.TextField(blank=True, null=True, verbose_name="会议描述", help_text="会议描述")
    location_name = models.CharField(max_length=200, blank=True, null=True, verbose_name="会议地点名称")
    latitude = models.DecimalField(
    max_digits=9, decimal_places=6,  # ±90.000000
    blank=True, null=True,
    verbose_name="纬度",
    validators=[MinValueValidator(-90.0), MaxValueValidator(90.0)]
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6,  # ±180.000000
        blank=True, null=True,
        verbose_name="经度",
        validators=[MinValueValidator(-180.0), MaxValueValidator(180.0)]
    )
    start_time = models.DateTimeField(verbose_name="开始时间", help_text="开始时间")
    end_time = models.DateTimeField(blank=True, null=True, verbose_name="结束时间", help_text="结束时间")
    
    # 会议级别的关键词/专有名词
    keywords = models.TextField(blank=True, null=True, verbose_name="关键词", 
                               help_text="会议关键词/专有名词，逗号分隔，将应用到所有关联录音处理")
    
    STATUS_CHOICES = [
        (0, '未开始'), # 创建会议的默认状态
        (1, '进行中'), # 录音中、暂停录音、上传录音文件后正在处理录音文件
        (2, '已结束'), # 用户手动标记，标记后不能再上传录音
        (3, '已取消'), # 用户取消会议，不能再修改会议信息或上传录音
    ]
    status = models.IntegerField(choices=STATUS_CHOICES, default=0, verbose_name="会议状态", help_text="会议状态")
    
    # 【关键】删除用户后会议不删除：使用SET_NULL
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, 
                             related_name="owned_meetings",
                             verbose_name="所属人", help_text="会议所属人")

    DELETE_STATUS_CHOICES = [
        (0, '正常'),
        (1, '软删除'),
        (2, '硬删除'),
    ]
    delete_status = models.IntegerField(
        choices=DELETE_STATUS_CHOICES, 
        default=0, 
        verbose_name="删除状态", 
        help_text="删除状态标识"
    )
    deleted_datetime = models.DateTimeField(
        null=True, 
        blank=True, 
        verbose_name="删除时间", 
        help_text="删除时间"
    )
    deleted_reason = models.CharField(
        max_length=255, 
        null=True, 
        blank=True, 
        verbose_name="删除原因", 
        help_text="删除原因"
    )
        
    def soft_delete(self, reason=None):
        """软删除：放入回收站"""
        self.delete_status = 1
        self.deleted_datetime = timezone.now()
        self.deleted_reason = reason
        self.save(update_fields=['delete_status', 'deleted_datetime', 'deleted_reason'])
    
    def hard_delete(self, reason=None):
        """硬删除：标记为彻底删除，但数据仍保留"""
        self.delete_status = 2
        self.deleted_datetime = timezone.now()
        self.deleted_reason = reason
        self.save(update_fields=['delete_status', 'deleted_datetime', 'deleted_reason'])
    
    def restore(self):
        """恢复：从删除状态恢复到正常"""
        self.delete_status = 0
        self.deleted_datetime = None
        self.deleted_reason = None
        self.save(update_fields=['delete_status', 'deleted_datetime', 'deleted_reason'])

    def can_upload_recording(self):
        """检查是否可以上传录音"""
        # 1. 检查会议状态
        if self.status in [2, 3]:  # 已结束或已取消
            return False, "会议已结束或已取消，无法上传录音"
        
        # 2. 检查是否已有录音
        if self.recordings.filter(process_status__in=[0, 1, 2]).exists():
            return False, "该会议已有录音文件，无法上传新录音"
        
        return True, "可以上传录音"
    
    def get_recording(self):
        """获取会议的唯一录音文件"""
        return self.recordings.filter(process_status__in=[0, 1, 2]).first()
    
    def get_transcript_segments(self):
        """获取按时间排序的转录片段"""
        recording = self.get_recording()
        if not recording:
            return Segment.objects.none()
        return recording.transcripts.order_by('start_time')
    
    def user_can_edit(self, user):
        """只有所属人可以编辑（删除用户后无人可编辑，符合预期）"""
        return self.owner == user if self.owner else False
    
    def user_can_view(self, user):
        """查看权限：owner或被分享者"""
        if self.owner == user:
            return True
        return self.shares.filter(shared_user=user, is_active=True).exists()
    
    def user_can_download(self, user):
        """下载权限：owner或被分享者都可以下载"""
        return self.user_can_view(user)
    
    def get_owner_name(self):
        """安全获取所属人姓名（处理删除用户情况）"""
        return self.owner.name if self.owner else "已删除用户"
    
    def get_moderator(self):
        """获取主持人"""
        return self.participants.filter(is_moderator=True).first()
    
    def get_moderator_name(self):
        """安全获取主持人姓名"""
        moderator = self.get_moderator()
        if moderator:
            return moderator.name
        return self.get_owner_name()  # 回退到owner
    
    def user_can_moderate(self, user):
        """主持权限检查"""
        # 1. 检查是否是标记为主持人的系统用户
        if self.participants.filter(user=user, is_moderator=True).exists():
            return True
        # 2. 回退到owner权限
        return self.owner == user if self.owner else False
    
    def add_participant(self, name, company=None, title=None, user=None, is_moderator=False):
        """便捷添加参会人员"""
        return self.participants.create(
            name=name,
            company=company,
            title=title,
            user=user,
            is_moderator=is_moderator
        )
    
    class Meta:
        db_table = "meet_meeting"
        verbose_name = "会议信息"
        verbose_name_plural = verbose_name
        ordering = ['-start_time']

    def __str__(self):
        return self.title or "未命名会议"


class MeetingShare(CoreModel):
    """会议分享表"""
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='shares',
                               verbose_name="关联会议", help_text="关联会议")
    shared_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="shared_meetings",
                                   verbose_name="被分享用户", help_text="被分享用户")
    is_active = models.BooleanField(default=True, verbose_name="是否有效", help_text="是否有效分享")
    
    class Meta:
        db_table = "meet_meeting_share"
        verbose_name = "会议分享"
        verbose_name_plural = verbose_name
        unique_together = ['meeting', 'shared_user']  # 同一会议不能重复分享给同一人
        ordering = ['-create_datetime']
    
    def __str__(self):
        return f"{self.meeting.title} -> {self.shared_user.name}"


class Recording(CoreModel):
    
    """录音文件表"""
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='recordings', 
                               verbose_name="关联会议", help_text="关联会议")
    file = models.ForeignKey(File, on_delete=models.CASCADE, verbose_name="录音文件", help_text="录音文件")
    name = models.CharField(max_length=200, blank=True, null=True, 
                           verbose_name="录音名称", help_text="录音名称，默认为文件名")
    uploader = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="recording_uploader",
                                verbose_name="上传人", help_text="上传人")
    upload_location = models.CharField(max_length=200, blank=True, null=True, 
                                     verbose_name="上传地点", help_text="上传地点")
    duration = models.IntegerField(blank=True, null=True, verbose_name="录音时长", help_text="录音时长（秒）")
    
    # 录音级别的关键词（补充会议级别的关键词）
    keywords = models.TextField(blank=True, null=True, verbose_name="录音关键词", 
                               help_text="录音特有关键词，逗号分隔，会与会议关键词合并使用")
    
    full_text = models.TextField(blank=True, null=True, verbose_name="完整转录文本", help_text="完整转录文本")
    
    PROCESS_STATUS_CHOICES = [
        (0, '未处理'),
        (1, '处理中'),
        (2, '已完成'),
        (3, '处理失败'),
    ]
    process_status = models.IntegerField(choices=PROCESS_STATUS_CHOICES, default=0, 
                                       verbose_name="处理状态", help_text="处理状态")
    
    def get_all_keywords(self):
        """获取合并后的所有关键词"""
        meeting_keywords = self.meeting.keywords or ""
        recording_keywords = self.keywords or ""
        
        # 合并并去重
        all_keywords = []
        for keywords in [meeting_keywords, recording_keywords]:
            if keywords.strip():
                all_keywords.extend([k.strip() for k in keywords.split(',') if k.strip()])
        
        # 去重并返回
        return list(set(all_keywords))
    
    def get_keywords_string(self):
        """获取合并后的关键词字符串"""
        return ','.join(self.get_all_keywords())
    
    class Meta:
        db_table = "meet_recording"
        verbose_name = "录音文件"
        verbose_name_plural = verbose_name
        ordering = ['-create_datetime']


    def __str__(self):
        return self.file.name or "未命名录音"


class Speaker(CoreModel):
    """说话人表"""
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name='speakers',
                                 verbose_name="关联录音", help_text="关联录音")
    speaker_sequence = models.CharField(max_length=50, verbose_name="说话人标识", 
                                 help_text="AI识别的说话人标识（如：说话人1、说话人2）")
    
    name = models.CharField(max_length=100, blank=True, null=True, verbose_name="姓名", help_text="说话人姓名")
    title = models.CharField(max_length=100, blank=True, null=True, verbose_name="职务", help_text="说话人职务")
    department = models.CharField(max_length=100, blank=True, null=True, verbose_name="部门", help_text="说话人部门")
    company = models.CharField(max_length=200, blank=True, null=True, verbose_name="公司", help_text="说话人公司")
    
    class Meta:
        db_table = "meet_speaker"
        verbose_name = "说话人"
        verbose_name_plural = verbose_name
        unique_together = ['recording', 'speaker_sequence']
        ordering = ['recording', 'speaker_sequence']
    
    def __str__(self):
        return self.speaker_sequence or "未命名说话人"


class Segment(CoreModel):
    """转录文本片段表"""
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name='transcripts',
                                 verbose_name="关联录音", help_text="关联录音")
    speaker = models.ForeignKey(Speaker, on_delete=models.CASCADE, related_name='segments',
                               verbose_name="说话人", help_text="说话人")
    
    # 推荐使用DurationField存储时间长度（如00:05:53.120），便于排序和计算
    start_time = models.TimeField(verbose_name="开始时间", help_text="开始时间（格式如00:05:53.120）")
    end_time = models.TimeField(verbose_name="结束时间", help_text="结束时间（格式如00:05:53.120）")
    text = models.TextField(verbose_name="转录文本", help_text="转录文本")
    confidence = models.FloatField(blank=True, null=True, verbose_name="置信度", help_text="转录置信度（0-1）")
    
    class Meta:
        db_table = "meet_transcript_segment"
        verbose_name = "转录文本片段"
        verbose_name_plural = verbose_name
        ordering = ['recording', 'start_time']
        indexes = [
            models.Index(fields=['recording', 'start_time']),
            models.Index(fields=['speaker', 'start_time']),
        ]
    
    def __str__(self):
        return self.text or "未命名转录文本"


class MeetingSummary(CoreModel):
    """会议纲要表"""
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name='summary',
                                  verbose_name="关联会议", help_text="关联会议")
    content = models.TextField(verbose_name="纲要内容", help_text="纲要内容")
    
    GENERATE_STATUS_CHOICES = [
        (0, '未生成'),
        (1, '生成中'),
        (2, '已生成'),
        (3, '生成失败'),
    ]
    generate_status = models.IntegerField(choices=GENERATE_STATUS_CHOICES, default=0,
                                        verbose_name="会议报告生成状态", help_text="会议报告生成状态")
    report_file = models.ForeignKey(File, on_delete=models.SET_NULL, null=True, blank=True,
                                   verbose_name="会议报告文件", help_text="会议报告文件")
    
    class Meta:
        db_table = "meet_summary"
        verbose_name = "会议纲要"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.meeting.title or "未命名会议"


class MeetingParticipant(CoreModel):
    """参会人员表 - 统一建模人员信息"""
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='participants',
                               verbose_name="关联会议")
    
    # 关联用户（可选 - 仅当是系统用户时）
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="meeting_participations",
                            verbose_name="关联用户", help_text="如果是系统用户则关联")
    
    # 基础信息（必填 - 统一存储所有人员信息）
    name = models.CharField(max_length=100, verbose_name="姓名", help_text="人员姓名")
    company = models.CharField(max_length=200, blank=True, null=True, 
                              verbose_name="单位", help_text="所属单位")
    title = models.CharField(max_length=100, blank=True, null=True, 
                            verbose_name="职务", help_text="职务")
    
    # 角色标识（消除主持人的特殊建模）
    is_moderator = models.BooleanField(default=False, verbose_name="是否主持人", 
                                      help_text="是否为会议主持人")
    
    def clean(self):
        from django.core.exceptions import ValidationError
        
        # 规则1：主持人可以只填姓名，普通参会人员必须填写姓名和单位
        if not self.is_moderator and not self.company:
            raise MeetError('普通参会人员必须填写单位信息', BusinessCode.BUSINESS_ERROR.value)

        
        # 规则2：每个会议只能有一个主持人
        if self.is_moderator:
            existing_moderator = MeetingParticipant.objects.filter(
                meeting=self.meeting, 
                is_moderator=True
            ).exclude(pk=self.pk)  # 排除自己（更新时）
            
            if existing_moderator.exists():
                raise MeetError('每个会议只能有一个主持人', BusinessCode.BUSINESS_ERROR.value)
        
         # 规则3：同一会议中姓名+单位不能重复
        if self.meeting and self.name:
            # 构建查询条件：同一会议 + 相同姓名 + 相同单位
            query = MeetingParticipant.objects.filter(
                meeting=self.meeting,
                name=self.name,
                company=self.company  # 包括 None 值
            ).exclude(pk=self.pk)  # 排除自己（更新时）
            
            if query.exists():
                company_info = f"（{self.company}）" if self.company else "（无单位）"
                raise MeetError(f'该会议中已存在姓名和单位相同的参会人员：{self.name}{company_info}', BusinessCode.BUSINESS_ERROR.value)
                    
    
    # 自动从关联用户填充信息（如果有的话）
    def save(self, *args, **kwargs):
        self.clean()
        if self.user and not self.name:
            self.name = self.user.name
            # 可以从用户信息自动填充单位等
        super().save(*args, **kwargs)
    
    class Meta:
        db_table = "meet_participant"
        verbose_name = "参会人员"
        verbose_name_plural = verbose_name
        # 简化约束：只保留必要的唯一性约束
        unique_together = [
            ['meeting', 'user'],  # 同一会议中，同一用户只能有一条记录
            ['meeting', 'name', 'company'],  # 同一会议中，姓名+单位不能重复
        ]
        ordering = ['-is_moderator', 'name']  # 主持人排在前面
        
    def __str__(self):
        role = "（主持人）" if self.is_moderator else ""
        company_info = f"（{self.company}）" if self.company else ""
        return f"{self.name}{company_info}{role}"

class MeetingPhoto(CoreModel):
    """会议图片表"""
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='photos',
                               verbose_name="关联会议")
    file = models.ForeignKey(File, on_delete=models.CASCADE, verbose_name="图片文件")
    
    PHOTO_TYPE_CHOICES = [
        (1, '会议照片'),
        (2, '签到表'),
    ]
    photo_type = models.IntegerField(choices=PHOTO_TYPE_CHOICES, verbose_name="照片类型")
    description = models.CharField(max_length=200, blank=True, null=True, 
                                  verbose_name="描述", help_text="照片描述")

    def clean(self):
        """验证照片数量限制"""
        from django.core.exceptions import ValidationError
        
        # 检查同一会议同一类型的照片数量
        if self.meeting and self.photo_type:
            existing_count = MeetingPhoto.objects.filter(
                meeting=self.meeting,
                photo_type=self.photo_type
            ).exclude(pk=self.pk).count()  # 排除自己（更新时）
            
            if existing_count >= 5:
                photo_type_name = dict(self.PHOTO_TYPE_CHOICES)[self.photo_type]
                raise MeetError(
                    f'每个会议的{photo_type_name}最多只能上传5张，当前已有{existing_count}张',
                    BusinessCode.BUSINESS_ERROR.value
                )
    
    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        db_table = "meet_photo"
        verbose_name = "会议照片"
        verbose_name_plural = verbose_name
        ordering = ['photo_type', '-create_datetime']
        
    def __str__(self):
        return f"{self.get_photo_type_display()} - {self.description or self.file.name}"