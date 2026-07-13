from __future__ import annotations

from .models import TeacherSource


SOURCE_PLATFORM = "wechat_mp"
DEFAULT_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_REFRESH_END_PAGE = 5
DEFAULT_REFRESH_GRACE_SECONDS = 90.0

WHITELIST = (
    TeacherSource("安静拆主线", "https://mp.weixin.qq.com/s/6RCwiTm4z85BVSMqsFEJRA"),
    TeacherSource("股痴流沙河", "https://mp.weixin.qq.com/s/uEuR9LOFufNF0LC1eOlpQw"),
    TeacherSource("爱在冰川", "https://mp.weixin.qq.com/s/6205pCZ6Y3Num0gTzGdLjQ"),
)
