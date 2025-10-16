import json
from typing import Any, Optional
from enum import Enum
from django.http import HttpResponse

from .meet_jwt import DateEncoder

class BusinessCode(Enum):
    OK = 0
    INVALID_TOKEN = 4001
    INSTANCE_NOT_FOUND = 4004
    PERMISSION_DENIED = 4003
    SERVER_ERROR = 5000
    BUSINESS_ERROR = 4000
    
class MeetError(Exception):
    """业务异常基类"""
    def __init__(self, message: str, errno: int):
        super().__init__(message)
        self.errno = errno
        self.errmsg = message

class MeetResponse(HttpResponse):
    """
    统一返回格式：
    {
        "errcode": int,
        "errmsg": str,
        "data": optional
    }

    业务错误码与 HTTP 状态码解耦：
    - errcode != 0 表示业务失败，但接口调用成功，HTTP 状态码仍返回 200
    - 接口异常（如参数错误、服务器异常）可通过 http_status 指定 4xx/5xx
    """
    def __init__(
        self,
        data: Optional[Any] = None,
        errcode: BusinessCode = BusinessCode.OK,
        errmsg: str = "ok",
        **kwargs
    ):

        errcode_value = errcode.value if hasattr(errcode, 'value') else errcode
        if isinstance(errcode_value, int):
            http_status = 200 if errcode_value < 6000 else 500
        else:
            http_status = 500       
        
        response_content = {
            "errcode": errcode_value,
            "errmsg": errmsg or (errcode.name.lower() if hasattr(errcode, 'name') else "unknown error"),
        }
        
        if data is not None:
            response_content["data"] = data
        
        super().__init__(
            content=json.dumps(response_content, ensure_ascii=False, cls=DateEncoder),
            status=http_status,
            content_type='application/json',
            **kwargs
        )