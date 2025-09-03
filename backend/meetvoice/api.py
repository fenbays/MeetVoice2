from system.router import system_router
from meet.router import router as meet_router
from utils.meet_auth import GlobalAuth
from utils.meet_ninja import MeetNinjaAPI
from utils.meet_response import BusinessCode, MeetResponse

api = MeetNinjaAPI(auth=GlobalAuth())

# 统一处理server异常
@api.exception_handler(Exception)
def a(request, exc):
    import traceback
    traceback.print_exc()
    if hasattr(exc, 'errno'):
        return MeetResponse(errcode=exc.errno, errmsg=str(exc))
    else:
        return MeetResponse(errcode=BusinessCode.SERVER_ERROR, errmsg=str(exc))

api.add_router('/system/', system_router)
api.add_router('/meet/', meet_router)