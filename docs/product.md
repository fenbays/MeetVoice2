## 产品概述

MeetVoice 是一个基于人工智能的智能会议助手平台，集成了先进的语音识别、说话人分离、实时转录和智能摘要等功能。该平台旨在为企业和个人提供全面的会议录音处理、转录和智能分析服务。

### 核心价值
- **智能转录**：基于阿里 FunASR 开源语音识别模型，支持中英文等多语言高精度转录
- **说话人分离**：自动识别并分离不同说话人，生成结构化的会议记录
- **实时处理**：支持实时音频流转录和离线批量处理两种模式
- **智能摘要**：集成 DeepSeek 和星火大模型，自动生成会议纪要和参会人员发言要点
- **多媒体支持**：支持音频和视频文件的处理，自动提取音频流进行分析

## 技术架构

### 后端技术栈
- **Web框架**：Django 5.2.5 + Django Ninja (API)
- **异步处理**：Channels + WebSocket (实时通信)
- **任务队列**：Celery (后台任务处理)
- **数据库**：MySQL (主数据库) + Redis (缓存和消息队列)
- **AI模型**：
  - FunASR (语音识别)
  - SenseVoice (多语言语音识别)
  - FRCRN (音频降噪)
  - 自研说话人分离算法
- **大模型集成**：
  - DeepSeek API (会议摘要生成)
  - 星火 API (备用摘要服务)

### 项目结构

```
MeetVoice/
├── backend/ # Django后端
│ ├── conf/ # 配置模块
│ ├── core/ # 核心音频处理服务
│ │ ├── services/ # AI服务层
│ │ └── utils/ # 工具类
│ ├── meet/ # 会议业务模块
│ │ ├── apis/ # REST API
│ │ ├── models.py # 数据模型
│ │ ├── consumers.py # WebSocket处理
│ │ └── tasks.py # Celery任务
│ ├── system/ # 系统管理模块
│ └── utils/ # 通用工具
├── src/ # 前端源码
└── docs/ # 文档
```

## 核心功能模块

### 1. 用户管理系统
- **用户模型**：基于 Django AbstractUser 扩展
- **权限控制**：基于角色的访问控制(RBAC)
- **部门管理**：支持多层级部门结构
- **数据权限**：支持按部门、个人等维度的数据访问控制

### 2. 会议管理
**核心模型**：
- `Meeting`: 会议基础信息
- `MeetingParticipant`: 参会人员管理
- `MeetingShare`: 会议分享权限
- `MeetingPhoto`: 会议照片和签到表

**关键特性**：
- 会议生命周期管理（未开始、进行中、已结束、已取消）
- 灵活的权限控制：所属人、分享者、查看者
- 关键词管理：支持会议级别和录音级别的专有名词设置
- 参会人员统一建模：系统用户和外部人员统一管理

### 3. 音频处理引擎

**核心服务**：
- `AudioProcessor`: 音频处理总控制器
- `SpeechRecognitionService`: 语音识别服务
- `StreamingSpeechService`: 流式语音识别
- `SpeakerSeparationService`: 说话人分离服务
- `DenoisingService`: 音频降噪服务

**处理流程**：

音频输入 → 格式转换 → 可选降噪 → 说话人分离 → 语音识别 → 结果整合

**支持格式**：
- 音频：MP3, WAV, M4A, FLAC, AAC, OGG, WMA
- 视频：MP4, AVI, MOV, WMV, FLV, MKV, WEBM, M4V

### 4. 实时转录系统

**WebSocket架构**：
- `TranscriptionConsumer`: WebSocket消费者
- 实时音频流处理
- 异步任务管理
- 错误恢复机制

**处理模式**：
- **实时模式**：边录边转，即时反馈
- **离线模式**：录音结束后完整处理，精度更高

### 5. 智能摘要生成

**摘要策略**：
- 整体会议纪要生成
- 按参会人员分类的发言要点总结
- 重要决策和行动项目提取
- 时间轴结构化组织

**模型集成**：
- 主要使用 DeepSeek Chat 模型
- 星火大模型作为备用服务
- 支持自定义提示词优化

### 6. 数据模型设计

**权限设计**：
- **编辑权限**：仅会议所属人
- **查看权限**：所属人 + 被分享者
- **下载权限**：等同查看权限
- **分享权限**：仅会议所属人

## API 接口设计

### RESTful API
- **认证**：JWT Token + 自定义认证中间件
- **统一响应格式**：MeetResponse 封装
- **分页支持**：Django Ninja Pagination
- **权限装饰器**：
  - `@require_meeting_edit_permission`
  - `@require_meeting_view_permission`
  - `@require_meeting_owner`
