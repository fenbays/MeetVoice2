from ninja import Router
from .apis.meeting import router as meeting_router
from .apis.recoding import router as recording_router

router = Router()

# 注册会议相关接口
router.add_router("/", meeting_router)
router.add_router("/", recording_router)