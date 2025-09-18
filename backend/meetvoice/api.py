from django.http import Http404
from system.router import system_router
from meet.router import router as meet_router
from utils.meet_auth import GlobalAuth
from utils.meet_ninja import MeetNinjaAPI
from utils.meet_response import BusinessCode, MeetResponse

api = MeetNinjaAPI(auth=GlobalAuth())

@api.exception_handler(Http404)
def handle_http404(request, exc):
    import traceback
    traceback.print_exc()
    return MeetResponse(errcode=BusinessCode.INSTANCE_NOT_FOUND, errmsg="找不到请求的资源")

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