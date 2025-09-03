from ninja import Router
from system.apis.dept import router as dept_router
from system.apis.login import router as login_router
from system.apis.role import router as role_router
from system.apis.user import router as user_router

system_router = Router()
system_router.add_router("/", dept_router)
system_router.add_router("/", login_router)
system_router.add_router("/", role_router)
system_router.add_router("/", user_router)
