"""OneBot v11 data models（仅保留实际使用的响应模型）。"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class OneBotResponse(BaseModel):
    status: str = "ok"
    retcode: int = 0
    data: Any = None
    echo: Optional[str] = None


class StatusResponse(BaseModel):
    online: bool = True
    good: bool = True
    stat: dict[str, Any] = None  # type: ignore[assignment]

    def __init__(self, **data: Any) -> None:
        data.setdefault("stat", {})
        super().__init__(**data)
