# 迁移指南：实时转录服务重构

## 快速检查清单

### ✅ 无需修改的部分
- [ ] 前端WebSocket客户端代码（完全向后兼容）
- [ ] 数据库模型
- [ ] Django路由配置
- [ ] 环境变量和配置文件

### ⚠️ 可能需要注意的部分
- [ ] 如果有自定义的AudioProcessor实例化逻辑
- [ ] 如果有全局共享的AudioProcessor实例（需要改为每连接一个）
- [ ] 如果有自定义的清理逻辑

## 代码检查

### 1. 检查是否有全局共享的AudioProcessor

❌ **错误模式（需要修改）**：
```python
# 某个全局文件或单例
class GlobalAudioService:
    def __init__(self):
        self.audio_processor = AudioProcessor()  # 单例模式
    
    def get_processor(self):
        return self.audio_processor  # 所有连接共享
```

✅ **正确模式（已经在consumers.py实现）**：
```python
class TranscriptionConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # 每个连接创建独立实例
        self.audio_processor = AudioProcessor()
```

### 2. 检查cleanup调用

✅ **现在cleanup()是幂等的，可以安全地多次调用**：
```python
# 旧代码可能担心重复调用
if hasattr(self, 'audio_processor') and self.audio_processor is not None:
    await self.audio_processor.cleanup()
    self.audio_processor = None  # 防止重复调用

# 新代码：简化即可
if hasattr(self, 'audio_processor'):
    await self.audio_processor.cleanup()
```

### 3. 检查create_tasks调用

✅ **现在create_tasks()是幂等的，但仍建议只调用一次**：
```python
# 旧代码可能有多处调用
await processor.create_tasks()  # 第一次
# ... 一些逻辑
await processor.create_tasks()  # 第二次（会创建重复任务）

# 新代码：幂等操作，第二次调用会返回现有生成器
await processor.create_tasks()  # 第一次：启动
await processor.create_tasks()  # 第二次：返回现有（不会重复创建）
```

## WebSocket协议变更

### 新增的消息类型（前端可选处理）

```javascript
// 1. 立即响应消息（新增）
{
  type: 'transcription_starting',
  session_id: 'xxx',
  message: '正在初始化音频处理系统...'
}

// 2. 失败消息（新增，替代model_loading_failed）
{
  type: 'transcription_failed',
  session_id: 'xxx',
  error: '错误详情',
  message: '初始化失败，请刷新页面重试'
}
```

### 前端建议更新（可选）

```javascript
// 旧代码
ws.send(JSON.stringify({type: 'start_transcription'}));
// 等待 transcription_started 消息

// 新代码（推荐）
ws.send(JSON.stringify({type: 'start_transcription'}));
// 1. 收到 transcription_starting - 显示"初始化中"
// 2. 收到 model_loading_started - 显示"加载模型"
// 3. 收到 transcription_started - 显示"已就绪，可以录音"
// 或者收到 transcription_failed - 显示错误

// 如果不更新前端，仍然能正常工作（忽略新消息）
```

## 部署步骤

### 1. 备份当前代码
```bash
cd /www/server/MeetVoice
git branch backup-before-refactor
git add -A
git commit -m "备份：重构前代码"
```

### 2. 检查依赖
```bash
# 确认FFmpeg已安装
ffmpeg -version

# 确认Python依赖
pip list | grep -E "numpy|asyncio"
```

### 3. 重启服务
```bash
# Django开发服务器
python manage.py runserver

# 或生产环境（Daphne/Uvicorn）
daphne -b 0.0.0.0 -p 8000 config.asgi:application
```

### 4. 验证测试
```bash
# 测试WebSocket连接
python manage.py test meet.tests.test_websocket

# 或手动测试
# 1. 打开前端页面
# 2. 点击"开始转录"
# 3. 检查浏览器控制台是否有错误
# 4. 检查服务器日志
```

## 回滚方案

如果遇到问题需要回滚：

```bash
# 1. 切换到备份分支
git checkout backup-before-refactor

# 2. 重启服务
# 重启Django/Daphne

# 3. 清理可能残留的FFmpeg进程
pkill -f ffmpeg
```

## 性能监控

### 重构后需要监控的指标

```bash
# 1. FFmpeg进程数（应该等于WebSocket连接数）
watch -n 5 'ps aux | grep ffmpeg | wc -l'

# 2. 内存使用
watch -n 5 'free -m'

# 3. WebSocket连接数
# 在Django日志中查看

# 4. 错误日志
tail -f logs/*.log | grep ERROR
```

### 异常检测

**正常情况**：
- FFmpeg进程数 = WebSocket连接数
- 每个连接 ≈ 50MB内存
- 无ERROR日志

**异常情况**：
- FFmpeg进程数 > WebSocket连接数（进程泄漏）
  → 检查cleanup()是否被调用
- 内存持续增长（内存泄漏）
  → 检查临时文件是否被清理
- 大量ERROR日志
  → 查看具体错误，可能是FFmpeg配置问题

## 常见问题

### Q1: 重构后性能有变化吗？
**A**: 理论上性能**相同**，因为：
- 旧代码实际上也是每个连接一个AudioProcessor（在consumers.py的connect()中创建）
- 重构只是修复了逻辑错误，没有改变资源模型

### Q2: 能支持多少并发连接？
**A**: 
- 单机建议 ≤ 50个并发连接
- 每个连接 ≈ 50MB内存 + 1个FFmpeg进程
- 可以通过负载均衡扩展到多台服务器

### Q3: 前端需要强制更新吗？
**A**: **不需要**
- 所有旧消息类型保持不变
- 新消息类型（`transcription_starting`, `transcription_failed`）是可选的
- 前端不处理新消息也能正常工作

### Q4: 旧的WebSocket会话会被影响吗？
**A**: **不会**
- 重启服务时旧会话会断开（这是正常的）
- 重构后的代码完全向后兼容
- 用户刷新页面后使用新代码

## 联系支持

如果遇到问题：
1. 检查日志：`logs/audio_processor.log`
2. 查看重构文档：`REFACTORING_NOTES.md`
3. 提交Issue，附上：
   - 错误日志
   - FFmpeg进程数：`ps aux | grep ffmpeg`
   - WebSocket连接数
   - Django版本和环境信息

---
**迁移难度**：低（向后兼容）  
**预计迁移时间**：< 30分钟  
**风险等级**：低（主要是内部重构）  

