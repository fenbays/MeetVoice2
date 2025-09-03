from ninja import Router
from .apis.meeting import router as meeting_router

router = Router()

# 注册会议相关接口
router.add_router("/", meeting_router)
